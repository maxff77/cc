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


def click_if_present(page, *selectors, timeout=4000) -> bool:
    """Click first matching/visible selector. Skip silently if none appear."""
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=timeout)
            loc.click()
            print(f"click: {sel}")
            return True
        except Exception:
            continue
    return False


def cookies_to_header(cookies) -> str:
    """Cookies de Playwright -> string estilo header Cookie: 'name=value;name2=value2'."""
    return ";".join(f"{c['name']}={c['value']}" for c in cookies)


def dump_cookies(context, path="amazon_cookies.txt") -> str:
    cookies = context.cookies("https://www.amazon.com")
    header = cookies_to_header(cookies)
    Path(path).write_text(header)
    print(f"cookies: {len(cookies)} -> {path}")
    return header


def add_random_address(page) -> dict:
    """Llena el form 'Add Address' con datos random válidos y guarda."""
    a = random_address()
    click_if_present(page, "#ya-myab-address-add-link", "text=Add Address")
    page.fill("#address-ui-widgets-enterAddressFullName", a["name"])
    page.fill("#address-ui-widgets-enterAddressPhoneNumber", a["phone"])
    page.fill("#address-ui-widgets-enterAddressLine1", a["line1"])
    page.fill("#address-ui-widgets-enterAddressCity", a["city"])
    # estado: dropdown custom de Amazon (no <select>) -> abrir y elegir por nombre.
    # Las opciones son <a class="a-dropdown-link"> SIN href (no son role=link).
    page.click("#address-ui-widgets-enterAddressStateOrRegion")
    page.locator(
        "a.a-dropdown-link",
        has_text=re.compile(rf"^\s*{re.escape(a['state_name'])}\s*$"),
    ).click(timeout=8000)
    page.fill("#address-ui-widgets-enterAddressPostalCode", a["zip"])
    click_if_present(page, "#address-ui-widgets-form-submit-button", "text=Add address")
    # Amazon puede pedir confirmar/normalizar (USPS) -> aceptar lo propuesto.
    click_if_present(page,
                     "input[name='address-ui-widgets-saveOriginalOrSuggestedAddress']",
                     "text=Use this address", "text=Use This Address",
                     "#address-ui-widgets-form-submit-button")
    print("dirección añadida:", a)
    return a


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
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        page.goto(ADDRESSES_URL, wait_until="domcontentloaded")

        # Login SÓLO si Amazon nos manda a signin (con perfil guardado ya no pasa).
        if "signin" in page.url or page.locator("#ap_email, #ap_email_login").count():
            page.fill("#ap_email, #ap_email_login", email)
            if page.locator("#continue").count():  # botón sólo en el flujo nuevo
                page.click("#continue")
            page.fill("#ap_password", password)
            page.click("#signInSubmit")
            # CAPTCHA/código: resuélvelo TÚ en la ventana visible (hasta 3 min).
            print("⏳ Si sale CAPTCHA o código, resuélvelo en la ventana abierta...")
            try:
                page.wait_for_url("**/a/addresses**", timeout=180_000)
            except Exception:
                pass

        # Tras login Amazon a veces pide agregar teléfono ("Keep hackers out"). Saltar.
        click_if_present(page, "#ap-account-fixup-phone-skip-link", "text=Not now")

        # Ir a Mis Direcciones; reintenta la navegación si falla.
        for _ in range(2):
            try:
                page.goto(ADDRESSES_URL, wait_until="domcontentloaded")
                break
            except Exception as e:
                print("nav falló, reintento:", e)

        # Popup de país ("Estás en Amazon.com / ¿desde México?") -> quedarse en .com
        click_if_present(page, "text=Quedarse en Amazon.com")

        if "/a/addresses" in page.url:
            print("✅ En Mis Direcciones:", page.url)
            if "--add" in sys.argv:
                try:
                    add_random_address(page)
                except Exception as e:
                    print("⚠️  fallo al agregar dirección:", e)
            dump_cookies(ctx)
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
    print("selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        raise SystemExit(main())
