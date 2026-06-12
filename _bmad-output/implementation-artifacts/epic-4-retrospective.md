# Retrospectiva — Epic 4: Protección operativa de la cuenta

**Fecha:** 2026-06-12 · **Stories:** 4-1 a 4-4 (4/4 done) · **Suite al cierre:** 275 tests

## Qué salió bien

- **Dev paralelo en worktrees funcionó:** 4-1, 4-2 y 4-4 se desarrollaron simultáneamente en worktrees aislados mientras epic 3 terminaba en el árbol principal — el merge posterior (cherry-picks + re-chain de migraciones Alembic + resolución de conflictos en models/errors/ws/page) tomó una fracción del tiempo que habría costado serializarlas.
- **Composición limpia de guardarraíles:** watchdog (4-1), admission control (4-2) y la ventana FloodWait global (2-5) conviven en `step()` con orden explícito y documentado: watchdog gate → admission sweep → ventana flood → selección. El orden mismo quedó como decisión registrada en el merge.
- **Estado durable correcto:** la pausa del watchdog sobrevive deploys (tabla `watchdog_state` restaurada en lifespan) — sin eso, el CI que deploya en cada push habría sido exactamente el "resume automático" que el AC 3 prohíbe.
- **4-4 entregó el runbook de re-auth un día antes de necesitarlo:** el procedimiento se ejecutó en producción (sesión @maxff9778) siguiendo en esencia lo que el runbook documenta.

## Qué salió mal / fricción

- **Migraciones huérfanas del mismo padre:** dos worktrees paralelos colgaron sus migraciones del mismo head → dos heads de Alembic al mergear. Resuelto con re-chain manual de `down_revision`, pero es un costo fijo del dev paralelo que hay que presupuestar.
- **Workflow de merge registrado en namespace equivocado** (cwd en `backend/` al lanzarlo) → quedó invisible e in-stoppable, murió huérfano a mitad de cherry-pick y el merge terminó haciéndose a mano. Lección operativa: verificar cwd antes de lanzar orquestación.
- **El `detail` del watchdog viaja a todos los tenants** en eventos/snapshot (texto de excepción Telethon). Decisión registrada como info operativa no sensible; si algún día lleva datos del owner, filtrarlo por rol.

## Lecciones / pendientes

1. Dev paralelo en worktrees: rentable con scopes disjuntos; presupuestar re-chain de migraciones y conflictos en archivos hub (ws.ts, page.tsx, models.py).
2. Pendiente humano único: smoke supervisado de los guardarraíles con Telegram real (matar la sesión a mitad de lote → banner + pausa global + resume del owner) y corrida de los gates de carga de 4-4 antes del lanzamiento público.
3. Los contadores de observabilidad (4-3) son memoria de proceso a propósito — si se necesita historia, va a Postgres como `system_settings`/`send_log`, no a logs parseados.
