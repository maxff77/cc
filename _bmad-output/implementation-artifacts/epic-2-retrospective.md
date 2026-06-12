# Retrospectiva — Epic 2: Envío en lote controlado

Fecha: 2026-06-12 · Stories: 2.1–2.5 (5/5 done) · Facilitada por el dev agent (BMad retrospective)

## Métricas

| Story | Alcance | Tests backend nuevos | Hallazgos de review | Estado de hallazgos |
| --- | --- | --- | --- | --- |
| 2.1 Catálogo de gates | CRUD owner + soft-delete + `/admin/gates` | +16 (+3 post-review `name`) | 12 | 10 corregidos in-story, 2 diferidos |
| 2.2 Enviar lote | Telethon gateway, batches, worker, `/ws`, Envío UI, gate_categories | +34 (suite 107) | 8 diferidos | 5 resueltos (2.3/2.5/3.2), 3 abiertos |
| 2.3 Pause/resume/stop | Máquina de estados, índice "un lote vivo", pill/ring/flood notice | +12 (suite 125) | 3 diferidos | 3 resueltos (2.4/3.3) |
| 2.4 Scheduler multi-tenant | Round-robin, owner ≤50%, `G = max(G_min, P(n)/n)`, governor | +22 (suite 148) | 3 diferidos | 2 resueltos (2.5), 1 obsoleto |
| 2.5 Endurecimiento | `send_log` write-ahead, cap=3, fail-stop, reconciliación, expiry | +18 (suite 165) | 6 | 3 resueltos (3.1/3.2), 3 abiertos |

- Suite backend: 45 → **165 tests** (+120) — ruff/mypy/pytest verdes en cada story. Frontend: 0 tests (sin framework, decisión diferida; gates = eslint/tsc/build).
- 6 migraciones Alembic, todas con espejo en modelos y backfill/saneo donde producción tenía filas vivas.
- 4 commits de story en `deea2ce..a9c8df8` (2.2–2.5; 2.1 cerró antes). Sin commits de review-fix separados: los hallazgos se corrigieron in-story (2.1) o se difirieron con destino explícito.
- Diferidos de epic 2 aún abiertos: 2-2 #2 (append concurrente → 500), 2-2 #4 (WS no re-valida auth), 2-2 LOW (constraint-name), 2-1 ×2 (tipos a mano, copy de delete), 2-5 ×3 (ver riesgos).

## Qué salió bien

- **Las "promesas pagadas" entre stories.** 2.2 dejó primitivos a propósito (`wake`/`sleep_cancelable`, `FakeGateway`, `gateway.send` devolviendo `message_id`, comentarios `# Story 2.5`) y 2.3–2.5 los cobraron sin reescribir el worker. El cerco de alcance explícito ("Qué NO es esta story") evitó scope creep en las 5 stories.
- **El pipeline deferred-work funcionó como sistema.** Cada review difirió hallazgos con destino nominal y la story siguiente los absorbió en su cabecera (2.3 absorbió 3 de 2.2; 2.4 absorbió 2 de 2.3; 2.5 absorbió 3). Nada se perdió; incluso stories de epic 3 ya resolvieron rezagos.
- **Cambios de alcance del owner integrados sin descarrilar.** El rename prefijo→gate (día 1) y gate_categories + selector dos pasos (supersedió la decisión de 2.1 al día siguiente) se registraron como decisiones en la story, que gana sobre epics.md.
- **Decisiones de diseño registradas en el punto de uso**: estados String sin enum (cero ALTER después), snapshot verbatim de gate en batches (retirar/renombrar no reescribe historia), estado durable en Postgres preservando semánticas legacy documentadas, ventana FloodWait global sin exención.
- **Robustez más allá de la letra** detectada por el propio dev (locks FOR UPDATE en finalización, release que detecta stop concurrente, `scheduler.reset()` autouse) — varios bugs murieron antes del review.

## Qué salió mal

- **La concurrencia fue la fuente dominante de hallazgos MEDIUM, y se repitió.** TOCTOU/IntegrityError (2.1 y otra vez 2.2), carrera dos-tabs, hueco snapshot↔register, wake cross-tenant, bypass de ventana FloodWait, doble-envío post-crash. Cada review encontró una variante nueva del mismo tema: el diseño inicial de cada story subestimó las carreras.
- **Ningún smoke manual se ejecutó.** Las 4 stories de envío dejaron su smoke (HUMAN, credenciales de Richard) pendiente. Todo el epic está verificado solo contra `FakeGateway`: cero mensajes reales enviados, FloodWait real nunca provocado.
- **Producción no puede enviar**: 1.7 AC4 (re-auth de `anon.session` en el VPS) sigue pendiente → `503 telegram_unauthorized` en prod, y `TELEGRAM_TARGET` falta en el `.env` del VPS.
- **2.5 arrancó con working tree parcialmente avanzado y roto** (worker a medio migrar llamando funciones inexistentes) — hubo que auditar y completar; el handoff entre sesiones de dev no quedó limpio.
- **Drift doc↔código**: epics.md/UX siguen diciendo "prefijo" y sin categorías; conteos de baseline desincronizados (story decía 125, real 126). Cada story carga un banner de terminología para compensar.

## Lecciones accionables para epics 3/4

1. **Checklist de concurrencia en cada story que toque DB compartida o el worker**: FOR UPDATE en transiciones, catch de IntegrityError con constraint-name, ids > int32 → 404, eventos globales vs tenant-scoped (la señal que limpia debe tener el mismo alcance que la que ensucia — lección flood-notice, directamente aplicable a la captura de 3.x).
2. **Resolver 2-5 MEDIUM (parse_mode/reconciliación) en o antes de 3.1**: la atribución `reply_to_msg_id → send_log` exige texto byte-a-byte; hoy markdown en una línea puede corromper el envío y provocar re-queue/doble-envío.
3. **Darle dueño a los diferidos que nadie absorbe**: 2-2 #4 (socket abierto ignora bloqueo/expiración) es de seguridad y encaja en 4.1/4.2; 2-2 #2 y el pase epic-wide de tipos generados necesitan story u housekeeping explícito — el pipeline solo funciona si alguien "cobra".
4. **Desbloquear la validación humana antes del cierre de epic 3**: re-auth en VPS + un smoke real supervisado (envío, pausa, FloodWait si ocurre) — la captura de 3.1 no es verificable de verdad sin replies reales.
5. **Registrar decisiones del owner también hacia atrás**: actualizar epics.md (o un decision log) con gate/categorías para frenar el drift que cada story parchea con banners.

## Riesgos abiertos

- Re-auth de Telegram en el VPS pendiente (1.7 AC4) + `TELEGRAM_TARGET` ausente → producción responde 503; el pipeline completo jamás corrió contra Telegram real.
- Smoke manual humano pendiente en 2.2–2.5 (envía mensajes reales; requiere OK de Richard).
- Reconciliación post-crash frágil (2-5 MEDIUM abierto): `messages.Search` + markdown re-renderizado pueden re-encolar una línea ya entregada.
- Sockets WS abiertos no re-validan auth (2-2 #4): un cliente bloqueado/vencido sigue recibiendo eventos hasta que el socket muera.
- Governor/G_min sin load-test ("to be load-tested" del AC); fairness probada solo con FakeGateway.
- Frontend sin framework de tests — toda la máquina de estados de la UI se valida a ojo + tsc.

— Retro cerrada. Próximo paso sugerido: marcar `epic-2-retrospective: done` en sprint-status.yaml (no se tocó aquí por instrucción) y arrancar epic 3 con la lección 2 como precondición de 3.1.
