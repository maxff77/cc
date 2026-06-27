#!/usr/bin/env python3
"""Cosecha cookies de Amazon en N cuentas. Personal, multi-cuenta, anti-detect.

Cada cuenta corre en un navegador EFÍMERO Camoufox (anti-detect Firefox): huella
de dispositivo real y distinta por lanzamiento (canvas/WebGL/fonts), y con geoip
deriva timezone/locale/geo/WebRTC de la IP del proxy. El perfil NO se conserva (se
descarta al cerrar) -> cada cuenta aislada, sin acumular info en disco.

Setup:
    pip install -U 'camoufox[geoip]' && python -m camoufox fetch   # comillas: zsh come []
    # AMZ_SAVER=off para cargar todo; por defecto bloquea video/fuentes/trackers +
    # imágenes en el warmup (el login SÍ carga imágenes -> el CAPTCHA se ve).
    # AMZ_WARMUP=light(default)|full|off  velocidad del calentamiento: light = rápido,
    #   full = máx anti-CAPTCHA (más lento), off = directo al login.
    # cuentas en accounts.txt (junto a este archivo), una por línea:
    #   email@example.com:password        (el password puede contener ':')
    # proxies en proxies.txt (junto a este archivo), uno por línea, formato Decodo:
    #   host:port:user:pass     p.ej. gate.decodo.com:10001:USER:PASS
    #   en Decodo cada PUERTO = una sesión sticky / IP distinta (10001..10100).
    #   Se eligen por LRU + enfriamiento de 10 min -> al reusar un puerto la IP ya rotó.
    # (fallback single-proxy: AMZ_PROXY=http://user-{session}:pass@host:port en .env)
    # compat single-account: si no hay accounts.txt, cae a AMZ_EMAIL/AMZ_PASSWORD del .env

Run:
    python amazon_login.py                     # primera cuenta (compat single)
    python amazon_login.py --account x@y.com   # esa cuenta
    python amazon_login.py --all               # TODAS las de accounts.txt (secuencial)
    python amazon_login.py --no-add            # solo login (no agrega dirección ni cookies)
    python amazon_login.py --headless          # sin pantalla (nadie resuelve CAPTCHA/OTP)
    python amazon_login.py --selftest          # checa parsers + pick_proxy, sin browser

Passkey: el prompt (incl. el diálogo nativo de iCloud) se auto-cancela vía un init
script que neutraliza WebAuthn -> Amazon cae a contraseña sola, sin click humano.

Códigos de salida: 0 ok / 1 alta falló / 2 necesita humano (CAPTCHA/OTP). Con --all
devuelve el peor -> útil para alertar desde cron.

⏱️  Una corrida (warmup + login + alta) debe caber en la ventana sticky del proxy
(~10 min) para que la IP no rote a media sesión; el warmup va acotado para eso.
"""
from __future__ import annotations  # 'dict | None' en anotaciones aun en py<3.10

import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.parse
from pathlib import Path

ADDRESSES_URL = "https://www.amazon.com/a/addresses"
HOME_URL = "https://www.amazon.com"

# Cookies cosechadas por cuenta + estado de enfriamiento de proxies (runtime).
COOKIES_DIR = Path(__file__).with_name("cookies")
ACCOUNTS_FILE = Path(__file__).with_name("accounts.txt")
PROXIES_FILE = Path(__file__).with_name("proxies.txt")
PROXY_STATE_FILE = Path(__file__).with_name("proxy_state.json")

# Sitios neutrales LIVIANOS (texto) para sembrar historial antes de Amazon: la conducta
# (wander/scroll) es lo que importa, no el peso del sitio -> bytes mínimos por el proxy.
NEUTRAL_SITES = [
    "https://en.wikipedia.org/wiki/Special:Random",
    "https://news.ycombinator.com",
    "https://lite.cnn.com",
    "https://text.npr.org",
]

# Términos benignos para el calentamiento (búsqueda "humana" pre-login).
WARMUP_SEARCHES = ["wireless mouse", "usb c cable", "coffee mug", "phone case",
                   "notebook", "headphones", "water bottle", "desk lamp", "watch"]

# Combos reales city/state/zip -> dirección "válida" pero random.
# ZIPs residenciales (no los "x01" que suelen ser PO-box/comercial y Amazon marca como
# "fuera del área de servicio"); cada combo city/state/zip es consistente.
CITIES = [
    ("New York", "NY", "New York", "10025"),
    ("Los Angeles", "CA", "California", "90001"),
    ("Chicago", "IL", "Illinois", "60614"),
    ("Houston", "TX", "Texas", "77018"),
    ("Phoenix", "AZ", "Arizona", "85013"),
    ("Miami", "FL", "Florida", "33101"),
    ("Seattle", "WA", "Washington", "98103"),
    ("Denver", "CO", "Colorado", "80202"),
]
STREETS = ["Main St", "Oak Ave", "Maple Dr", "Cedar Ln", "Pine St",
           "Elm St", "Washington Ave", "Lake View Rd", "Park Blvd", "Sunset Dr"]
FIRST = ["James", "Mary", "John", "Patricia", "Robert", "Jennifer", "Michael", "Linda"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis"]
UNITS = ["Apt", "Unit", "Ste", "#"]

# Direcciones ya usadas (fingerprints) -> garantiza no repetir NUNCA la misma calle.
USED_ADDRESSES_FILE = Path(__file__).with_name("used_addresses.txt")


def random_address() -> dict:
    city, state, state_name, zipc = random.choice(CITIES)
    line2 = f"{random.choice(UNITS)} {random.randint(1, 9999)}" if random.random() < 0.7 else ""
    return {
        "name": f"{random.choice(FIRST)} {random.choice(LAST)}",
        "phone": f"{random.randint(200, 999)}{random.randint(200, 999)}{random.randint(1000, 9999)}",
        "line1": f"{random.randint(100, 99999)} {random.choice(STREETS)}",
        "line2": line2,
        "city": city, "state": state, "state_name": state_name, "zip": zipc,
    }


def _addr_fingerprint(a: dict) -> str:
    """Huella de la DIRECCIÓN (no del nombre/teléfono) -> dedup de la misma calle."""
    raw = "|".join(str(a[k]) for k in ("line1", "line2", "city", "state", "zip"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _seen_fingerprints(seen_path: Path) -> set:
    return set(seen_path.read_text().split()) if seen_path.exists() else set()


def unique_address(seen_path: Path = USED_ADDRESSES_FILE) -> dict:
    """Dirección válida GARANTIZADA única vs las ya usadas (persistidas en
    seen_path). Imposible repetir: si el fingerprint ya existe, reroll. El espacio
    (nº de calle x calle x unidad x ciudad) es enorme, así que casi siempre acierta
    al primer intento; el archivo lo vuelve garantía dura, no probabilística."""
    seen = _seen_fingerprints(seen_path)
    for _ in range(100_000):
        a = random_address()
        fp = _addr_fingerprint(a)
        if fp not in seen:
            a["_fp"] = fp
            return a
    raise RuntimeError("espacio de direcciones agotado (imposible en la práctica)")


def _record_address(a: dict, seen_path: Path = USED_ADDRESSES_FILE) -> None:
    with seen_path.open("a", encoding="utf-8") as f:
        f.write(a["_fp"] + "\n")


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


def load_accounts(path: Path = ACCOUNTS_FILE) -> list:
    """Lee accounts.txt: 'email:password' por línea (#comments y vacías ignoradas).
    split(':', 1) -> el password puede contener ':'. Sin archivo (o vacío) cae a
    AMZ_EMAIL/AMZ_PASSWORD del entorno (.env) para compat single-account."""
    accounts = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            email, pw = line.split(":", 1)
            email, pw = email.strip(), pw.strip()
            if email and pw:
                accounts.append({"email": email, "password": pw})
    if not accounts:
        email, pw = os.environ.get("AMZ_EMAIL"), os.environ.get("AMZ_PASSWORD")
        if email and pw:
            accounts.append({"email": email, "password": pw})
    return accounts


def account_slug(email: str) -> str:
    """Slug filesystem-safe y ESTABLE por email (para profiles/<slug> y cookies/<slug>)."""
    return re.sub(r"[^a-z0-9]+", "_", email.lower()).strip("_") or "default"


def proxy_for(email: str) -> dict | None:
    """Parsea AMZ_PROXY -> dict proxy de Playwright. Sustituye {session} por un token
    estable por cuenta (sha256(email)[:10]) -> IP sticky DISTINTA por cuenta, estable
    dentro de la ventana del proxy. Sin AMZ_PROXY -> None (conexión directa)."""
    raw = os.environ.get("AMZ_PROXY", "").strip()
    if not raw:
        return None
    session = hashlib.sha256(email.encode()).hexdigest()[:10]
    u = urllib.parse.urlsplit(raw.replace("{session}", session))
    if not u.hostname:
        return None
    server = f"{u.scheme or 'http'}://{u.hostname}" + (f":{u.port}" if u.port else "")
    proxy = {"server": server}
    if u.username:
        proxy["username"] = urllib.parse.unquote(u.username)
    if u.password:
        proxy["password"] = urllib.parse.unquote(u.password)
    return proxy


def proxy_from_line(line: str) -> dict | None:
    """'host:port:user:pass' (formato Decodo/Smartproxy) -> dict proxy de Playwright.
    split(':', 3) -> el password puede contener ':'. Es texto plano, no URL, así que
    el '+' y demás se quedan literales. None si la línea no trae al menos host:port."""
    parts = line.strip().split(":", 3)
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    proxy = {"server": f"http://{parts[0]}:{parts[1]}"}
    if len(parts) >= 4:
        proxy["username"], proxy["password"] = parts[2], parts[3]
    return proxy


def load_proxies(path: Path = PROXIES_FILE) -> list:
    """Lee proxies.txt ('host:port:user:pass' por línea, # y vacías ignoradas). En
    Decodo gate.decodo.com cada PUERTO es una sesión sticky / IP distinta -> la lista
    de puertos 10001..10100 son 100 endpoints. Se asignan por índice a las cuentas
    (1:1) -> cada cuenta su IP propia, nunca compartida = sin señal de enlace."""
    proxies = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            pr = proxy_from_line(line)
            if pr:
                proxies.append(pr)
    return proxies


def load_proxy_state(path: Path = PROXY_STATE_FILE) -> dict:
    """{server: last_used_epoch} para el enfriamiento. Ausente/corrupto -> {}."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_proxy_state(state: dict, path: Path = PROXY_STATE_FILE) -> None:
    path.write_text(json.dumps(state), encoding="utf-8")


def pick_proxy(proxies: list, state: dict, now: float, cooldown: int = 600):
    """Elige el proxy MENOS-recientemente-usado (LRU). Devuelve (proxy, wait_s):
    wait_s>0 si hasta el más viejo lleva <cooldown -> hay que esperar a que su IP
    sticky rote (Decodo la suelta a los ~10 min) y así garantizar IP distinta al
    reusar el puerto. Puro: recibe `now`, no llama al reloj (testeable)."""
    if not proxies:
        return None, 0
    pick = min(proxies, key=lambda pr: state.get(pr["server"], 0))
    last = state.get(pick["server"], 0)
    wait = max(0, cooldown - (now - last)) if last else 0
    return pick, wait


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


def _select_state(page, state_name: str, state_abbr: str) -> bool:
    """Selecciona el estado tolerando TODAS las variantes del form de Amazon (esto rompía
    con Timeout). En capas, de lo más humano al fallback duro:
    (1) a-dropdown estilizado: abrir, esperar opciones, clic por texto exacto->contains;
    (2) <select> nativo VISIBLE: select_option por value(abbr)->label(name);
    (3) JS: setear el value del <select> nativo + disparar 'change' —funciona aunque el
        select esté OCULTO o la página venga SIN CSS (el caso del proxy free-trial)—;
    (4) si nada: volcar el HTML del control a cookies/state_debug.html y seguir (el ZIP a
        veces lo infiere). Devuelve si acertó."""
    sel = "#address-ui-widgets-enterAddressStateOrRegion"
    # (1) dropdown estilizado (lo más humano): abrir y clicar la opción
    try:
        page.click(sel, timeout=3000)
        page.wait_for_selector("a.a-dropdown-link", state="visible", timeout=3000)
        for rx in (rf"^\s*{re.escape(state_name)}\s*$", re.escape(state_name)):
            opt = page.locator("a.a-dropdown-link", has_text=re.compile(rx)).first
            if opt.count():
                opt.click(timeout=3000)
                return True
    except Exception:
        pass
    try:
        page.keyboard.press("Escape")  # cierra un dropdown nativo que haya quedado abierto
    except Exception:
        pass
    # (2) <select> nativo visible
    for kind, val in (("value", state_abbr), ("label", state_name)):
        try:
            page.select_option(sel, timeout=2000, **{kind: val})
            return True
        except Exception:
            pass
    # (3) JS: setear el <select> nativo aunque esté oculto / sin CSS y disparar change/input
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
    for css in (sel, "select[id*='State' i]", "select[name*='State' i]"):
        try:
            loc = page.locator(css).first
            if loc.count() and loc.evaluate(js, [state_abbr, state_name]):
                return True
        except Exception:
            pass
    # (4) dump para diagnóstico exacto + seguir
    try:
        html = page.locator(sel).first.evaluate("el => el.outerHTML")
        dbg = COOKIES_DIR / "state_debug.html"  # ponytail: 1 archivo, debug single-account
        dbg.write_text(html, encoding="utf-8")
        print(f"⚠️  estado '{state_name}' no seleccionable; HTML del control -> {dbg}")
    except Exception:
        print(f"⚠️  estado '{state_name}' no seleccionable; sigo (el ZIP suele inferirlo)")
    return False


def _await_address_ok(page, timeout_ms: int = 6000) -> bool:
    """¿El alta se confirmó dentro del tiempo? SOLO señal POSITIVA: reaparece el tile
    #ya-myab-address-add-link (volvimos a la lista = guardó). NO lee texto de error: con el CSS
    dropeado por el proxy los hints OCULTOS se renderizan visibles y daban falso rechazo."""
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
    """¿La calle recién tecleada ya figura en la lista de direcciones? Señal POSITIVA de éxito,
    robusta aunque la página venga SIN CSS: el `value` de un <input> NO es texto del DOM, así que
    `line1` solo matchea cuando la dirección ya está LISTADA (guardada), no mientras está en el
    form. Verificación dura: usar tras recargar /a/addresses."""
    try:
        return page.get_by_text(line1, exact=False).first.count() > 0
    except Exception:
        return False


def _fill_and_submit_address(page, a: dict) -> None:
    """Un intento de alta: abre el form (espera a que cargue), llena, elige estado,
    envía y CONFIRMA (reaparece el tile Add Address). Lanza si no confirma."""
    click_if_present(page, "#ya-myab-address-add-link", "text=Add Address")
    page.wait_for_selector("#address-ui-widgets-enterAddressFullName",
                           state="visible", timeout=10000)
    # Tecleo humano. Numéricos con typos=False (un typo filtrado borraría un dígito real).
    # El nombre Amazon lo PRE-LLENA con el de la cuenta -> NO reescribir (apendaría y daría
    # "Inappropriate words in Name"); solo llenar si viene vacío. El nombre real es más legítimo.
    name_box = page.locator("#address-ui-widgets-enterAddressFullName")
    try:
        prefilled = name_box.input_value(timeout=3000).strip()
    except Exception:
        prefilled = ""
    if prefilled:
        print(f"nombre ya presente ('{prefilled}'), no se reescribe")
    else:
        _human_type(page, name_box, a["name"])
    _human_type(page, page.locator("#address-ui-widgets-enterAddressPhoneNumber"), a["phone"], typos=False)
    _human_type(page, page.locator("#address-ui-widgets-enterAddressLine1"), a["line1"])
    if a["line2"]:
        try:
            _human_type(page, page.locator("#address-ui-widgets-enterAddressLine2"), a["line2"])
        except Exception:
            pass  # algunos layouts no muestran línea 2; no es crítico
    _human_type(page, page.locator("#address-ui-widgets-enterAddressCity"), a["city"])
    _select_state(page, a["state_name"], a["state"])
    _human_type(page, page.locator("#address-ui-widgets-enterAddressPostalCode"), a["zip"], typos=False)
    # Enviar. La página a veces viene SIN CSS (el proxy dropea los stylesheets) y entonces TODOS
    # los hints de error OCULTOS se ven -> leer texto de error daba falso rechazo. Por eso NO se
    # confía en el texto: se presiona enviar hasta 4 veces (Amazon suele pedir un 2º click tras
    # validar el ZIP) y el ÉXITO se confirma por señal POSITIVA (reaparece el tile de alta).
    for _ in range(4):
        click_if_present(page, "#address-ui-widgets-form-submit-button", "text=Add address")
        # Amazon puede interponer confirmar/normalizar (USPS) -> aceptar lo propuesto.
        click_if_present(page,
                         "input[name='address-ui-widgets-saveOriginalOrSuggestedAddress']",
                         "text=Use this address", "text=Use This Address")
        if _await_address_ok(page, timeout_ms=6000):
            return
    # Verificación dura: recargar la lista y ver si la calle quedó guardada (cubre un guardado
    # lento que no alcanzó a mostrar el tile). Sin CSS igual sirve: el value del input no es texto.
    try:
        page.goto(ADDRESSES_URL, wait_until="domcontentloaded")
    except Exception:
        pass
    if _address_listed(page, a["line1"]):
        return
    raise RuntimeError("el alta no se guardó tras re-presionar enviar -> reroll")


def add_random_address(page, attempts: int = 3) -> dict:
    """Agrega una dirección ÚNICA con reintentos. Cada intento usa una dirección
    FRESCA; `_record_address` solo corre al confirmar, así un intento fallido NO quema
    unicidad. Entre intentos vuelve a una grilla limpia."""
    last_err = None
    for i in range(attempts):
        if i:  # reintento: resetear a la grilla por si el form quedó a medias
            try:
                page.goto(ADDRESSES_URL, wait_until="domcontentloaded")
            except Exception:
                pass
        a = unique_address()
        try:
            _fill_and_submit_address(page, a)
            _record_address(a)  # recién acá: dirección consumida, jamás se repetirá
            print("✅ dirección añadida (única):", {k: v for k, v in a.items() if k != "_fp"})
            return a
        except Exception as e:
            last_err = e
            print(f"⚠️  intento {i + 1}/{attempts} de alta falló: {e}")
    raise RuntimeError(f"no se confirmó el alta tras {attempts} intentos: {last_err}")


def _pause(page, lo=400, hi=1500):
    """Espera humana aleatoria (ms) sin bloquear el event loop ni importar time."""
    page.wait_for_timeout(random.randint(lo, hi))


# --- Capa conductual: lo que la detección de bots PESA (mousemove en curva, wheel
# nativo, tipeo irregular, micro-drift al leer). La CURVA humana la traza Camoufox
# (humanize=True) por nosotros en cada page.mouse.move; acá solo decidimos A DÓNDE va el
# cursor (wander sin rumbo, approach a elementos, micro-drift al leer) y rastreamos su
# posición. Todo cuesta ~0 bytes -> no pelea con el ahorro de proxy.
_cursor = {"x": None, "y": None}  # posición virtual del mouse (Playwright no la expone)


_VIEWPORT = {"wh": (1280, 800)}  # tamaño de ventana cacheado; _prime_viewport lo llena 1x/cuenta


def _prime_viewport(page) -> None:
    """Lee el tamaño de ventana UNA vez y lo cachea. Llamar SOLO sobre una página calma
    (blank, pre-Amazon). Clave anti-cuelgue: evita que _vp haga page.evaluate en CADA
    movimiento de mouse -> ese evaluate NO respeta timeout (Playwright lo ignora) y se
    cuelga indefinido detrás del JS anti-bot que clava el hilo tras el muro 'Continue
    shopping'. viewport_size es una propiedad (sin round-trip) -> no puede colgar; solo si
    viene None se cae al único evaluate, hecho acá donde la página todavía está ociosa."""
    try:
        vs = page.viewport_size
        if vs and vs.get("width") and vs.get("height"):
            _VIEWPORT["wh"] = (max(320, int(vs["width"])), max(480, int(vs["height"])))
            return
    except Exception:
        pass
    try:
        d = page.evaluate("() => ({w: innerWidth, h: innerHeight})")
        _VIEWPORT["wh"] = (max(320, int(d["w"])), max(480, int(d["h"])))
    except Exception:
        _VIEWPORT["wh"] = (1280, 800)


def _vp(page) -> tuple:
    # ponytail: el viewport no cambia durante la corrida -> devolver el cache que
    # _prime_viewport llenó sobre una página calma; NUNCA tocar la página acá (el
    # page.evaluate por-movimiento era el cuelgue de ~5 min del warmup). `page` queda
    # por compat con los ~8 call sites.
    return _VIEWPORT["wh"]


def _human_move(page, x, y):
    """Mueve el cursor a (x,y) con UNA sola page.mouse.move: Camoufox (humanize=True) ya
    traza la curva humana (Bezier nativa con ease) por nosotros. Hacerlo a mano en N
    sub-pasos era el BUG —humanize re-animaba CADA sub-paso, así el mouse iba lento/
    errático y el warmup tardaba minutos—. Mantiene _cursor para el micro-drift de _idle
    y el approach de _human_click; clampa al viewport."""
    w, h = _vp(page)
    x, y = max(1, min(w - 1, int(x))), max(1, min(h - 1, int(y)))
    page.mouse.move(x, y)
    _cursor["x"], _cursor["y"] = x, y


def _idle(page, lo=400, hi=1500):
    """Pausa de 'lectura' que cada tanto nudgea el cursor unos px: el humano no congela
    el mouse mientras lee. Reemplaza los _pause de lectura del warmup."""
    total = random.randint(lo, hi)
    spent = 0
    while spent < total:
        step = min(total - spent, random.randint(200, 700))
        page.wait_for_timeout(step)
        spent += step
        if random.random() < 0.25 and _cursor["x"] is not None:
            _human_move(page, _cursor["x"] + random.randint(-30, 30),
                        _cursor["y"] + random.randint(-30, 30))


def _wander(page):
    """Mouse 'sin rumbo' en curva Bezier con micro-pausas: un humano mirando la página
    genera mousemove constantes; un bot, cero. Barato (~0 bytes) y de alto impacto."""
    w, h = _vp(page)
    for _ in range(random.randint(2, 5)):
        _human_move(page, random.randint(2, w - 2), random.randint(2, h - 2))
        _pause(page, 200, 900)


def _human_scroll(page, n=None, up=False):
    """Scroll con RUEDA real (page.mouse.wheel) en tramos chicos con lectura + micro-
    drift. window.scrollBy es programático y NO dispara wheel nativos -> delator; esto
    sí. A veces re-lee hacia arriba (humano). Magnitud de rueda variable."""
    for _ in range(n or random.randint(3, 7)):
        page.mouse.wheel(0, random.randint(90, 300) * (-1 if up else 1))
        _idle(page, 250, 900)
        if not up and random.random() < 0.2:
            page.mouse.wheel(0, -random.randint(40, 120))
            _pause(page, 300, 800)


def _human_click(page, locator, timeout=5000):
    """Acerca el mouse al elemento por curva Bezier (mousemove reales) apuntando a un
    punto INTERNO aleatorio —no el centro— y luego clickea con el locator (auto-wait/
    scroll de Playwright = fiable). El movimiento previo es la señal humana."""
    try:
        locator.scroll_into_view_if_needed(timeout=4000)
        box = locator.bounding_box()
        if box:
            tx = box["x"] + box["width"] * random.uniform(0.25, 0.75)
            ty = box["y"] + box["height"] * random.uniform(0.25, 0.75)
            _human_move(page, tx, ty)
            _pause(page, 120, 400)
    except Exception:
        pass  # sin bounding box / fuera de vista: el click del locator igual resuelve
    locator.click(timeout=timeout)


def _human_type(page, locator, text, typos: bool = True):
    """Teclea con cadencia variable + pausa de 'pensar' ocasional y, si typos, un error
    ocasional + backspace. En campos NUMÉRICOS pasar typos=False: si el campo filtra la
    letra del typo, el backspace borraría un dígito REAL (corrompe phone/zip)."""
    _human_click(page, locator)
    _pause(page, 200, 600)
    for ch in text:
        if typos and random.random() < 0.06:  # typo + backspace (errar es humano)
            page.keyboard.press(random.choice("asdfghjkl"))
            _pause(page, 120, 320)
            page.keyboard.press("Backspace")
            _pause(page, 120, 320)
        page.keyboard.type(ch, delay=random.randint(50, 190))
        if random.random() < 0.04:  # pausa de "pensar" a media palabra
            _pause(page, 250, 700)


def _continue_shopping(page) -> bool:
    """Amazon a veces interpone un muro 'Click the button below to continue shopping' (anti-bot
    suave, frecuente con IPs de proxy de baja reputación) que reemplaza la home -> el warmup no
    halla la barra de búsqueda. Clic en 'Continue shopping' y seguir. Rápido (count+is_visible,
    sin esperas largas) y best-effort -> nunca lanza."""
    for sel in ("button:has-text('Continue shopping')",
                "input[type='submit'][value='Continue shopping']",
                "a:has-text('Continue shopping')"):
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=5000)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                except Exception:
                    pass
                print("muro 'Continue shopping' saltado")
                return True
        except Exception:
            continue
    return False


def _warmup_profile() -> dict:
    """Perfil de calentamiento según AMZ_WARMUP: 'light' (default, rápido pero humano),
    'full' (máx anti-CAPTCHA, más lento) u 'off' (sin warmup -> directo al login). Define
    cuántos sitios/búsquedas/productos y qué tan largas las pausas de lectura ('read')."""
    mode = os.environ.get("AMZ_WARMUP", "light").strip().lower()
    if mode == "off":
        return {"mode": "off"}
    if mode == "full":
        return {"mode": "full", "sites": (1, 2), "searches": (1, 3),
                "products": (0, 2), "read": (1500, 3500)}
    return {"mode": "light", "sites": (0, 1), "searches": (1, 1),  # default (incl. inválidos)
            "products": (0, 0), "read": (500, 1200)}


def _warmup_web(page, prof):
    """Antes de Amazon, curiosea 0–2 sitios neutrales LIVIANOS (texto) para sembrar historial:
    un perfil con historial real se ve menos 'recién nacido'. Cantidad y pausas salen de `prof`
    (AMZ_WARMUP). Conducta (wander/scroll/idle) intacta; bytes mínimos. Best-effort y acotado."""
    k = random.randint(*prof["sites"])
    if not k:
        return
    print("🌐 Sembrando historial (sitios neutrales)...")
    for url in random.sample(NEUTRAL_SITES, k=k):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            _idle(page, *prof["read"])
            _wander(page)
            _human_scroll(page)
        except Exception as e:
            print("⚠️  sitio neutral saltado (sigo):", e)


def _warmup_search(page, term):
    """Teclea el término y a VECES clickea una sugerencia de autocompletado (muy humano)
    en vez de Enter; best-effort -> si no hay sugerencias, completa lo que falte y Enter."""
    _continue_shopping(page)  # si Amazon puso el muro anti-bot, saltarlo antes de buscar
    box = page.locator("#twotabsearchtextbox").first
    try:
        box.fill("")  # limpia restos de la búsqueda anterior
    except Exception:
        pass
    if random.random() < 0.5:  # tipear parcial y probar una sugerencia
        _human_type(page, box, term[:max(3, len(term) - random.randint(2, 5))])
        try:
            sug = page.locator("div.s-suggestion, .s-suggestion, [data-testid='suggestion']").first
            sug.wait_for(state="visible", timeout=2500)
            _human_click(page, sug)
            return  # la sugerencia navega a resultados
        except Exception:
            pass  # sin sugerencia visible -> completar abajo
    try:
        rest = term[len(box.input_value()):]
    except Exception:
        rest = term
    if rest:
        _human_type(page, box, rest)
    _pause(page, 300, 900)
    page.keyboard.press("Enter")
    # Tras el muro anti-bot Amazon sirve una home DEGRADADA cuyo form de búsqueda es el LEGACY
    # (/s?url=search-alias=aps&field-keywords=...) -> responde "Sorry! Something went wrong" (500).
    # Si el Enter no aterrizó en resultados canónicos /s?k=, forzar la URL canónica de búsqueda.
    try:
        page.wait_for_url("**/s?k=**", timeout=6000)
    except Exception:
        try:
            page.goto(f"{HOME_URL}/s?k={urllib.parse.quote_plus(term)}",
                      wait_until="domcontentloaded")
        except Exception:
            pass


def _warmup(page, prof):
    """Curiosea Amazon como humano ANTES del login con señales CONDUCTUALES reales (mouse en
    curva, scroll de rueda con micro-drift, tipeo con typos + a veces autocompletado, abrir
    productos y leer). El nº de búsquedas/productos y la duración de las pausas salen de `prof`
    (AMZ_WARMUP: light rápido / full máx) -> cada corrida distinta. Best-effort: si un selector
    falta, avisa y sigue."""
    print("🔥 Calentando sesión (modo humano)...")
    rlo, rhi = prof["read"]
    try:
        page.goto(HOME_URL, wait_until="domcontentloaded")
        _continue_shopping(page)  # saltar el muro anti-bot si reemplazó la home
        _idle(page, rlo, rhi)
        _wander(page)
        _human_scroll(page)
        for _ in range(random.randint(*prof["searches"])):
            _warmup_search(page, random.choice(WARMUP_SEARCHES))
            try:  # esperar resultados; NO wait_for_load_state (race mid-nav)
                page.wait_for_url("**/s?k=**", timeout=15000)
            except Exception:
                pass  # el autocompletado pudo ir a otra URL; sigo igual
            _idle(page, rlo, rhi)
            _wander(page)
            _human_scroll(page)
            for _ in range(random.randint(*prof["products"])):
                try:  # abrir un producto: approach en curva -> click -> leer -> volver
                    prod = page.locator("a[href*='/dp/']").nth(random.randint(0, 4))
                    _human_click(page, prod)
                    page.wait_for_url("**/dp/**", timeout=12000)
                    _idle(page, rlo, rhi)
                    _human_scroll(page)
                    _wander(page)
                    _human_scroll(page, n=random.randint(1, 3), up=True)  # vuelve a subir
                    page.go_back(wait_until="domcontentloaded")
                    _idle(page, rlo, rhi)
                except Exception:
                    break  # sin producto clickable: la búsqueda ya cuenta, sigo
        page.goto(HOME_URL, wait_until="domcontentloaded")
        _continue_shopping(page)
        _idle(page, rlo, rhi)
        _wander(page)
        print("✅ Calentamiento completado")
    except Exception as e:
        print("⚠️  calentamiento incompleto (sigo):", e)


# Botones "saltar" de los interstitiales post-login de Amazon (teléfono, guardar
# passkey, etc.). Texto en inglés porque forzamos locale en-US.
SKIP_CONTROLS = [
    "#ap-account-fixup-phone-skip-link",   # "Keep hackers out" (teléfono)
    "text=Not now",
    "text=Skip for now",
    "text=Maybe later",
    "text=No thanks",
]


def _dismiss_interstitials(page) -> bool:
    """Salta cualquier interstitial post-login (teléfono, 'guardar passkey', etc.)
    clicando el primer botón Not now/Skip/Later visible. Best-effort, nunca lanza."""
    for sel in SKIP_CONTROLS:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=3000)
                print(f"interstitial saltado: {sel}")
                return True
        except Exception:
            continue
    return False


def _settle_after_login(page, timeout_s=180):
    """Espera llegar a /a/addresses tolerando interstitiales. Va saltando los
    prompts (teléfono, passkey...) con _dismiss_interstitials y deja tiempo para
    que el humano resuelva CAPTCHA/OTP/passkey en la ventana visible."""
    for _ in range(max(1, timeout_s // 2)):
        try:
            if "/a/addresses" in page.url:
                return True
            _dismiss_interstitials(page)
            page.wait_for_timeout(2000)
        except Exception:
            try:
                page.wait_for_timeout(1000)  # navegando -> reintenta el sondeo
            except Exception:
                pass
    try:
        return "/a/addresses" in page.url
    except Exception:
        return False


# Init script (corre antes de cualquier script de la página, en cada frame):
#  1) Passkey -> simula que el usuario SIEMPRE da "Cancelar": el get modal se
#     rechaza con NotAllowedError (idéntico a cancelar) y Amazon cae a contraseña.
#     Al neutralizar la API JS, el diálogo NATIVO de Chrome/iCloud nunca dispara
#     -> no hay nada que cerrar a mano. El get 'conditional' (autofill) queda mudo.
#  2) navigator.webdriver=false: cinturón extra. Camoufox ya enmascara la automatización
#     y genera la huella real (canvas/WebGL/fonts los maneja el motor, no este script).
WEBAUTHN_NEUTER = """
(() => {
  const c = navigator.credentials;
  if (c && c.get) {
    const cancel = () => Promise.reject(new DOMException('Not allowed', 'NotAllowedError'));
    c.get = (opts) => (opts && opts.mediation === 'conditional') ? new Promise(() => {}) : cancel();
    if (c.create) c.create = cancel;
  }
  try { Object.defineProperty(navigator, 'webdriver', { get: () => false }); } catch (e) {}
})();
"""


# Dominios de ads/trackers/analytics -> abortar siempre (lo que hace un adblock normal:
# cero riesgo de detección y el grueso del ahorro de datos en news/social).
TRACKERS = (
    "doubleclick.net", "google-analytics.com", "googlesyndication.com",
    "googletagmanager.com", "googletagservices.com", "google.com/pagead",
    "amazon-adsystem.com", "adsystem.amazon", "scorecardresearch.com",
    "facebook.net", "facebook.com/tr", "criteo.", "taboola.com", "outbrain.com",
    "hotjar.com", "segment.io", "branch.io", "adnxs.com", "moatads.com",
)


def _should_abort(resource_type, url, block_images) -> bool:
    """Decide si abortar una petición para ahorrar datos del proxy. SIEMPRE corta media
    (video/audio) + fuentes + ads/trackers (adblock normal = cero riesgo). Imágenes SOLO
    si block_images (True en warmup; False en login para que el CAPTCHA cargue). Nunca
    toca document/script/stylesheet/xhr (romperlos = layout roto + señal rara). Pura."""
    if resource_type in ("media", "font"):
        return True
    if any(t in url for t in TRACKERS):
        return True
    if resource_type == "image" and block_images:
        return True
    return False


def _install_saver_routes(context, saver):
    """Instala el ruteo de ahorro en el context (respeta AMZ_SAVER=off). `saver` es un
    dict mutable {'images': bool}: el caller lo flipa a False antes del login para dejar
    cargar el CAPTCHA. Best-effort por request: ante error, deja continuar la petición."""
    if os.environ.get("AMZ_SAVER") == "off":
        return

    def _route(route):
        try:
            req = route.request
            if _should_abort(req.resource_type, req.url, saver["images"]):
                return route.abort()
            return route.continue_()
        except Exception:
            try:
                return route.continue_()
            except Exception:
                pass

    context.route("**/*", _route)


def launch_browser(proxy, headless: bool):
    """Devuelve un context manager Camoufox EFÍMERO (anti-detect Firefox): huella de
    dispositivo real y NUEVA por lanzamiento; con geoip deriva tz/locale/geo/WebRTC de
    la IP del proxy. Sin user_data_dir -> el perfil se descarta al cerrar (no se conserva).
    Import perezoso: el selftest corre sin Camoufox instalado.
    headless sin GUI no resuelve CAPTCHA/OTP -> el primer login de cada cuenta va headful."""
    from camoufox.sync_api import Camoufox
    return Camoufox(
        headless=headless,
        proxy=proxy,                       # dict Decodo o None (directo)
        geoip=bool(proxy),                 # tz/locale/geo/WebRTC auto desde la IP del proxy
        humanize=0.8,                      # curva de cursor humana nativa, ≤0.8s por movimiento
        os=["macos", "windows", "linux"],  # pool de OS para la huella
    )


def run_account(account: dict, proxy, headless: bool, skip_add: bool, hold: bool) -> int:
    """Flujo completo de UNA cuenta en navegador EFÍMERO Camoufox: warmup multi-sitio +
    Amazon -> login -> /a/addresses -> alta ÚNICA -> cosecha cookies en cookies/<slug>.txt.
    Devuelve rc 0 ok / 1 alta falló / 2 necesita humano (CAPTCHA/OTP). Cierra siempre
    (el `with` descarta el navegador efímero al salir)."""
    email, password = account["email"], account["password"]
    slug = account_slug(email)
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\n=== Cuenta: {email} ===")
    if proxy:
        print(f"🌐 proxy: {proxy['server']}")
    with launch_browser(proxy, headless) as browser:
        page = browser.new_page()
        # Un proxy lento NO debe colgar una navegación indefinidamente: tope global de 20s
        # (los timeouts explícitos más largos siguen ganando donde hagan falta).
        page.set_default_navigation_timeout(20000)
        page.set_default_timeout(15000)
        # Locale/timezone que geoip derivó de la IP del proxy. Si NO es en-US/US, el proxy
        # salió por otro país (p.ej. fr-FR/Europe/Paris = Francia) -> Amazon servirá ese país
        # y el alta de dirección US fallará. Señal directa para detectar un proxy no-US.
        try:
            geo = page.evaluate("() => ({lang: navigator.language, "
                                "tz: Intl.DateTimeFormat().resolvedOptions().timeZone})")
            flag = "" if str(geo["lang"]).lower().startswith("en-us") else "  ⚠️ NO-US"
            print(f"🌍 locale/tz del proxy: {geo['lang']} / {geo['tz']}{flag}")
        except Exception:
            pass
        # Cachear el viewport AQUÍ, con la página aún en blanco (calma) -> _vp deja de hacer
        # page.evaluate por-movimiento en el warmup (ese evaluate sin timeout se colgaba
        # detrás del JS anti-bot tras el muro 'Continue shopping'). Por cuenta -> correcto en --all.
        _prime_viewport(page)
        page.context.add_init_script(WEBAUTHN_NEUTER)  # passkey auto-cancel, antes de navegar
        saver = {"images": True}  # warmup sin imágenes (ahorro); se flipea antes del login
        _install_saver_routes(page.context, saver)

        # Sembrar historial neutral + calentar Amazon (clave anti-CAPTCHA). AMZ_WARMUP regula
        # intensidad/velocidad; 'off' salta el warmup y va directo al login.
        prof = _warmup_profile()
        if prof["mode"] != "off":
            _warmup_web(page, prof)
            _warmup(page, prof)

        saver["images"] = False  # login/CAPTCHA/form: dejar cargar imágenes (CAPTCHA visible)
        page.goto(ADDRESSES_URL, wait_until="domcontentloaded")

        # Login SÓLO si Amazon nos manda a signin. Best-effort: si está el form clásico
        # lo completa; si Amazon muestra passkey / "continuar como X" / captcha (variantes
        # SIN #ap_email), NO fuerza (forzar dispara más detección y antes crasheaba con
        # Timeout 30s) -> cae al login manual en la ventana visible.
        if "signin" in page.url or page.locator("#ap_email, #ap_email_login").count():
            try:
                email_box = page.locator("#ap_email, #ap_email_login").first
                email_box.wait_for(state="visible", timeout=8000)
                _human_type(page, email_box, email)
                cont = page.locator("#continue")
                if cont.count():  # botón sólo en el flujo nuevo
                    _human_click(page, cont.first)
            except Exception as e:
                print("ℹ️  paso de email omitido (passkey/cuenta recordada):", e)
            try:
                pw = page.locator("#ap_password").first
                pw.wait_for(state="visible", timeout=8000)
                _human_type(page, pw, password)
                _human_click(page, page.locator("#signInSubmit").first)
            except Exception as e:
                print("ℹ️  paso de contraseña omitido (passkey/captcha/variante):", e)
            # El passkey se auto-cancela (WEBAUTHN_NEUTER) y _settle_after_login salta
            # los interstitiales (teléfono, "guardar passkey"). SOLO CAPTCHA/OTP queda
            # para el humano -> resuélvelo en la ventana (hasta 3 min, headful).
            print("⏳ Passkey auto-cancelado. Si sale CAPTCHA o código, resuélvelo en la ventana...")
            _settle_after_login(page, timeout_s=180)

        # Tras login Amazon a veces interpone otro prompt (teléfono, passkey). Saltar.
        _dismiss_interstitials(page)

        # Ir a Mis Direcciones; reintenta la navegación si falla.
        for _ in range(2):
            try:
                page.goto(ADDRESSES_URL, wait_until="domcontentloaded")
                break
            except Exception as e:
                print("nav falló, reintento:", e)

        # Popup de país ("Estás en Amazon.com / ¿desde México?") -> quedarse en .com
        click_if_present(page, "text=Quedarse en Amazon.com")
        _continue_shopping(page)  # por si el muro anti-bot sale post-login

        # El proxy free-trial a veces dropea CSS/recursos -> página a medias. Esperar 'load'
        # y, si estando en direcciones falta el tile de alta, un reload de cortesía.
        try:
            page.wait_for_load_state("load", timeout=15000)
        except Exception:
            pass
        if "/a/addresses" in page.url and not page.locator("#ya-myab-address-add-link").count():
            try:
                page.reload(wait_until="domcontentloaded")
            except Exception:
                pass

        rc = 2  # default: no llegamos a direcciones -> necesita humano (CAPTCHA/OTP)
        if "/a/addresses" in page.url:
            print("✅ En Mis Direcciones:", page.url)
            added = None
            if not skip_add:
                try:
                    added = add_random_address(page)
                except Exception as e:
                    print("⚠️  fallo al agregar dirección:", e)
            if added:
                # Cookies SOLO tras alta exitosa -> archivo. El navegador es efímero
                # (se descarta al cerrar), así que no hay perfil que limpiar después.
                dump_cookies(page.context, str(COOKIES_DIR / f"{slug}.txt"))
                rc = 0
            elif skip_add:
                rc = 0  # solo login pedido (--no-add): llegar a direcciones ya es éxito
            else:
                print("⚠️  sin alta exitosa -> NO se guardan cookies")
                rc = 1
        else:
            print("⚠️  URL actual:", page.url, "(¿CAPTCHA/paso extra?)")
            print("   headless no resuelve CAPTCHA/OTP." if headless
                  else "   resuélvelo en la ventana abierta.")

        page.screenshot(path=str(COOKIES_DIR / f"{slug}.png"), full_page=True)
        if hold and not headless:  # solo single-account headful: inspección manual
            try:
                input("Enter para cerrar el navegador...")
            except EOFError:
                page.wait_for_timeout(90_000)  # sin stdin: mantén ventana 90s visible
        return rc


def main() -> int:
    load_dotenv(Path(__file__).with_name(".env"))
    accounts = load_accounts()
    if not accounts:
        sys.exit("No hay cuentas: crea accounts.txt (email:password por línea) "
                 "o pon AMZ_EMAIL/AMZ_PASSWORD en .env")

    headless = "--headless" in sys.argv or os.environ.get("HEADLESS") == "1"
    skip_add = "--no-add" in sys.argv
    run_all = "--all" in sys.argv
    selected = None
    if "--account" in sys.argv:
        i = sys.argv.index("--account")
        selected = sys.argv[i + 1] if i + 1 < len(sys.argv) else None

    if run_all:
        targets = accounts
    elif selected:
        targets = [a for a in accounts if a["email"] == selected]
        if not targets:
            sys.exit(f"Cuenta no encontrada (accounts.txt / .env): {selected}")
    else:
        targets = accounts[:1]  # default: primera (compat single-account)

    # En --all no bloqueamos por cada cuenta (correría desatendido); en single sí.
    hold = not run_all

    # Proxy por LRU + enfriamiento: cada corrida toma el puerto MENOS-recientemente usado;
    # si aún no cumplió 10 min, espera -> al reusar el puerto su IP sticky ya rotó = IP
    # fresca. proxies.txt tiene precedencia; sin él, AMZ_PROXY ({session}) como fallback.
    proxies = load_proxies()
    state = load_proxy_state()
    if proxies:
        print(f"🌐 {len(proxies)} proxies (LRU + enfriamiento 10 min)")

    worst = 0
    for account in targets:
        email = account["email"]
        if proxies:
            proxy, wait = pick_proxy(proxies, state, time.time())
            if wait > 0:
                print(f"⏳ esperando {int(wait)}s a que rote la IP de {proxy['server']}...")
                time.sleep(wait)
            state[proxy["server"]] = time.time()
            save_proxy_state(state)
        else:
            proxy = proxy_for(email)  # fallback single-proxy (.env AMZ_PROXY) o None
        try:
            rc = run_account(account, proxy, headless, skip_add, hold)
        except Exception as e:
            print("❌ error en la cuenta", email, "->", e)
            rc = 2
        worst = max(worst, rc)  # 0 ok < 1 alta falló < 2 necesita humano
    return worst


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
    assert WARMUP_SEARCHES, "WARMUP_SEARCHES vacío -> random.choice fallaría"
    # unicidad dura: 300 direcciones, todas con fingerprint distinto y persistido;
    # ningún reroll devuelve una repetida ni siquiera contra el archivo creciente.
    seen_file = Path(tempfile.mkdtemp()) / "used.txt"
    fps = set()
    for _ in range(300):
        a = unique_address(seen_file)
        assert a["_fp"] not in fps, ("dirección repetida!", a)
        fps.add(a["_fp"])
        _record_address(a, seen_file)
    assert len(seen_file.read_text().split()) == 300
    # el init script anti-passkey es load-bearing: que nadie lo vacíe sin querer.
    assert "navigator.credentials" in WEBAUTHN_NEUTER and "conditional" in WEBAUTHN_NEUTER
    # la capa conductual + Camoufox/form (browser-only, no testeable headless-less aquí).
    assert all(callable(f) for f in (_vp, _prime_viewport, _wander, _human_scroll, _human_click, _human_type,
                                     _human_move, _idle, _warmup_search, _install_saver_routes,
                                     launch_browser, _select_state, _fill_and_submit_address,
                                     _warmup_profile, _await_address_ok, _address_listed,
                                     _continue_shopping))
    # _human_type acepta typos (numéricos lo desactivan para no corromper el dígito).
    import inspect
    assert "typos" in inspect.signature(_human_type).parameters
    # alta: 3 intentos por defecto (reroll barato gracias al fast-fail de validación).
    assert inspect.signature(add_random_address).parameters["attempts"].default == 3
    # warmup configurable por AMZ_WARMUP (light default / full / off; inválido -> light).
    os.environ.pop("AMZ_WARMUP", None)
    assert _warmup_profile()["mode"] == "light", "default debe ser light"
    os.environ["AMZ_WARMUP"] = "off"
    assert _warmup_profile()["mode"] == "off"
    os.environ["AMZ_WARMUP"] = "FULL"
    assert _warmup_profile()["mode"] == "full"
    os.environ["AMZ_WARMUP"] = "garbage"
    assert _warmup_profile()["mode"] == "light", "valor inválido cae a light"
    os.environ.pop("AMZ_WARMUP", None)
    # CITIES: zip de 5 dígitos + state abbr de 2 letras (combos consistentes, residenciales).
    for city, abbr, name, zipc in CITIES:
        assert len(zipc) == 5 and zipc.isdigit(), (city, zipc)
        assert len(abbr) == 2 and abbr.isalpha(), (city, abbr)

    # --- ahorro de datos: _should_abort (puro) ---
    assert _should_abort("media", "https://x.com/v.mp4", False) is True
    assert _should_abort("font", "https://x.com/f.woff2", False) is True
    assert _should_abort("image", "https://amazon.com/t.jpg", True) is True    # warmup
    assert _should_abort("image", "https://amazon.com/t.jpg", False) is False  # login -> CAPTCHA visible
    assert _should_abort("script", "https://www.google-analytics.com/a.js", False) is True  # tracker
    assert _should_abort("document", "https://www.amazon.com/", True) is False
    assert _should_abort("xhr", "https://www.amazon.com/api", True) is False

    # --- multi-cuenta: parser de accounts.txt (password con ':' intacto) ---
    acc_file = Path(tempfile.mkdtemp()) / "accounts.txt"
    acc_file.write_text("# comment\na@b.com:pw:with:colons\n  c@d.com : secret \n\nbadline\n")
    accs = load_accounts(acc_file)
    assert accs == [{"email": "a@b.com", "password": "pw:with:colons"},
                    {"email": "c@d.com", "password": "secret"}], accs
    # archivo ausente -> cae a AMZ_EMAIL/AMZ_PASSWORD del entorno (compat single)
    fb = load_accounts(Path(tempfile.mkdtemp()) / "nope.txt")
    assert fb == [{"email": "a@b.com", "password": "pw=secret"}], fb
    # slug filesystem-safe y estable
    assert account_slug("A.B+tag@Gmail.com") == "a_b_tag_gmail_com", account_slug("A.B+tag@Gmail.com")
    assert account_slug("@@@") == "default", account_slug("@@@")
    # proxy: sustituye {session} (estable por email) y parsea user/pass/host/port
    os.environ["AMZ_PROXY"] = "http://user-{session}:p@ss@gate.example.com:7777"
    pr1, pr2 = proxy_for("x@y.com"), proxy_for("x@y.com")
    assert pr1 == pr2, ("proxy debe ser determinístico por email", pr1, pr2)
    assert pr1["server"] == "http://gate.example.com:7777", pr1
    assert pr1["username"].startswith("user-") and pr1["password"] == "p@ss", pr1
    assert proxy_for("z@y.com")["username"] != pr1["username"], "session token varía por email"
    os.environ.pop("AMZ_PROXY")
    assert proxy_for("x@y.com") is None, "sin AMZ_PROXY -> None"
    # --- enfriamiento de proxy (LRU + cooldown), puro y testeable con `now` inyectado ---
    pr = [{"server": "http://h:10001"}, {"server": "http://h:10002"}, {"server": "http://h:10003"}]
    st = {"http://h:10001": 1000.0, "http://h:10002": 100.0}
    pk, w = pick_proxy(pr, st, now=1200.0, cooldown=600)
    assert pk["server"] == "http://h:10003" and w == 0, (pk, w)  # nunca usado -> LRU, sin espera
    pk2, w2 = pick_proxy([{"server": "http://h:10001"}], {"http://h:10001": 1000.0},
                         now=1300.0, cooldown=600)
    assert pk2["server"] == "http://h:10001" and w2 == 300, (pk2, w2)  # usado hace 300s -> espera 300
    assert pick_proxy([], {}, now=0.0) == (None, 0)

    # --- pool de proxies (formato Decodo host:port:user:pass) ---
    assert proxy_from_line("gate.decodo.com:10001:spvvxp149l:r10pSBdhtc4swMJ3o+") == {
        "server": "http://gate.decodo.com:10001",
        "username": "spvvxp149l", "password": "r10pSBdhtc4swMJ3o+",
    }, proxy_from_line("gate.decodo.com:10001:spvvxp149l:r10pSBdhtc4swMJ3o+")
    assert proxy_from_line("h:80:u:p:a:ss")["password"] == "p:a:ss"  # ':' en pass (maxsplit=3)
    assert proxy_from_line("badline") is None
    prox_file = Path(tempfile.mkdtemp()) / "proxies.txt"
    prox_file.write_text("# c\ngate.decodo.com:10001:u:p\ngate.decodo.com:10002:u:p\n\n")
    pool = load_proxies(prox_file)
    assert len(pool) == 2 and pool[0]["server"].endswith(":10001") \
        and pool[1]["server"].endswith(":10002"), pool
    print("selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        raise SystemExit(main())
