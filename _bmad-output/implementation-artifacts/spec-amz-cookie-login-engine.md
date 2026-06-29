---
title: 'Headless Amazon Login Engine (Cookie Generator Core)'
type: 'feature'
created: '2026-06-27'
status: 'in-review'
context: []
baseline_commit: 'c2e85f5'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The cookie-mode gate needs server-generated Amazon cookies, and the generator must not get fingerprinted as a bot. Two candidate engines already live in the repo — `amazon_signup.py` (Camoufox: a hardened anti-fingerprint Firefox, built to be undetectable headless, ephemeral per instance, with CAPTCHA detection + warmup) and `amazon_login.py` (real Chromium, humanized typing/mouse, `navigator.webdriver` patched) — but NEITHER exposes a headless, multi-account, callable "credential → cookie header" routine that fails cleanly (no human) on CAPTCHA. And it is unknown which engine, if either, gets past Amazon from the VPS IP.

**Approach:** Add a standalone `amazon_cookiegen.py` that is ENGINE-AGNOSTIC behind one stdin→JSON contract. `AMZ_LOGIN_ENGINE` selects the launcher — default `camoufox` (max anti-detection), `chromium` as the lighter alternate — so both can be A/B-tested against the VPS IP before any queue/UI is built. It reuses each file's helpers (Camoufox `launch_browser` + `_continue_shopping`/`_cvf_error` + warmup from signup; `_submit_login`/`_human_*`/`cookies_to_header` shared), runs ephemeral + headless on the direct VPS IP (proxy knob reserved), detects CAPTCHA/anomaly and returns a typed reason instead of waiting for a human, and prints the cookie header as one JSON line. This is the keystone the follow-on queue/API/UI subprocesses.

## Boundaries & Constraints

**Always:**
- Engine-agnostic: `AMZ_LOGIN_ENGINE` ∈ {`camoufox` (default), `chromium`}, BOTH behind the identical stdin→JSON contract. Reuse helpers — don't fork: from `amazon_signup.py` the Camoufox `launch_browser(proxy, headless, geoip_ip)`, `_continue_shopping`/`_cvf_error`, `_warmup*`; from `amazon_login.py` the `_submit_login` selector flow, `_human_*`, `_skip_nags`, `cookies_to_header`, and the Chromium hardening (`--disable-blink-features=AutomationControlled` + the `navigator.webdriver` init script).
- EPHEMERAL per run on BOTH engines — a fresh instance/context, NEVER a shared profile (multi-account: a persisted session contaminates the next credential). Camoufox is ephemeral by default; Chromium uses a fresh `new_context`. Always close + clean up in `finally`.
- Credentials enter ONLY via stdin (never argv). The password and the cookie value are NEVER written to logs/files/stdout except inside the single success JSON.
- Output is exactly one JSON line: `{"ok":true,"cookie":"..."}` or `{"ok":false,"reason":"..."}`. Exit 0 on ok, a distinct non-zero per failure reason.
- Headless by default (`AMZ_LOGIN_HEADLESS`; off → headful, e.g. under `xvfb-run`). On CAPTCHA / wall / MFA / selector timeout: DETECT and return a reason — never the interactive human-wait / `input()` path.

**Ask First:**
- If BOTH engines get persistently CAPTCHA-walled on the direct VPS IP, HALT before wiring the proxy (`AMZ_LOGIN_PROXY`) or any captcha-solver — that is the proxy/cost decision the owner deferred. (The walling would point at the IP axis, which no engine fixes.)

**Never:**
- No DB, no API, no job queue, no plan-limit, no frontend — those are the follow-on feature (Spec 2, see `deferred-work.md`).
- Do not modify `amazon_login.py` or `amazon_signup.py` (both keep working as-is; this script imports from them).
- No shared persistent profile, no address-add flow, no `input()` / visible-window human step, no argv credentials, no proxy wiring in v1 (the knob is reserved, not used).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy login | stdin `{"email","password"}`, valid account, no CAPTCHA (either engine) | stdout `{"ok":true,"cookie":"name=value;..."}`, exit 0 | N/A |
| CAPTCHA / MFA / wall | captcha image/form, `/ap/cvf` or MFA page, or "Continue shopping" wall | `{"ok":false,"reason":"captcha"}`, exit 2 | detect via captcha selectors + `_continue_shopping`/`_cvf_error`; NO human wait |
| Wrong credentials | bad email or password | `{"ok":false,"reason":"login_failed"}`, exit 3 | still on `/ap/signin` / auth-error text after submit |
| Selector timeout | page slow / unexpected layout / no signal | `{"ok":false,"reason":"timeout"}`, exit 4 | every wait bounded; never hangs |
| Malformed stdin | not JSON, or missing email/password | `{"ok":false,"reason":"bad_input"}`, exit 6 | validate BEFORE launching a browser |
| Engine can't launch | selected engine not installed / launch raises (e.g. Camoufox not fetched) | `{"ok":false,"reason":"engine_error"}`, exit 7 | catch launch failure; message names the engine, no secrets |

</frozen-after-approval>

## Code Map

- `amazon_cookiegen.py` (NEW) — engine-agnostic server login entry; imports helpers from both files.
- `amazon_signup.py` — Camoufox path (default): `launch_browser(proxy, headless, geoip_ip)` (ephemeral, headless-grade fingerprint), `_continue_shopping`/`_cvf_error` (CAPTCHA detection), `_warmup*`. Imported, NOT modified.
- `amazon_login.py` — Chromium path + shared: `_submit_login` (`#ap_email_login`/`#ap_email` → `#continue` → `#ap_password` → `#signInSubmit`), `_human_*`, `_skip_nags`, `cookies_to_header`, launch hardening. Imported, NOT modified. (Side-effect-free import: `sync_playwright` is imported inside its `main()`.)

## Tasks & Acceptance

**Execution:**
- [x] `amazon_cookiegen.py` -- parse stdin JSON `{email,password}`; read env `AMZ_LOGIN_ENGINE` (default `camoufox`), `AMZ_LOGIN_HEADLESS` (default on), `AMZ_LOGIN_WARMUP` (default off), `AMZ_LOGIN_PROXY` (reserved, empty in v1); validate fields before any browser launch -- entry contract.
- [x] `amazon_cookiegen.py` -- `launch(engine)`: `camoufox` → `signup.launch_browser(proxy=None, headless=..., geoip_ip=None)` (ephemeral by default); `chromium` → ephemeral `p.chromium.launch(channel="chrome", headless=..., args=[...])` (fallback no channel) + `new_context(locale="en-US")` + webdriver init script. Return `(context, page)`; a launch failure → `engine_error` -- engine bring-up.
- [x] `amazon_cookiegen.py` -- if `AMZ_LOGIN_WARMUP != off` run the light `_warmup*` (camoufox path); if warmup reports `walled`, treat as `captcha` -- optional warmup.
- [x] `amazon_cookiegen.py` -- log in via `_submit_login(page, email, password)` (works on both Playwright engines); clear nags with `_skip_nags`; handle existing-session sign-out + retry like `amazon_login.main` -- login.
- [x] `amazon_cookiegen.py` -- DETECT outcome: success = an auth cookie present (`at-main`/`sess-at-main`) or account-nav reached; failure = captcha selectors (`#auth-captcha-image`, `form[action*='captcha' i]`, "Enter the characters"), `_continue_shopping` wall, `_cvf_error`, MFA, still on `/ap/signin`, or bounded timeout → map to the matrix reasons + exit codes -- robustness.
- [x] `amazon_cookiegen.py` -- on success `cookie = cookies_to_header(ctx.cookies("https://www.amazon.com"))`, print `{"ok":true,"cookie":cookie}`; close the browser in `finally` on every path -- capture + cleanup.
- [x] `amazon_cookiegen.py` -- `--selftest`: assert the helper symbols import from both `amazon_login` and `amazon_signup` and the JSON contract round-trips (the `bad_input` path) WITHOUT launching a browser or touching the network -- offline check.

**Acceptance Criteria:**
- Given a valid credential on stdin and no CAPTCHA, when run on the VPS with either engine, then stdout is one JSON line with a non-empty `cookie` header and exit 0.
- Given any failure (captcha / login / timeout / input / engine), when run, then it prints `{"ok":false,"reason":...}` with the matrix's distinct non-zero exit and NEVER blocks waiting for a human.
- Given an ephemeral profile, when two different credentials run back to back, then the second login is unaffected by the first (no shared session state).
- Given `--selftest` with no network, when run, then it passes without launching a browser.
- Given any run, when it completes, then the password and the cookie value appear nowhere except inside the single success JSON.

## Design Notes

Default `camoufox` (anti-detection is the owner's stated priority): stronger headless fingerprint — spoofs canvas/WebGL/fonts/navigator consistently, built to be undetectable headless, where plain Chromium leaks the `HeadlessChrome` UA + `SwiftShader` WebGL and the `webdriver` patch only fixes one tell. Ephemeral-by-design fits multi-account, and it works DIRECT (no proxy), so the fingerprint benefit applies on the VPS IP. Cost: a one-time `pip install 'camoufox[geoip]'` + `python -m camoufox fetch` (~300-500MB) on the VPS.

`chromium` alternate is kept because it is lighter, already installed (`playwright install chromium`), and reuses the humanization just added to `amazon_login.py` — so the two engines can be A/B-tested to see which one Amazon actually lets through from the VPS IP.

IMPORTANT — engine choice improves the fingerprint/behavior axis, NOT the IP axis: a datacenter VPS IP can get CAPTCHA'd regardless of engine. That is why the VPS-IP test is step one and `AMZ_LOGIN_PROXY` stays the reserved escalation (then `xvfb-run` headful for the chromium path). Process isolation (running the engine as a subprocess so a browser crash can't take down the single-process cc-core) is independent of engine — handled by Spec 2's worker. reason→exit: captcha=2, login_failed=3, timeout=4, bad_proxy=5 (reserved), bad_input=6, engine_error=7.

## Verification

**Commands:**
- `python amazon_cookiegen.py --selftest` -- expected: passes, launches no browser.
- `echo '{"email":"<acct>","password":"<pw>"}' | AMZ_LOGIN_ENGINE=camoufox python amazon_cookiegen.py` (on the VPS) -- expected: `{"ok":true,"cookie":"..."}` exit 0, or a typed reason.
- `echo '{"email":"<acct>","password":"<pw>"}' | AMZ_LOGIN_ENGINE=chromium python amazon_cookiegen.py` -- expected: same contract. Run both: this A/B answers "which engine gets past the VPS IP?".
- `echo 'garbage' | python amazon_cookiegen.py` -- expected: `{"ok":false,"reason":"bad_input"}` exit 6, no browser launched.
