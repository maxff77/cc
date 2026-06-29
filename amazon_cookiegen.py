#!/usr/bin/env python3
"""Server-side Amazon login -> cookie header. One credential in (stdin), one JSON line out.

Engine-agnostic: AMZ_LOGIN_ENGINE=camoufox (default, anti-detect) | chromium (alternate).
Both reuse the existing engines as-is:
  - camoufox path -> amazon_signup.py (Camoufox launch + warmup + CAPTCHA detectors)
  - chromium path -> amazon_login.py  (real Chrome + webdriver patch)
  - shared        -> amazon_login._submit_login / _skip_nags / cookies_to_header

Ephemeral per run (no shared profile), headless, direct VPS IP (proxy reserved). On
CAPTCHA/anomaly it DETECTS and returns a typed reason -- it never waits for a human.

This is the worker-callable core (Spec 1). The queue/API/UI subprocess it.

Run:
    echo '{"email":"a@b.com","password":"pw"}' | AMZ_LOGIN_ENGINE=camoufox python amazon_cookiegen.py
    echo '{"email":"a@b.com","password":"pw"}' | AMZ_LOGIN_ENGINE=chromium python amazon_cookiegen.py
    python amazon_cookiegen.py --selftest     # parsers + imports, no browser

stdout: {"ok":true,"cookie":"name=value;..."} | {"ok":false,"reason":"..."}
exit:   0 ok | 2 captcha | 3 login_failed | 4 timeout | 5 bad_proxy(reserved) | 6 bad_input | 7 engine_error
"""
import json
import os
import sys
import time

import amazon_login as al    # _submit_login, _skip_nags, cookies_to_header, _human_* (Chromium + shared)
import amazon_signup as sg   # launch_browser (Camoufox), _continue_shopping, _cvf_error, _warmup*

ADDRESSES_URL = "https://www.amazon.com/a/addresses"
SIGNOUT_URL = "https://www.amazon.com/gp/sign-out.html"
HOME_URL = "https://www.amazon.com"

# A logged-in Amazon session carries these auth cookies; logged-out has only
# session-id/session-token/ubid-main. Presence of any = login succeeded.
_AUTH_COOKIES = {"at-main", "sess-at-main", "x-main"}

REASON_EXIT = {
    "captcha": 2,
    "login_failed": 3,
    "timeout": 4,
    "bad_proxy": 5,      # reserved for when AMZ_LOGIN_PROXY lands
    "bad_input": 6,
    "engine_error": 7,
}

# Playwright's Firefox driver crashes (coreBundle.js -> pageError.location.url) on any page
# error with no 'location' -> the node driver dies and the Python call hangs (not raises).
# amazon_signup.PAGEERROR_SHIELD deliberately EXCLUDES amazon.com; our login goes straight to
# amazon and hits exactly that crash. Shield every host: mark uncaught errors/rejections handled
# so Firefox never emits the uncaught event the driver chokes on. A real auth still sets its
# cookies; only page-script noise is muted.
_PAGEERROR_SHIELD_ALL = (
    "(() => {"
    "window.addEventListener('unhandledrejection', e => e.preventDefault());"
    "window.onerror = () => true;"
    "})();"
)


def _parse_creds(raw: str):
    """stdin JSON -> (email, password). Raises on anything malformed (caller -> bad_input)."""
    data = json.loads(raw)
    email, password = data["email"], data["password"]
    if not (isinstance(email, str) and isinstance(password, str) and email.strip() and password):
        raise ValueError("empty or non-string credential")
    return email.strip(), password


def _safe_count(page, selector: str) -> bool:
    try:
        return bool(page.locator(selector).count())
    except Exception:
        return False


def _logged_in(ctx) -> bool:
    try:
        names = {c["name"] for c in ctx.cookies(HOME_URL)}
    except Exception:
        return False
    return bool(names & _AUTH_COOKIES)


def _bad_signal(page):
    """Read-only check for a stuck state. Returns 'captcha' or None (never clicks)."""
    url = page.url
    if "/ap/cvf" in url or "/ap/mfa" in url:
        return "captcha"
    if _safe_count(page, "#auth-captcha-image, input#captchacharacters, "
                         "form[action*='captcha' i], #auth-mfa-otpcode"):
        return "captcha"
    try:
        if sg._cvf_error(page):   # 500 'went wrong on our end' on /ap/cvf|/ap/signin = bad IP
            return "captcha"
    except Exception:
        pass
    return None


def _login_and_capture(page, ctx, email: str, password: str) -> dict:
    """Drive login on an already-launched page, settle, and capture the cookie header.
    Engine-agnostic: works on Chromium or Camoufox (plain Playwright calls)."""
    page.goto(ADDRESSES_URL, wait_until="domcontentloaded")
    if "signin" in page.url or _safe_count(page, "#ap_email, #ap_email_login"):
        try:
            al._submit_login(page, email, password)
        except Exception:
            # Existing/foreign session: sign out and retry once with these creds.
            try:
                page.goto(SIGNOUT_URL, wait_until="domcontentloaded")
                page.goto(ADDRESSES_URL, wait_until="domcontentloaded")
                al._submit_login(page, email, password)
            except Exception:
                pass

    # Bounded settle (~60s): wait for the auth cookie, bailing on any bad signal.
    # ponytail: page.wait_for_timeout (Playwright clock) keeps it deterministic + cancel-safe.
    bad = None
    deadline = time.monotonic() + 45          # wall-clock cap: detector calls make a pass cost >1s
    while time.monotonic() < deadline:
        if _logged_in(ctx):
            break
        bad = _bad_signal(page)
        if bad:
            break
        try:
            if sg._continue_shopping(page):   # anti-bot wall = degraded IP
                bad = "captcha"
                break
        except Exception:
            pass
        try:
            al._skip_nags(page)               # phone/passkey/country interstitials
        except Exception:
            pass
        page.wait_for_timeout(1500)

    if _logged_in(ctx):
        return {"ok": True, "cookie": al.cookies_to_header(ctx.cookies(HOME_URL))}
    if bad:
        return {"ok": False, "reason": bad}
    if "/ap/signin" in page.url:
        return {"ok": False, "reason": "login_failed"}
    return {"ok": False, "reason": "timeout"}


def _run_camoufox(email: str, password: str, headless: bool, warmup: str) -> dict:
    """Default engine. Camoufox is ephemeral by design (no user_data_dir) -> each run a
    fresh, isolated fingerprint. Direct IP (proxy=None, geoip off)."""
    with sg.launch_browser(None, headless, None) as browser:
        page = browser.new_page()
        page.set_default_navigation_timeout(20000)
        page.set_default_timeout(15000)
        try:
            sg._prime_viewport(page)          # cache viewport before any move (avoids cull-hang)
        except Exception:
            pass
        neuter = getattr(sg, "WEBAUTHN_NEUTER", None)
        if neuter:
            try:
                page.context.add_init_script(neuter)   # auto-cancel passkey -> falls back to password
            except Exception:
                pass
        try:
            page.context.add_init_script(_PAGEERROR_SHIELD_ALL)   # keep the FF driver alive on amazon.com
        except Exception:
            pass
        try:
            sg._install_saver_routes(page.context, {"images": False})  # block trackers/media, keep images
        except Exception:
            pass
        if warmup != "off":                   # ponytail: warmup only on the camoufox path (its machinery)
            os.environ["AMZ_WARMUP"] = warmup
            prof = sg._warmup_profile()
            if prof.get("mode") != "off":
                sg._warmup_web(page, prof)
                if sg._warmup(page, prof):     # walled = anti-bot, degraded IP
                    return {"ok": False, "reason": "captcha"}
        return _login_and_capture(page, page.context, email, password)


def _run_chromium(email: str, password: str, headless: bool) -> dict:
    """Alternate engine. Real Chrome + the amazon_login hardening, but EPHEMERAL: a fresh
    new_context (no shared amazon_profile/) discarded on close."""
    from playwright.sync_api import sync_playwright

    args = ["--disable-blink-features=AutomationControlled"]
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=headless, args=args)
        except Exception:
            browser = p.chromium.launch(headless=headless, args=args)   # no real Chrome installed
        ctx = browser.new_context(locale="en-US")
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        try:
            ctx.add_init_script(_PAGEERROR_SHIELD_ALL)
        except Exception:
            pass
        page = ctx.new_page()
        page.set_default_navigation_timeout(20000)
        page.set_default_timeout(15000)
        try:
            return _login_and_capture(page, ctx, email, password)
        finally:
            for closer in (ctx.close, browser.close):
                try:
                    closer()
                except Exception:
                    pass


def _emit(out, result: dict) -> int:
    out.write(json.dumps(result) + "\n")
    out.flush()
    return 0 if result.get("ok") else REASON_EXIT.get(result.get("reason"), 1)


def main() -> int:
    # The reused engine functions print progress to stdout; keep the JSON channel clean
    # by routing every print to stderr and writing the one JSON line to the real stdout.
    real_out = sys.stdout
    sys.stdout = sys.stderr

    try:
        email, password = _parse_creds(sys.stdin.read())
    except Exception:
        return _emit(real_out, {"ok": False, "reason": "bad_input"})

    engine = os.getenv("AMZ_LOGIN_ENGINE", "camoufox").strip().lower()
    headless = os.getenv("AMZ_LOGIN_HEADLESS", "on").strip().lower() != "off"
    warmup = os.getenv("AMZ_LOGIN_WARMUP", "off").strip().lower()

    try:
        if engine == "chromium":
            result = _run_chromium(email, password, headless)
        else:
            result = _run_camoufox(email, password, headless, warmup)
    except Exception as e:
        # Any launch/runtime failure -> typed error, never a hang or a stack trace on stdout.
        result = {"ok": False, "reason": "engine_error", "detail": f"{engine}:{type(e).__name__}"}

    return _emit(real_out, result)


def _selftest() -> None:
    assert all(callable(f) for f in (
        al._submit_login, al._skip_nags, al.cookies_to_header,
        sg.launch_browser, sg._continue_shopping, sg._cvf_error, sg._warmup_profile,
    )), "engine helpers import"

    assert al.cookies_to_header(
        [{"name": "at-main", "value": "x"}, {"name": "s", "value": "y"}]
    ) == "at-main=x;s=y", "cookie header contract"

    for bad in ("garbage", "{}", '{"email":"a@b.com"}', '{"email":"","password":"p"}',
                '{"email":"a@b.com","password":""}'):
        try:
            _parse_creds(bad)
        except Exception:
            continue
        raise AssertionError(f"bad_input not rejected: {bad}")
    assert _parse_creds('{"email":" a@b.com ","password":"pw"}') == ("a@b.com", "pw")

    assert set(REASON_EXIT) >= {"captcha", "login_failed", "timeout",
                                "bad_proxy", "bad_input", "engine_error"}, "reason map"
    print("selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        raise SystemExit(main())
