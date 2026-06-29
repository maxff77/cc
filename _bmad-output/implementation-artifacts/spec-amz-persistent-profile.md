---
title: 'Calentamiento humano + limpieza de cookies tras harvest en amazon_login.py'
type: 'feature'
created: '2026-06-25'
status: 'done'
context: []
baseline_commit: 'b19522e87005c781055d4104952c7e394a807ae4'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** El archivo committeado (211 líneas) YA usa perfil persistente (`launch_persistent_context` sobre `amazon_profile`), pero (1) va directo a `/a/addresses` sin ningún calentamiento → en frío fuerza el signin de inmediato sin historial = ruta máxima de CAPTCHA; y (2) tras guardar las cookies (`dump_cookies`) deja la sesión activa en el navegador, en vez de dejar el perfil "limpio y humano" reutilizable.

**Approach:** (A) Agregar un `_warmup(page)` que curiosea Amazon como humano (home, scroll, una búsqueda escrita a mano) antes de ir a direcciones, acumulando historia/caché en el perfil persistente. (B) Después de `dump_cookies(ctx)`, borrar las cookies DEL NAVEGADOR con `ctx.clear_cookies()` (limpieza LOCAL: las del archivo `amazon_cookies.txt` siguen válidas server-side), dejando un perfil consistente sin sesión activa. Mantener el perfil persistente tal cual está.

## Boundaries & Constraints

**Always:**
- Conservar el perfil persistente actual (`amazon_profile`, `launch_persistent_context`) sin cambios.
- `_warmup` totalmente envuelto en `try/except`: un selector ausente jamás rompe el login.
- Delays humanos vía `page.wait_for_timeout` (sin nuevo import `time`).
- `clear_cookies()` corre SOLO después de un `dump_cookies` exitoso (cookies ya en disco).

**Ask First:**
- Cualquier cambio que invalide server-side las cookies recién guardadas (p.ej. sign-out real de Amazon) — NO hacerlo: rompe el harvest.

**Never:**
- No reintroducir fingerprints / stealth pesado / perfiles por-email de la versión de 425 líneas revertida.
- No leer ni loguear valores de cookies/credenciales.
- No correr headless. No quitar los delays humanos.
- No cerrar sesión server-side (mataría las cookies del archivo).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Warmup OK | home + barra de búsqueda presentes | navega, scrollea, busca un término, vuelve a home, sigue al login | N/A |
| Selector de búsqueda ausente | Amazon cambió el DOM | imprime aviso y continúa al login igual | `except` → seguir |
| Harvest + limpieza | login OK, en `/a/addresses` | `dump_cookies` escribe archivo; luego `clear_cookies()` vacía el navegador | N/A |
| Perfil ya logueado | sesión persistida válida | warmup corre igual; no re-login; harvest + limpieza igual | N/A |

</frozen-after-approval>

## Code Map

- `amazon_login.py` :: constantes top -- agregar `HOME_URL` y `WARMUP_SEARCHES`.
- `amazon_login.py` :: `_pause` / `_warmup` (nuevas) -- helper de delay + calentamiento humano.
- `amazon_login.py` :: `main` -- llamar `_warmup(page)` antes del `goto(ADDRESSES_URL)`; agregar `ctx.clear_cookies()` tras `dump_cookies(ctx)`.
- `amazon_login.py` :: `_selftest` -- aserción de que `WARMUP_SEARCHES` no está vacío.

## Tasks & Acceptance

**Execution:**
- [x] `amazon_login.py` :: constantes -- agregar `HOME_URL = "https://www.amazon.com"` y `WARMUP_SEARCHES` (lista de términos benignos).
- [x] `amazon_login.py` :: `_pause`/`_warmup` -- helper `_pause(page, lo, hi)` con `page.wait_for_timeout(random.randint(...))`; `_warmup(page)` que va a home, scrollea, escribe una búsqueda en `#twotabsearchtextbox` + Enter, scrollea resultados, vuelve a home — todo en un `try/except` que solo avisa y sigue.
- [x] `amazon_login.py` :: `main` -- insertar `_warmup(page)` entre crear `page` y `page.goto(ADDRESSES_URL)`; tras `dump_cookies(ctx)` agregar `ctx.clear_cookies()` + print, con comentario ponytail del trade-off (borra también device-trust cookies).
- [x] `amazon_login.py` :: `_selftest` -- `assert WARMUP_SEARCHES`.

**Acceptance Criteria:**
- Given un perfil en frío, when corro `python amazon_login.py`, then antes de cualquier login el navegador visita home + hace una búsqueda (visible en la ventana) y recién después va a direcciones.
- Given que aterricé en `/a/addresses`, when se guardan las cookies, then `amazon_cookies.txt` queda escrito y acto seguido el navegador queda sin cookies (sesión local limpia).
- Given que Amazon cambió el selector de búsqueda, when corre el warmup, then imprime un aviso y el flujo de login continúa sin crashear.
- Given `--selftest`, when corro, then imprime `selftest OK` (sin red).

## Spec Change Log

- **2026-06-25 (review iter 1).** Findings (blind + edge hunters): (1) `ctx.clear_cookies()` borraba TODAS las cookies incluyendo device-trust (`ubid-main`/`session-id`) → cada corrida quedaba deslogueada y re-login = CAPTCHA, auto-saboteando la meta anti-CAPTCHA; (2) `Enter`+`wait_for_load_state("domcontentloaded")` = race que dispara scroll mid-navegación y aborta el warmup; (3) `Locator.type` deprecado. **Amendado:** clear_cookies ahora filtra por nombre solo cookies de auth (`at-main|sess-at-main|sst-main|session-token|x-main`), conservando device-trust; `wait_for_url("**/s?k=**")` en vez de `wait_for_load_state`; `press_sequentially` en vez de `type`. **Known-bad evitado:** perfil full-wipe que re-dispara CAPTCHA cada corrida; warmup crasheando mid-nav; ruptura futura por deprecación. **KEEP:** perfil persistente intacto; warmup en try/except best-effort; limpieza LOCAL (el dump sigue válido server-side). Pendiente confirmar con el humano si prefiere full-wipe.

## Design Notes

`clear_cookies()` es LOCAL: vacía el cookie jar del navegador pero NO invalida la sesión en los servidores de Amazon, así que las cookies ya volcadas a `amazon_cookies.txt` siguen siendo válidas para reuso. Un sign-out real de Amazon haría lo contrario (mataría el token) — por eso NO se usa.

Trade-off honesto: `clear_cookies()` también borra `ubid-main`/`session-id` (device-trust). Si el CAPTCHA reaparece, el upgrade es filtrar por nombre para conservarlas (`ctx.clear_cookies(name="at-main")`, etc.) — anotado como comentario `ponytail:` en el código.

Delays con `page.wait_for_timeout(ms)` (Playwright-native) en vez de `time.sleep` → no bloquea ni agrega import.

## Verification

**Commands:**
- `python amazon_login.py --selftest` -- expected: imprime `selftest OK`.
- `python -c "import ast; ast.parse(open('amazon_login.py').read())"` -- expected: sin error de sintaxis.

**Manual checks:**
- Correr `python amazon_login.py`: la ventana visita home y escribe una búsqueda ANTES de ir a direcciones; al terminar existe `amazon_cookies.txt` y el navegador queda deslogueado (recargar `/a/addresses` manda a signin).

## Suggested Review Order

**Calentamiento humano (anti-CAPTCHA)**

- Punto de entrada: el warmup se intercala antes de tocar el login.
  [`amazon_login.py:181`](../../amazon_login.py#L181)

- La función: home → scroll → búsqueda tecleada; `wait_for_url` evita el race mid-navegación.
  [`amazon_login.py:125`](../../amazon_login.py#L125)

- `press_sequentially` reemplaza el `Locator.type` deprecado.
  [`amazon_login.py:141`](../../amazon_login.py#L141)

**Harvest + cierre de sesión local**

- Tras volcar cookies, cierra sesión borrando SOLO auth y conserva device-trust (decisión revisada).
  [`amazon_login.py:227`](../../amazon_login.py#L227)

**Periféricos**

- Pool de términos benignos del warmup.
  [`amazon_login.py:25`](../../amazon_login.py#L25)

- Guard de selftest (sin red) para `WARMUP_SEARCHES`.
  [`amazon_login.py:259`](../../amazon_login.py#L259)
