---
title: 'Tres secciones de respuestas: Completa (todo) + Filtrada con/sin response'
type: 'feature'
created: '2026-06-12'
status: 'done'
baseline_commit: '3aa5e5fa25e54856632e554328a6bee382331a8d'
context: ['{project-root}/_bmad-output/project-context.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Hoy `completa.txt` solo guarda las respuestas clasificadas ✅, pero como el detector es `"✅" in texto`, las declinadas que traen un ✅ en algún campo también caen ahí (Richard ve "todas" en el historial — clasificación errada). Y solo existe una sección filtrada, que guarda únicamente el dato `CC:`, sin el texto completo de la respuesta aprobada.

**Approach:** Redefinir tres secciones (vivo + historial): **Completa** = guarda TODO resultado definitivo (✅ y ❌) sin filtrar; **Filtrada con response** = solo ✅, texto completo (archivo nuevo `filtrada_completa.txt`); **Filtrada sin response** = solo ✅, solo dato `CC:` (la actual `filtrada.txt`, renombrada en la UI).

## Boundaries & Constraints

**Always:**
- `core.py` puro (sin Telethon ni I/O de terminal); estado per-instancia en `Sesion`; escrituras append igual que hoy (`[ts] texto\n\n`).
- Dedup de `CC:` por sesión intacto: `filtrada.txt` y `cargar_cc_existentes()` siguen siendo la fuente del dedup (`continuar=True` no cambia).
- `_ultima` symlink + `mkdir` lazy se disparan en el **primer guardado de cualquier tipo** (incluida una ❌ que solo va a Completa).
- Naming en español; estilo y 4 espacios; sin deps nuevas; sin build.

**Ask First:**
- Llevar el CLI (`auto_sender.py`) a paridad total (que su `completa.txt` también incluya ❌). Default: NO — el CLI es legacy y queda con su comportamiento actual vía wrapper de compatibilidad.

**Never:**
- Leer el contenido de `respuestas/` (solo estructura/paths).
- Guardar intermedios ⏳ (sin ✅ ni ❌) en ninguna sección — no son un resultado.
- Tocar el dedup de líneas de envío, rate-limiting, o comandos sobre `/ws`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Respuesta ✅ nueva | bot manda texto con ✅ | append a `completa.txt` + `filtrada_completa.txt` (texto completo) + CC nuevos a `filtrada.txt`; emite `respuesta estado=ok`; recibidas++ | N/A |
| Respuesta ❌ nueva | bot manda texto con ❌ | append SOLO a `completa.txt`; emite `respuesta estado=rechazada`; rechazadas++ | N/A |
| Edit ⏳→✅ | ⏳ luego editado a ✅ | el ⏳ no guarda nada; el ✅ guarda en las tres como ✅ nueva | N/A |
| Edit ✅→❌ | ✅ ya guardado, editado a ❌ | append del texto ❌ a `completa.txt`; NO re-guarda filtradas; recibidas--, rechazadas++ | N/A |
| Edit ✅→✅ (mismo msg, texto cambia) | nueva revisión ✅ | append a `completa.txt` + `filtrada_completa.txt`; CC solo si hay nuevos; emite `estado=ok-edit` | N/A |
| Edit idéntico | `previo["texto"] == texto` | return temprano, no guarda ni emite | N/A |
| Intermedio ⏳ puro | texto sin ✅/❌, sin estado previo definitivo | no guarda ni emite | N/A |
| GET tipo inválido | `/api/respuesta/..?tipo=xxx` | HTTP 400 | mensaje claro |

</frozen-after-approval>

## Code Map

- `core.py` -- `Sesion.guardar_respuesta` se parte en `guardar_completa(texto)` (append a `completa.txt`) y `guardar_filtrada(texto)` (append a `filtrada_completa.txt` + CC nuevos a `filtrada.txt`, devuelve nuevos). `guardar_respuesta` queda como wrapper de compatibilidad (`guardar_completa` + `return guardar_filtrada`). Factor común `_preparar()` = mkdir + symlink.
- `app.py` -- `Engine._manejar_bot`: para todo resultado definitivo llama `guardar_completa`; en ✅ además `guardar_filtrada`; emite un evento `respuesta` por cada resultado (ok / ok-edit / rechazada). `/api/respuesta` valida `tipo in (completa, filtrada, filtrada_completa)`.
- `static/index.html` -- tercera columna en panel vivo e historial; labels "Completa" / "Filtrada con response" / "Filtrada sin response"; `agregarRespuesta` reparte; `histPane`/fetch/`.split` a 3 columnas.
- `auto_sender.py` -- sigue usando `guardar_respuesta` (wrapper); comportamiento sin cambios. No se edita.

## Tasks & Acceptance

**Execution:**
- [x] `core.py` -- partir `guardar_respuesta` en `guardar_completa` + `guardar_filtrada` con `_preparar()` compartido; `filtrada_completa.txt` recibe el texto completo con timestamp; mantener wrapper `guardar_respuesta` para el CLI -- separar "guardar todo" de "guardar solo ✅".
- [x] `app.py` -- reescribir `_manejar_bot`: tras dedup, si `estado_nuevo not in (ok, rechazada)` return; `guardar_completa` siempre; en ok `guardar_filtrada` y `nuevos`; emitir `respuesta` por cada resultado con `estado` ∈ {ok, ok-edit, rechazada}; counters como hoy. Añadir `filtrada_completa` a la validación de `tipo` en `/api/respuesta` -- Completa captura todo, filtradas solo ✅.
- [x] `static/index.html` -- agregar 3ª `split-col` en panel vivo (`#respuestasFiltradaCompleta`) y en historial (`#histFiltradaCompleta` con Copiar/Exportar `data-*="filtrada_completa"`); relabelar "Filtrada"→"Filtrada sin response", añadir "Filtrada con response"; `agregarRespuesta`: Completa siempre, con-response solo si `estado` empieza con "ok", sin-response = CC; extender `histPane` + `cargarArchivo` (3 fetch); `.split` a `repeat(3, minmax(0,1fr))` -- exponer las tres secciones.

**Acceptance Criteria:**
- Given una respuesta ❌, when llega al Engine, then aparece en Completa (vivo) y en `completa.txt`, y NO en ninguna sección filtrada.
- Given una respuesta ✅, when llega, then aparece en las tres secciones; `filtrada_completa.txt` tiene el texto completo y `filtrada.txt` solo el `CC:`.
- Given una sesión continuada (`continuar=True`), when llegan ✅ repetidos, then el dedup de `CC:` en `filtrada.txt` se preserva (sin duplicar), y `filtrada_completa.txt` registra cada revisión.
- Given el historial de una sesión, when toco Exportar en "Filtrada con response", then descarga `<pref>_<ses>_filtrada_completa.txt` con el contenido del pane.

## Verification

**Commands:**
- `python -c "import core, app"` -- expected: importa sin error (core puro, app carga).

**Manual checks:**
- `python app.py`, enviar un lote; confirmar que Completa lista ✅ y ❌, "Filtrada con response" solo ✅ con texto completo, "Filtrada sin response" solo el `CC:`; en Historial los tres panes cargan y Copiar/Exportar funcionan en el nuevo.

## Suggested Review Order

**Modelo de guardado (núcleo)**

- Entry point: el split que define las tres secciones — `completa` = todo, `filtrada` = solo ✅ (texto + CC).
  [`core.py:142`](../../core.py#L142)

- La respuesta ✅ completa va al archivo nuevo `filtrada_completa.txt`; el dedup de CC sigue contra `filtrada.txt`.
  [`core.py:149`](../../core.py#L149)

- Wrapper de compatibilidad: el CLI no se toca y mantiene su flujo.
  [`core.py:170`](../../core.py#L170)

**Dispatch del Engine (qué se guarda y se emite)**

- Corte de intermedios ⏳: solo resultados definitivos (✅/❌) guardan o emiten.
  [`app.py:347`](../../app.py#L347)

- Completa captura TODO resultado; solo ✅ además llama `guardar_filtrada`.
  [`app.py:352`](../../app.py#L352)

**API de historial**

- `tipo` acepta `filtrada_completa` → sirve `filtrada_completa.txt` (traversal-safe por `_safe_dir`).
  [`app.py:572`](../../app.py#L572)

**Binding de UI**

- `agregarRespuesta` reparte: Completa siempre, "con response" solo ✅, "sin response" = CC.
  [`index.html:346`](../../static/index.html#L346)

- Historial: tercer fetch + mapa de panes para Copiar/Exportar de la nueva sección.
  [`index.html:455`](../../static/index.html#L455)

- CSS: el grid `.split` pasa a 3 columnas (vivo e historial).
  [`index.html:68`](../../static/index.html#L68)
