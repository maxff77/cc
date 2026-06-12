# Retrospectiva — Epic 3: Captura de respuestas, sesiones e historial

**Fecha:** 2026-06-12 · **Stories:** 3-1 a 3-6 (6/6 done) · **Suite al cierre del epic:** 235 tests

## Qué salió bien

- **Cadena secuencial con absorción de diferidos:** cada story resolvió deferred de stories previas como parte de su scope (3-1 absorbió el buffer DB-down y el intent-row de 2-5; 3-2 los guards de reducers de ws.ts; 3-3 el pill de nav). El backlog diferido bajó en vez de crecer durante el epic.
- **Atribución verificada empíricamente:** `reply_to_msg_id` → `send_log` → batch → sesión funcionó tal como lo planteó la arquitectura; el bucket de unmatched dio el hook que 4-3 luego explotó para observabilidad.
- **Reuso de componentes:** los paneles duales de 3-2 sirvieron sin cambios a 3-3 (historial), 3-5 (export) y 3-6 (vista cross-tenant) — el costo marginal de las stories UI cayó story a story.
- **Paridad legacy disciplinada:** `extraer_cc`/`RE_CC` portados literal (truncado en `Status` incluido), dedup por sesión en DB con índice único en vez de set en memoria — mismo comportamiento, ahora durable.

## Qué salió mal / fricción

- **El doc de epics quedó atrás del código:** "prefijo"→"gate" y los renames de 2.x obligaron a cada create-story a reconciliar contra código actual. Mitigado con la regla "el código actual gana", pero costó contexto en cada story.
- **Snapshot vs. vivo:** el cap `_SNAPSHOT_ROWS=200` y los totales honestos generaron varios findings de review (badges mintiendo al reconectar) — el patrón snapshot-first exige que CADA campo nuevo pase por el snapshot, fácil de olvidar.
- **El MEDIUM de parse_mode (2-5) siguió abierto todo el epic** pese a que 3-1 dependía de texto byte-exacto; recién se cerró en el barrido final. Un deferred que bloquea semántica de un epic posterior debería promoverse a tarea de entrada del epic.

## Lecciones para adelante

1. Deferred que afecta correctness de un epic futuro = primera tarea de ese epic, no backlog.
2. Todo campo de evento WS nuevo necesita su espejo en snapshot + reducer + seed/reset en el mismo PR.
3. La validación humana con Telegram real sigue pendiente de un smoke supervisado del owner (toda la captura se probó con FakeGateway/ASGI).
