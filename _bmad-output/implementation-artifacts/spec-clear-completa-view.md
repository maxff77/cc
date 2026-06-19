---
title: 'Botón "Limpiar" para la vista Completa del cockpit'
type: 'feature'
created: '2026-06-19'
status: 'done'
context: []
baseline_commit: 'b11a6b9a7770df3951b6209499a50cb9c5232ff8'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** En el cockpit (Envío), el panel **Completa** acumula cada revisión capturada (✅ y ❌) sin tope práctico durante una sesión larga. Llega a ser demasiado y estorba la lectura en vivo.

**Approach:** Agregar un botón **"Limpiar"** que vacíe **solo** la vista Completa, de forma **visual y local** (no borra nada de la DB). Los datos siguen en Postgres/Historial y reaparecen al recargar. Aprobadas y Datos CC **no** se tocan, aunque Aprobadas comparta el mismo origen (`responses[]`).

## Boundaries & Constraints

**Always:**
- Limpiar es **solo de vista, por pestaña**: nada de endpoints, nada de DELETE, no toca `responses`/`send_log` en backend.
- Afecta **únicamente** la vista Completa. Aprobadas (subconjunto ✅) y Datos CC quedan intactos visualmente.
- Las nuevas respuestas capturadas **después** de limpiar siguen apareciendo en Completa (clear ≠ pausa).
- `response-views.tsx` se reusa **verbatim** en Historial e in admin/tenants — las props nuevas son **opcionales**; sin ellas el panel se comporta igual que hoy (cero botón).

**Ask First:**
- Cualquier intención de persistir el clear (entre recargas/pestañas) o de borrar datos reales — eso fue descartado explícitamente.

**Never:**
- No borrar datos capturados (dato sensible 🔒). No agregar endpoint ni evento WS. No tocar `lib/ws.ts` reducer ni el store.
- No usar el icono `trash` (implica eliminación, que es falso). Texto "Limpiar", sin icono o con uno neutro.
- No clonar el clear a Aprobadas/Datos CC.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Limpiar con N filas visibles | Completa muestra N filas | Completa queda vacía (estado vacío "Aún no hay respuestas."); Aprobadas y Datos CC sin cambios; badge de Completa baja a las filas no ocultas | N/A |
| Nueva respuesta tras limpiar | llega `response.captured` | la fila aparece en Completa (y en Aprobadas si es ✅); badge de Completa sube | N/A |
| Recargar / reconectar tras limpiar | el `snapshot` reemplaza el store | Completa vuelve a mostrar las filas de la sesión (clear no persiste — no se borró nada) | N/A |
| Limpiar con 0 filas visibles | Completa vacía | botón deshabilitado / no-op | N/A |
| Historial detail y admin/tenants | no pasan `onClearCompleta` | no se renderiza botón; Completa igual que hoy | N/A |

</frozen-after-approval>

## Code Map

- `frontend/app/app/page.tsx` — Cockpit. Dueño del estado local `clearedKeys` (Set de `ResponseRow.key`); deriva las filas visibles de Completa y el total ajustado; cablea `onClearCompleta`.
- `frontend/components/sessions/response-views.tsx` — `ResponseColumns`/`ResponseTabs`/`CompletaPanel`/`ResponsePanel`. Suma props opcionales y el botón "Limpiar" en el footer del panel Completa.
- `frontend/lib/ws.ts` — referencia de `ResponseRow.key` (`l-<seq>` vivo / `s-<id>` snapshot). **Sin cambios** (el clear es local al componente; no toca store ni reducer).
- `frontend/components/sessions/response-row.tsx` — `DataRow` (sin cambios; referencia visual).

## Tasks & Acceptance

**Execution:**
- [x] `frontend/app/app/page.tsx` — Agregar `const [clearedKeys, setClearedKeys] = useState<Set<string>>(new Set())`. `handleClearCompleta = () => setClearedKeys(new Set(live.responses.map(r => r.key)))`. Memoizar `completaResponses = live.responses.filter(r => !clearedKeys.has(r.key))` y `completaTotal = Math.max(0, live.responsesTotal - (live.responses.length - completaResponses.length))`. Pasar `completaResponses`, `completaTotal`, `onClearCompleta={handleClearCompleta}` a `ResponseColumns` y `ResponseTabs`. **No** filtrar el prop `responses` (lo usa Aprobadas). — aísla el clear a Completa sin tocar el store.
- [x] `frontend/components/sessions/response-views.tsx` — En `ResponseViewsProps` y `CompletaPanel`: props opcionales `completaResponses?: ResponseRow[]`, `completaTotal?: number`, `onClearCompleta?: () => void`. Completa usa `completaResponses ?? responses` y badge `completaTotal ?? responsesTotal`. En `ResponsePanel`: prop opcional `onClear?` → renderizar footer cuando `exportPath || onClear`; layout `justify-between`: botón "Limpiar" (izq, estilo igual a `ExportLink`, mono ~11.5px, deshabilitado si la lista visible está vacía, `aria-label="Limpiar la vista Completa"`) y `↓ .txt` (der). Solo `CompletaPanel` recibe `onClear`; Aprobadas/CC nunca. — botón presentacional, retrocompatible.

**Acceptance Criteria:**
- Given Completa tiene filas en vivo, when hago clic en "Limpiar", then Completa queda vacía y Aprobadas y Datos CC conservan sus filas.
- Given limpié Completa, when se captura una respuesta nueva, then aparece en Completa (y en Aprobadas si es ✅).
- Given limpié Completa, when recargo la página, then reaparecen las filas previas (no se borró nada en backend).
- Given el detalle de Historial o la vista admin/tenants, when renderiza, then no se muestra el botón "Limpiar" (paneles reusados verbatim).
- Given Completa sin filas visibles, when renderiza, then "Limpiar" está deshabilitado.

## Design Notes

Patrón existente: `clearSession()` en `lib/ws.ts` ya hace un "clear visual confirmado por cliente". Aquí el clear es aún más liviano: **estado local del cockpit**, sin tocar el store (cero cambios en el reducer, cero riesgo para reconexión/Historial). Las keys (`l-<seq>`/`s-<id>`) son monótonas y cambian al reconectar/cambiar de sesión, así que el Set de keys ocultas **se autoinvalida** en el siguiente `snapshot`/`session.active` (las filas reaparecen) y queda acotado (≤ filas presentes al limpiar). El comentario de cabecera de `response-views.tsx` exige paneles "props-driven, sin leer store adentro" — respetarlo: el filtrado vive en el cockpit, el componente solo recibe datos y un callback.

## Verification

**Commands:**
- `cd frontend && npm run build` — expected: compila sin errores de tsc (gate obligatorio antes de push a main).
- `cd frontend && npm run lint` — expected: sin errores nuevos.

**Manual checks:**
- Correr cockpit, enviar un lote, esperar capturas. Clic en "Limpiar" → solo Completa se vacía; Aprobadas y Datos CC siguen con sus filas y badges. Llega una respuesta nueva → aparece en Completa. Recargar → las filas vuelven. Abrir un detalle de Historial → no hay botón "Limpiar".

## Suggested Review Order

**Estado del clear (entry point)**

- Corazón del diseño: clear local por pestaña, oculta keys visibles sin tocar el store.
  [`page.tsx:114`](../../frontend/app/app/page.tsx#L114)

- Patch de review: badge = filas visibles si hay algo oculto, si no el total autoritativo (sin números fantasma sobre lista vacía).
  [`page.tsx:132`](../../frontend/app/app/page.tsx#L132)

- Higiene: reset del set al cambiar de sesión.
  [`page.tsx:118`](../../frontend/app/app/page.tsx#L118)

**Aislamiento a Completa (lo que NO se toca)**

- Solo Completa recibe la lista filtrada + onClear; `responses` crudo sigue alimentando Aprobadas.
  [`response-views.tsx:399`](../../frontend/components/sessions/response-views.tsx#L399)

- Props nuevas opcionales ⇒ Historial/admin no pasan nada y quedan idénticos.
  [`response-views.tsx:354`](../../frontend/components/sessions/response-views.tsx#L354)

- Cableado del cockpit a ambos consumidores (columnas desktop + tabs móvil).
  [`page.tsx:209`](../../frontend/app/app/page.tsx#L209)

**UI del botón**

- Botón "Limpiar" sin icono trash (no borra), deshabilitado si la vista está vacía.
  [`response-views.tsx:159`](../../frontend/components/sessions/response-views.tsx#L159)

- Footer del panel: Limpiar a la izquierda, ↓ .txt a la derecha; otros paneles sin cambio.
  [`response-views.tsx:230`](../../frontend/components/sessions/response-views.tsx#L230)
