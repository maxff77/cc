---
title: 'Rediseñar paneles COMPLETA/FILTRADA con HeroUI Card + tope de ancho'
type: 'refactor'
created: '2026-06-12'
status: 'done'
baseline_commit: '52161145852f7b1aa5c3a663437e0208e2b14234'
context: ['{project-root}/_bmad-output/project-context.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Los paneles de respuestas COMPLETA/FILTRADA en Envío (y en el detalle de Historial) son `<section>` planos hechos a mano que se leen como filas "inline" sueltas y, dentro de la grilla `300px 1fr 1fr`, se desbordan por el borde derecho de la pantalla: los `<div>` hijos de grid tienen `min-width:auto`, así que el texto mono largo de cada fila estira la columna más allá de `1fr` y el `truncate` nunca recorta. En pantallas anchas el tablero además se estira sin tope.

**Approach:** Reconstruir el contenedor de panel con el componente **HeroUI `Card`** (compound: `Card` + `Card.Header`/`Card.Title`/`Card.Content`/`Card.Footer`, `variant="default"`), aprovechando su superficie elevada (`bg-surface` + `shadow-surface`) en lugar del borde plano. Corregir el desborde con `min-w-0` en la raíz del Card (+ pistas `minmax(0,1fr)` en las grillas consumidoras) y poner un tope de ancho centrado al contenido de ambas páginas. Las filas siguen **truncadas a una línea** (sin cambios en `DataRow`).

## Boundaries & Constraints

**Always:** Usar SOLO componentes HeroUI para el chrome del card (decisión explícita del owner). Preservar el contrato de props de `CompletaPanel`/`FiltradaPanel`/`ResponseTabs` — son props-driven y se reusan VERBATIM en Envío y en el detalle de Historial (Story 3.2/3.3). Conservar: scroll interno con auto-pin al fondo (`PanelList`), badge de conteo visible aun en 0, link de export `↓ .txt` (solo si hay `exportPath`), empty-states verbatim, glifos ✅/❌ y el resaltado "nueva". El `truncate` de fila sigue intacto.

**Ask First:** Cambiar el comportamiento de truncado→wrap; tocar `DataRow`/`CountBadge`/`ExportLink`; convertir a HeroUI algo que no sea el contenedor de panel; cambiar el valor del tope de ancho si 1600px resulta inadecuado en revisión visual.

**Never:** No leer ni mostrar contenido de `respuestas/`. No tocar backend, WS store (`lib/ws`), ni endpoints. No introducir deps nuevas (HeroUI ya está). No agregar build step ni tests nuevos. No reintroducir `SectionCard` para estos paneles.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fila mono larga (CC + bank + type) | `text` excede el ancho de columna | Card permanece dentro de su track `1fr`; texto truncado con `…`; sin scroll horizontal de página | N/A |
| Pantalla ultra-ancha (≫1600px) | viewport 2000px | Contenido (cockpit + 2 paneles) topado y centrado; sin estiramiento infinito | N/A |
| Sin respuestas / sin CC | `responses=[]` / `cc=[]` | Card renderiza con header, badge en 0 y empty-state verbatim | N/A |
| Mobile (`<lg`) | `ResponseTabs`, `header={false}` | Card sin `Card.Header` dentro de cada `Tabs.Panel`; lista con `max-h-72` y scroll | N/A |
| Export en progreso/falla | click `↓ .txt` | Footer del Card muestra "Descargando…"/error local; el panel no se rompe | error local, no propaga |

</frozen-after-approval>

## Code Map

- `frontend/components/sessions/response-views.tsx` -- contiene `ResponsePanel` (wrapper a reescribir), `CompletaPanel`/`FiltradaPanel`/`ResponseTabs` (firmas intactas), `PanelList`/`ExportLink`/`CountBadge` (sin cambios).
- `frontend/app/(client)/page.tsx` -- Envío: grilla `lg:grid-cols-[300px_1fr_1fr]`, renderiza los dos paneles desktop + `ResponseTabs` mobile.
- `frontend/app/(client)/sessions/[id]/page.tsx` -- detalle Historial: grilla `lg:grid-cols-2`, reusa los mismos paneles + tabs.
- `@heroui/react` `Card` -- compound `Card.Root/Header/Title/Content/Footer`; `.card` = `flex flex-col gap-3 p-4 shadow-surface` radio ~32px, `--default` = `bg-surface` sin borde. Capa `utilities` gana sobre `components`, así que overrides Tailwind por-clase aplican.

## Tasks & Acceptance

**Execution:**
- [x] `frontend/components/sessions/response-views.tsx` -- Reescribir `ResponsePanel`: cambiar `<section>`→`<Card variant="default">` con `className` raíz `flex min-w-0 flex-col overflow-hidden rounded-lg p-0 gap-0` (más el `className` del consumidor); `<header>`→`<Card.Header className="flex-row items-center justify-between gap-2 px-3 py-2 border-b border-border">` con `<Card.Title>` estilizado como label-caps (`text-[10px] font-medium uppercase tracking-[0.12em] text-muted`) + `CountBadge`; envolver `PanelList` en `<Card.Content className="min-h-0 p-0">`; `<footer>`→`<Card.Footer className="justify-between border-t border-border px-3 py-2">`. Header/footer condicionales como hoy. Importar `Card` de `@heroui/react`.
- [x] `frontend/app/(client)/page.tsx` -- Tope de ancho: añadir `mx-auto w-full max-w-[1600px]` al `<div>` raíz de la grilla y cambiar pistas a `lg:grid-cols-[300px_minmax(0,1fr)_minmax(0,1fr)]`.
- [x] `frontend/app/(client)/sessions/[id]/page.tsx` -- Tope de ancho: añadir `mx-auto w-full max-w-[1600px]` al `<div>` raíz (`flex flex-col gap-5`) y cambiar la grilla desktop a `lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]`.

**Acceptance Criteria:**
- Given una sesión con filas largas en una pantalla ancha, when se renderiza Envío, then cada panel aparece como un Card HeroUI contenido (borde `border-border` + `bg-surface`), las filas se truncan dentro del Card y no hay scroll horizontal de página.
- Given un viewport ≫1600px, when se carga Envío o el detalle de Historial, then el contenido queda centrado y topado a 1600px.
- Given mobile (`<lg`), when se ven las tabs, then cada panel es un Card sin header (el strip de tabs lleva label+badge) con scroll interno.
- Given el detalle de Historial, when se renderiza, then usa los mismos Cards sin cambios de props ni regresiones de export/continuar.

## Spec Change Log

- **Review iter 1 (patch):** Revisión adversaria (3 lentes + verificación) levantó 10 hallazgos; 2 confirmados, ambos `patch` (sin loopback). (1) **Regresión dark-mode** — `variant="default"` no trae borde y depende de `shadow-surface`, pero el tema dark (default de la app, `<html className="dark">`) anula `--surface-shadow` a transparente → el panel perdía toda contención. **Fix:** se añadió `border border-border` a la raíz del Card. (2) **Stretch de grid** — `align-items:stretch` estiraba los paneles a la altura del cockpit sticky (más alto) dejando superficie vacía. **Fix:** `lg:items-start` en las grillas de Envío y del detalle. KEEP: estructura Card compound, `min-w-0` (fix de overflow), `max-w-[1600px]`, truncado.

## Design Notes

`Card` trae `flex flex-col` + `shadow-surface` + `bg-surface`; lo neutralizamos a densidad consola con `p-0 gap-0 overflow-hidden rounded-lg` (capa utilities gana sobre la capa components de `.card`) y devolvemos el padding/separadores por slot (`px-3 py-2`, `border-b/-t border-border`). **El borde exterior `border border-border` es explícito**: `.card` confía en `shadow-surface` para su borde, pero el tema dark anula `--surface-shadow`, así que sin el borde el panel no tendría contención contra `bg-background`. `min-w-0` en la raíz es lo que realmente arregla el desborde de grid (el `minmax(0,1fr)` es refuerzo a nivel de track). `lg:items-start` en las grillas evita que los paneles se estiren a la altura del cockpit.

Boceto del wrapper:
```tsx
<Card variant="default" className={clsx("flex min-w-0 flex-col gap-0 overflow-hidden rounded-lg border border-border p-0", className)}>
  {header && (
    <Card.Header className="flex-row items-center justify-between gap-2 border-b border-border px-3 py-2">
      <Card.Title className="text-[10px] font-medium uppercase tracking-[0.12em] text-muted">{header}</Card.Title>
      <CountBadge tone={countTone} value={count} />
    </Card.Header>
  )}
  <Card.Content className="min-h-0 p-0">
    <PanelList className={listClassName} emptyText={emptyText} rows={rows} />
  </Card.Content>
  {exportPath && (
    <Card.Footer className="justify-between border-t border-border px-3 py-2">
      <ExportLink path={exportPath} />
    </Card.Footer>
  )}
</Card>
```

## Verification

**Commands:**
- `cd frontend && npm run lint` -- expected: sin errores nuevos.
- `cd frontend && npx tsc --noEmit` -- expected: sin errores de tipos (Card/slots tipados).
- `cd frontend && npm run build` -- expected: build OK (compila las 3 páginas tocadas).

**Manual checks:**
- Envío en pantalla ancha: dos Cards contenidos lado a lado, filas truncadas, sin scroll horizontal; en ultrawide el set queda centrado.
- Mobile: tabs Completa|Filtrada, cada panel un Card con scroll interno.
- Detalle de Historial: mismos Cards, export `↓ .txt` y "Continuar" siguen funcionando.
- **Dark mode (default):** el borde `border-border` es visible (la sombra se anula en dark).

## Suggested Review Order

**El Card (núcleo del rediseño)**

- Punto de entrada: wrapper migrado a HeroUI Card; aquí viven el fix de overflow (`min-w-0`) y el borde dark-mode.
  [`response-views.tsx:205`](../../frontend/components/sessions/response-views.tsx#L205)

- Import del compound `Card`.
  [`response-views.tsx:18`](../../frontend/components/sessions/response-views.tsx#L18)

- Lista scrollable dentro de `Card.Content` (padding neutralizado, `min-h-0`).
  [`response-views.tsx:220`](../../frontend/components/sessions/response-views.tsx#L220)

- Footer de export como `Card.Footer`.
  [`response-views.tsx:228`](../../frontend/components/sessions/response-views.tsx#L228)

**Tope de ancho + grilla (anti-overflow / anti-stretch)**

- Envío: contenedor `max-w-[1600px]`, pistas `minmax(0,1fr)`, `items-start`.
  [`page.tsx:54`](../../frontend/app/(client)/page.tsx#L54)

- Detalle: raíz topada a 1600px.
  [`[id]/page.tsx:232`](<../../frontend/app/(client)/sessions/[id]/page.tsx#L232>)

- Detalle: grilla de paneles `minmax(0,1fr)` + `items-start`.
  [`[id]/page.tsx:271`](<../../frontend/app/(client)/sessions/[id]/page.tsx#L271>)

- Detalle: skeleton de carga también topado (evita salto de layout).
  [`[id]/page.tsx:179`](<../../frontend/app/(client)/sessions/[id]/page.tsx#L179>)
