#!/usr/bin/env python3
"""Log into Amazon and open the addresses page. Personal use, own account.

Setup:
    pip install playwright && playwright install chromium
    # creds in a .env next to this file (or export them):
    #   AMZ_EMAIL=you@example.com
    #   AMZ_PASSWORD=yourpassword

Run:
    python amazon_login.py            # login + abre Mis Direcciones
    python amazon_login.py --add      # ...y agrega una dirección random válida
    python amazon_login.py --selftest # checa parser .env + generador, sin browser
"""
import os
import random
import re
import sys
from pathlib import Path

ADDRESSES_URL = "https://www.amazon.com/a/addresses"
SIGNOUT_URL = "https://www.amazon.com/gp/sign-out.html"

# Combos reales city/state/zip -> dirección "válida" pero random.
CITIES = [
    ("New York", "NY", "New York", "10001"),
    ("Los Angeles", "CA", "California", "90001"),
    ("Chicago", "IL", "Illinois", "60601"),
    ("Houston", "TX", "Texas", "77001"),
    ("Phoenix", "AZ", "Arizona", "85001"),
    ("Miami", "FL", "Florida", "33101"),
    ("Seattle", "WA", "Washington", "98101"),
    ("Denver", "CO", "Colorado", "80201"),
]
STREETS = ["Main St", "Oak Ave", "Maple Dr", "Cedar Ln", "Pine St",
           "Elm St", "Washington Ave", "Lake View Rd", "Park Blvd", "Sunset Dr"]
FIRST = ["James", "Mary", "John", "Patricia", "Robert", "Jennifer", "Michael", "Linda"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis"]


def random_address() -> dict:
    city, state, state_name, zipc = random.choice(CITIES)
    return {
        "name": f"{random.choice(FIRST)} {random.choice(LAST)}",
        "phone": f"{random.randint(200, 999)}{random.randint(200, 999)}{random.randint(1000, 9999)}",
        "line1": f"{random.randint(100, 9999)} {random.choice(STREETS)}",
        "city": city, "state": state, "state_name": state_name, "zip": zipc,
    }


def load_dotenv(path: Path) -> None:
    # ponytail: 6-line .env reader; swap for python-dotenv if you need multiline/escapes
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip("'\""))


# --- Capa humana (pacing ligero, portado calibrado de amazon_signup.py) ---
# Apagable: AMZ_LOGIN_HUMAN=off -> tecleo/click instantáneo de antes.
HUMAN = os.getenv("AMZ_LOGIN_HUMAN", "on").strip().lower() != "off"
_cursor = {"x": None, "y": None}
_VP = {}


def _pause(page, lo=400, hi=1500) -> None:
    page.wait_for_timeout(random.randint(lo, hi))


def _vp(page):
    if not _VP:
        s = page.viewport_size
        if s:
            _VP["w"], _VP["h"] = s["width"], s["height"]
        else:
            d = page.evaluate("({w: window.innerWidth, h: window.innerHeight})")
            _VP["w"], _VP["h"] = d["w"], d["h"]
    return _VP["w"], _VP["h"]


def _human_move(page, x, y) -> None:
    """Cursor en curva Bezier cuadrática + smoothstep ease, en sub-pasos reales. En Chromium
    plano page.mouse.move teletransporta -> la curva la trazamos nosotros (signup la delega a
    Camoufox humanize=0.8, que aquí no existe)."""
    w, h = _vp(page)
    x = max(1, min(w - 1, int(x)))
    y = max(1, min(h - 1, int(y)))
    sx = _cursor["x"] if _cursor["x"] is not None else w // 2
    sy = _cursor["y"] if _cursor["y"] is not None else h // 2
    cx = (sx + x) / 2 + random.randint(-40, 40)
    cy = (sy + y) / 2 + random.randint(-40, 40)
    steps = random.randint(12, 22)
    for i in range(1, steps + 1):
        t = i / steps
        e = t * t * (3 - 2 * t)  # smoothstep
        mx = (1 - e) ** 2 * sx + 2 * (1 - e) * e * cx + e ** 2 * x
        my = (1 - e) ** 2 * sy + 2 * (1 - e) * e * cy + e ** 2 * y
        page.mouse.move(int(mx), int(my))
        page.wait_for_timeout(random.randint(6, 16))
    page.mouse.move(x, y)
    _cursor["x"], _cursor["y"] = x, y


def _human_click(page, locator, timeout=5000) -> None:
    """Acerca el mouse a un punto INTERNO aleatorio del elemento (no el centro) y clickea. El
    movimiento previo es la señal humana; el click del locator (auto-wait) es lo fiable."""
    if HUMAN:
        try:
            locator.scroll_into_view_if_needed(timeout=4000)
            box = locator.bounding_box()
            if box:
                tx = box["x"] + box["width"] * random.uniform(0.25, 0.75)
                ty = box["y"] + box["height"] * random.uniform(0.25, 0.75)
                _human_move(page, tx, ty)
                _pause(page, 100, 300)
        except Exception:
            pass
    locator.click(timeout=timeout)


def _human_type(page, locator, text, typos: bool = True) -> None:
    """Teclea con cadencia variable + typo ocasional autocorregido. En campos NUMÉRICOS pasar
    typos=False: el backspace tras un typo filtrado borraría un dígito real (phone/zip)."""
    if not HUMAN:
        locator.fill(text)
        return
    _human_click(page, locator)
    _pause(page, 120, 350)
    for ch in text:
        if typos and random.random() < 0.02:
            page.keyboard.press(random.choice("asdfghjkl"))
            _pause(page, 100, 250)
            page.keyboard.press("Backspace")
            _pause(page, 100, 250)
        page.keyboard.type(ch, delay=random.randint(40, 110))
        if random.random() < 0.01:
            _pause(page, 200, 500)


def click_if_present(page, *selectors, timeout=4000) -> bool:
    """Click first matching/visible selector. Skip silently if none appear."""
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=timeout)
            _human_click(page, loc)
            print(f"click: {sel}")
            return True
        except Exception:
            continue
    return False


def _skip_nags(page) -> bool:
    """Salta nags post-login (teléfono 'Keep hackers out', passkey) y popup de país.
    Instantáneo: chequea count/visible sin esperar timeouts."""
    for sel in ("#ap-account-fixup-phone-skip-link", "text=Not now", "text=Ahora no",
                "text=Skip", "text=Maybe later",
                "text=Quedarse en Amazon.com", "text=Stay on Amazon.com"):
        loc = page.locator(sel).first
        if loc.count() and loc.is_visible():
            _human_click(page, loc)
            print(f"skip nag: {sel}")
            return True
    return False


def _submit_login(page, email, password) -> None:
    """Llena email+password. Tolera flujo nuevo (email->continue->password) y
    cuenta recordada (sólo password). Si no aparece ningún campo -> levanta para
    que el caller cierre la sesión existente y reintente."""
    page.wait_for_selector("#ap_email_login, #ap_email, #ap_password",
                           state="visible", timeout=15000)
    email_box = page.locator("#ap_email_login, #ap_email").first
    if email_box.count() and email_box.is_visible():
        _human_type(page, email_box, email, typos=False)
        if page.locator("#continue").count():
            _human_click(page, page.locator("#continue").first)
    page.wait_for_selector("#ap_password", state="visible", timeout=15000)
    _human_type(page, page.locator("#ap_password").first, password, typos=False)
    _human_click(page, page.locator("#signInSubmit").first)


def cookies_to_header(cookies) -> str:
    """Cookies de Playwright -> string estilo header Cookie: 'name=value;name2=value2'."""
    return ";".join(f"{c['name']}={c['value']}" for c in cookies)


def dump_cookies(context, path="amazon_cookies.txt") -> str:
    cookies = context.cookies("https://www.amazon.com")
    header = cookies_to_header(cookies)
    Path(path).write_text(header)
    print(f"cookies: {len(cookies)} -> {path}")
    return header


def _state_debug(name: str, html: str) -> Path:
    p = Path(__file__).with_name(name)
    p.write_text(html, encoding="utf-8")
    return p


def _select_state(page, state_name: str, state_abbr: str) -> bool:
    """Selecciona el estado tolerando TODAS las variantes del form (esto rompía con Timeout).
    En capas: (1) a-dropdown estilizado; (2) <select> nativo visible; (3) JS set value+change
    (aunque el select esté oculto); (4) dump del HTML del control y seguir (el ZIP suele inferirlo)."""
    sel = "#address-ui-widgets-enterAddressStateOrRegion"
    try:  # (1) dropdown estilizado
        _human_click(page, page.locator(sel).first, timeout=3000)
        page.wait_for_selector("a.a-dropdown-link", state="visible", timeout=3000)
        for rx in (rf"^\s*{re.escape(state_name)}\s*$", re.escape(state_name)):
            opt = page.locator("a.a-dropdown-link", has_text=re.compile(rx)).first
            if opt.count():
                _human_click(page, opt, timeout=3000)
                return True
    except Exception:
        pass
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    for kind, val in (("value", state_abbr), ("label", state_name)):  # (2) <select> nativo
        try:
            page.select_option(sel, timeout=2000, **{kind: val})
            return True
        except Exception:
            pass
    js = """(el, args) => {
      const [abbr, name] = args;
      const sels = el.matches('select') ? [el] : [...el.querySelectorAll('select')];
      const s = sels.find(x => x.options && x.options.length > 1);
      if (!s) return false;
      const want = [...s.options].find(o =>
        o.value === abbr || o.text.trim() === name || o.text.trim().startsWith(name));
      if (!want) return false;
      s.value = want.value;
      s.dispatchEvent(new Event('input', {bubbles: true}));
      s.dispatchEvent(new Event('change', {bubbles: true}));
      return true;
    }"""
    for css in (sel, "select[id*='State' i]", "select[name*='State' i]"):  # (3) JS
        try:
            loc = page.locator(css).first
            if loc.count() and loc.evaluate(js, [state_abbr, state_name]):
                return True
        except Exception:
            pass
    try:  # (4) dump + seguir
        html = page.locator(sel).first.evaluate("el => el.outerHTML")
        print(f"⚠️  estado '{state_name}' no seleccionable; HTML -> {_state_debug('state_debug.html', html)}")
    except Exception:
        print(f"⚠️  estado '{state_name}' no seleccionable; sigo (el ZIP suele inferirlo)")
    return False


def _select_country(page) -> bool:
    """Fuerza país = United States. Necesario sin proxy si la IP es MX y el form default-ea a otro
    país. Capas como _select_state: <select> value 'US'/label -> JS -> dump. No-op si no hay selector."""
    css_list = ("#address-ui-widgets-countryCode",
                "select[name*='countryCode' i]", "select[id*='country' i]", "select[name*='country' i]")
    present = [c for c in css_list if _safe_count(page, c)]
    if not present:
        return False
    for css in present:  # (1) <select> nativo
        for kind, val in (("value", "US"), ("label", "United States")):
            try:
                page.select_option(css, timeout=2000, **{kind: val})
                return True
            except Exception:
                pass
    js = """(el) => {
      const s = el.matches('select') ? el : el.querySelector('select');
      if (!s) return false;
      const want = [...s.options].find(o => o.value === 'US' || /united states/i.test(o.text));
      if (!want) return false;
      s.value = want.value;
      s.dispatchEvent(new Event('input', {bubbles: true}));
      s.dispatchEvent(new Event('change', {bubbles: true}));
      return true;
    }"""
    for css in present:  # (2) JS
        try:
            if page.locator(css).first.evaluate(js):
                return True
        except Exception:
            pass
    try:  # (3) dump
        html = page.locator(present[0]).first.evaluate("el => el.outerHTML")
        print(f"⚠️  país no seleccionable; HTML -> {_state_debug('country_debug.html', html)}")
    except Exception:
        print("⚠️  país no seleccionable; sigo")
    return False


def _safe_count(page, css) -> bool:
    try:
        return bool(page.locator(css).count())
    except Exception:
        return False


def _await_address_ok(page, timeout_ms: int = 6000) -> bool:
    """¿El alta se confirmó? SOLO señal POSITIVA: reaparece el tile #ya-myab-address-add-link
    (volvimos a la lista = guardó)."""
    remaining = timeout_ms
    while remaining > 0:
        try:
            if page.locator("#ya-myab-address-add-link").first.is_visible():
                return True
        except Exception:
            pass
        page.wait_for_timeout(400)
        remaining -= 400
    return False


def _address_listed(page, line1: str) -> bool:
    """¿La calle tecleada ya figura en la lista? El `value` de un <input> NO es texto del DOM, así
    que `line1` solo matchea cuando ya está LISTADA (guardada). Verificación dura tras recargar."""
    try:
        return page.get_by_text(line1, exact=False).first.count() > 0
    except Exception:
        return False


def _dismiss_not_found(page) -> bool:
    """Soft-404 ('no es una página activa' / 'not a functioning page', con link Continuar) que a
    veces interpone al abrir /a/addresses/add. Clic Continuar para que deje de servirlo."""
    try:
        body = page.locator("body").inner_text(timeout=2000).lower()
    except Exception:
        return False
    markers = ("no es una página activa", "not a functioning page",
               "buscas algo", "looking for something")
    if not any(m in body for m in markers):
        return False
    for sel in ("a:has-text('Continuar')", "a:has-text('Continue')",
                "text=Continuar", "text=Continue"):
        try:
            link = page.locator(sel).first
            if link.count() and link.is_visible():
                _human_click(page, link)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
                return True
        except Exception:
            continue
    return True  # detectado pero sin link clicable: igual reportar para reintentar la nav


def _fill_and_submit_address(page, a: dict) -> None:
    """Un intento de alta: abre el form (espera a que cargue), llena, estado/país, envía hasta 4× y
    CONFIRMA por señal positiva (reaparece el tile Add Address). Lanza si no confirma."""
    for _ in range(2):  # abrir form; despejar soft-404 y reintentar una vez
        click_if_present(page, "#ya-myab-address-add-link", "text=Add Address")
        try:
            page.wait_for_selector("#address-ui-widgets-enterAddressFullName",
                                   state="visible", timeout=10000)
            break
        except Exception:
            if not _dismiss_not_found(page):
                raise
            page.goto(ADDRESSES_URL, wait_until="domcontentloaded")
    else:
        raise RuntimeError("no cargó el form de alta (soft-404 persistente)")
    if _select_country(page):  # con IP MX el form puede venir en otro país -> forzar US antes de llenar
        print("país -> United States")
        page.wait_for_timeout(800)  # deja re-renderizar estado/zip
    # El nombre Amazon lo PRE-LLENA con el de la cuenta -> no reescribir (apendaría -> "Inappropriate
    # words in Name"); solo llenar si viene vacío.
    name_box = page.locator("#address-ui-widgets-enterAddressFullName")
    try:
        prefilled = name_box.input_value(timeout=3000).strip()
    except Exception:
        prefilled = ""
    if prefilled:
        print(f"nombre ya presente ('{prefilled}'), no se reescribe")
    else:
        _human_type(page, name_box, a["name"], typos=True)
    _human_type(page, page.locator("#address-ui-widgets-enterAddressPhoneNumber"),
                a["phone"], typos=False)  # numérico -> sin typos (no borrar dígitos)
    _human_type(page, page.locator("#address-ui-widgets-enterAddressLine1"), a["line1"], typos=True)
    if a.get("line2"):
        try:
            _human_type(page, page.locator("#address-ui-widgets-enterAddressLine2"),
                        a["line2"], typos=True)
        except Exception:
            pass
    _human_type(page, page.locator("#address-ui-widgets-enterAddressCity"), a["city"], typos=True)
    _select_state(page, a["state_name"], a["state"])
    _human_type(page, page.locator("#address-ui-widgets-enterAddressPostalCode"),
                a["zip"], typos=False)  # numérico -> sin typos
    # Enviar hasta 4× (Amazon suele pedir 2º clic tras validar el ZIP); éxito por señal POSITIVA.
    for _ in range(4):
        click_if_present(page, "#address-ui-widgets-form-submit-button", "text=Add address")
        click_if_present(page,
                         "input[name='address-ui-widgets-saveOriginalOrSuggestedAddress']",
                         "text=Use this address", "text=Use This Address")
        if _await_address_ok(page, timeout_ms=6000):
            return
    try:  # verificación dura: recargar y ver si la calle quedó listada
        page.goto(ADDRESSES_URL, wait_until="domcontentloaded")
    except Exception:
        pass
    if _address_listed(page, a["line1"]):
        return
    raise RuntimeError("el alta no se guardó tras re-presionar enviar -> reroll")


def add_random_address(page, attempts: int = 3) -> dict:
    """Agrega una dirección random con reintentos. Cada intento usa una dirección fresca; entre
    intentos vuelve a una grilla limpia. Lanza si no confirma tras `attempts`."""
    last_err = None
    for i in range(attempts):
        if i:  # reintento: resetear a la grilla por si el form quedó a medias
            try:
                page.goto(ADDRESSES_URL, wait_until="domcontentloaded")
            except Exception:
                pass
        a = random_address()
        try:
            _fill_and_submit_address(page, a)
            print("✅ dirección añadida:", a)
            return a
        except Exception as e:
            last_err = e
            print(f"⚠️  intento {i + 1}/{attempts} de alta falló: {e}")
    raise RuntimeError(f"no se confirmó el alta tras {attempts} intentos: {last_err}")


def main() -> int:
    from playwright.sync_api import sync_playwright

    load_dotenv(Path(__file__).with_name(".env"))
    email = os.environ.get("AMZ_EMAIL")
    password = os.environ.get("AMZ_PASSWORD")
    if not email or not password:
        sys.exit("Falta AMZ_EMAIL / AMZ_PASSWORD (ponlos en .env o export).")

    with sync_playwright() as p:
        # Perfil persistente: la sesión vive en disco -> login UNA vez, reúsa después.
        # Evita el CAPTCHA porque dejas de re-loguearte en cada corrida.
        profile = str(Path(__file__).with_name("amazon_profile"))
        launch = dict(user_data_dir=profile, headless=False, locale="en-US",
                      args=["--disable-blink-features=AutomationControlled"])
        try:
            ctx = p.chromium.launch_persistent_context(channel="chrome", **launch)
        except Exception:
            ctx = p.chromium.launch_persistent_context(**launch)  # sin Chrome real
        # Mata el tell duro de automatización (Playwright/Chrome expone navigator.webdriver=true).
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        page.goto(ADDRESSES_URL, wait_until="domcontentloaded")

        # Login SÓLO si Amazon manda a signin. accountfixup/otros nags NO son login.
        needs_login = "signin" in page.url or bool(
            page.locator("#ap_email, #ap_email_login").count())
        if needs_login:
            try:
                _submit_login(page, email, password)
            except Exception:
                # Sesión existente / "usuario distinto": cerrar la actual y reintentar
                # con la nueva.
                print("⚠️  sesión existente -> cierro la actual y reintento con la nueva")
                page.goto(SIGNOUT_URL, wait_until="domcontentloaded")
                page.goto(ADDRESSES_URL, wait_until="domcontentloaded")
                _submit_login(page, email, password)
            # CAPTCHA/código: resuélvelo TÚ en la ventana visible.
            print("⏳ Si sale CAPTCHA o código, resuélvelo en la ventana abierta...")

        # Espera Mis Direcciones (hasta ~3 min) saltando nags (teléfono/passkey/país) en
        # el camino. Sustituye al wait_for_url ciego que se quedaba pegado en accountfixup.
        for _ in range(180):
            if "/a/addresses" in page.url:
                break
            _skip_nags(page)
            page.wait_for_timeout(1000)

        # Si no aterrizó en direcciones (nag resuelto deja en home), fuerza la navegación.
        if "/a/addresses" not in page.url:
            try:
                page.goto(ADDRESSES_URL, wait_until="domcontentloaded")
            except Exception as e:
                print("nav falló:", e)
            _skip_nags(page)

        if "/a/addresses" in page.url:
            print("✅ En Mis Direcciones:", page.url)
            add_ok = True
            if "--add" in sys.argv:
                add_ok = False
                try:
                    add_random_address(page)
                    add_ok = True
                except Exception as e:
                    print("❌ NO se agregó la dirección:", e)
            dump_cookies(ctx)  # cookies valen aunque el alta falle (sesión logueada)
            if "--add" in sys.argv and not add_ok:
                print("⚠️  cookies extraídas, pero el alta de dirección FALLÓ (ver arriba)")
        else:
            print("⚠️  URL actual:", page.url, "(¿CAPTCHA/paso extra? resuélvelo en la ventana)")

        page.screenshot(path="amazon_addresses.png", full_page=True)
        try:
            input("Enter para cerrar el navegador...")
        except EOFError:
            page.wait_for_timeout(90_000)  # sin stdin: mantén ventana 90s visible
        ctx.close()
    return 0


def _selftest() -> None:
    import tempfile

    p = Path(tempfile.mkdtemp()) / ".env"
    p.write_text("# comment\nAMZ_EMAIL = a@b.com \nAMZ_PASSWORD='pw=secret'\nBAD LINE\n")
    os.environ.pop("AMZ_EMAIL", None)
    os.environ.pop("AMZ_PASSWORD", None)
    load_dotenv(p)
    assert os.environ["AMZ_EMAIL"] == "a@b.com", os.environ.get("AMZ_EMAIL")
    assert os.environ["AMZ_PASSWORD"] == "pw=secret", os.environ.get("AMZ_PASSWORD")
    for _ in range(50):
        a = random_address()
        assert len(a["phone"]) == 10 and a["phone"].isdigit(), a
        assert len(a["zip"]) == 5 and a["zip"].isdigit(), a
        assert a["state"].isalpha() and len(a["state"]) == 2, a
        assert a["line1"][0].isdigit(), a
    fake = [{"name": "ubid-main", "value": "130-1"}, {"name": "session-token", "value": '"a;b=c"'}]
    assert cookies_to_header(fake) == 'ubid-main=130-1;session-token="a;b=c"'
    assert callable(_skip_nags) and callable(_submit_login), "login helpers"
    assert all(callable(f) for f in (_select_state, _select_country, _await_address_ok,
                                     _address_listed, _dismiss_not_found, _fill_and_submit_address)), \
        "address helpers"
    assert all(callable(f) for f in (_pause, _vp, _human_move, _human_click, _human_type)), \
        "human helpers"
    print("selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        raise SystemExit(main())
