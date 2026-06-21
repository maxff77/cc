---
title: 'Renombrar copy visible "Gate" → "Gateway"'
type: 'chore'
created: '2026-06-20'
status: 'done'
route: 'one-shot'
---

# Renombrar copy visible "Gate" → "Gateway"

## Intent

**Problem:** El UI (y los mensajes del backend) muestran la palabra "Gate"/"Gates" como etiqueta del catálogo de prefijos; el cliente la quiere ver como "Gateway"/"Gateways".

**Approach:** Reemplazo de **copy visible solamente** — texto JSX, props `label`/`placeholder`/`title`/`legend`/`heading`, toasts, mensajes `AppError`/`ValueError` en español, y el label del grupo nulo de Historial. **Cero** cambios a identificadores de código, rutas (`/admin/gates`, `/api/gates`), valores `code=` máquina (`gate_not_found`…), nombres de tabla/columna DB, logs, telemetría (`rx.send.gate`) ni comentarios. Sin migración (la palabra "gate" del dominio en código/DB queda intacta).

## Suggested Review Order

Orden por superficie (de mayor a menor densidad de cambios). Ctrl/Cmd+click para saltar.

1. [`../../frontend/app/admin/gates/page.tsx`](../../frontend/app/admin/gates/page.tsx) — catálogo admin: título, toasts (`creado`/`actualizado`/`eliminado`), labels "Gateway (comando real)", validaciones, headings crear/editar/eliminar, EmptyState, CTA "Crea tu primer gateway".
2. [`../../backend/app/errors.py`](../../backend/app/errors.py) — 8 mensajes `AppError` (`gate_exists`, `gate_not_found`, créditos, cookie-mode, límites). Los `code=` quedan iguales.
3. [`../../backend/app/api/admin.py`](../../backend/app/api/admin.py) — 3 `ValueError` de validación del valor del gateway.
4. [`../../backend/app/api/history.py`](../../backend/app/api/history.py) — solo `_NO_GATE_DISPLAY = "Sin gateway"` (label grupo nulo). El archivo trae WIP previo ajeno a este cambio.
5. [`../../frontend/components/batch/send-form.tsx`](../../frontend/components/batch/send-form.tsx) — chip "Gateway activo", label/placeholder del select, errores de selección y créditos.
6. [`../../frontend/components/landing/gates.tsx`](../../frontend/components/landing/gates.tsx) — h2 "Nuestros gateways", contador singular/plural, copy catálogo en preparación.
7. [`../../frontend/app/app/historial/page.tsx`](../../frontend/app/app/historial/page.tsx) — subtítulo, botón borrar por gateway, confirm.
8. [`../../frontend/components/batch/cookie-manager.tsx`](../../frontend/components/batch/cookie-manager.tsx) — legend, EmptyState y notice de tope de cookies.
9. Restantes (1 línea c/u): [`client-nav.tsx`](../../frontend/components/client-nav.tsx) · [`ui/admin-shell.tsx`](../../frontend/components/ui/admin-shell.tsx) (nav "Gateways") · [`app/page.tsx`](../../frontend/app/page.tsx) (meta description) · [`admin/tenants/[id]/page.tsx`](../../frontend/app/admin/tenants/[id]/page.tsx) (header tabla) · [`batch/progress-ring.tsx`](../../frontend/components/batch/progress-ring.tsx) · [`batch/verdict-timeout-notice.tsx`](../../frontend/components/batch/verdict-timeout-notice.tsx).

**Verificación:** `cd frontend && npm run build` → verde (tsc OK). Backend: `python -m py_compile` de los 3 módulos → OK. Review adversarial (subagente, sin contexto): sin identificadores/rutas/`code=`/logs mal tocados; 11 strings visibles faltantes detectados y corregidos en este mismo pase.
