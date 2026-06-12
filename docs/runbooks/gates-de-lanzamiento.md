# Runbook: gates de lanzamiento (pre-launch)

Verificaciones que deben estar **en verde antes de onboardear clientes
reales** (Story 4.4; los riesgos vienen del deep-dive de la arquitectura).
"Gate de lanzamiento" ≠ "gate" del catálogo de prefijos — ver
[`README.md`](README.md).

Cada gate tiene dos capas:

- **Capa A — simulación (automatizada, en el repo):** valida el *contrato*
  (fórmula del scheduler, regla de atribución) contra un gateway/bot falso.
  Corre en milisegundos, sin Telegram, sin VPS, sin base de datos. Está
  cableada también como tests (`backend/tests/test_prelaunch.py`).
- **Capa B — entorno real (manual, owner):** valida los *supuestos sobre
  Telegram* (comportamiento real de FloodWait, que el bot siempre responda
  con `reply_to`). Solo el owner la ejecuta, en una ventana controlada.
  **Nunca** se corre desde una máquina de desarrollo contra la cuenta de
  producción.

## Gate 1 — Load test de `G_min = 3.0s` (supuesto: ese ritmo es seguro)

### Capa A — simulación

```bash
cd backend   # venv activo
python -m scripts.load_test_gmin                  # exit 0 = PASA
python -m scripts.load_test_gmin --json           # reporte máquina-legible
python -m scripts.load_test_gmin --g-min 1.0      # demo: un G_min inseguro FALLA
```

Valida: ritmo global nunca por debajo de `G_min`, round-robin justo,
prioridad del owner acotada al 50%, tenants pausados excluidos de `n`,
FloodWait → reintento de la misma línea + governor sube `G_min`.

### Capa B — staging/producción (manual, owner; requiere Story 2.4 desplegado)

1. Elegí una ventana de bajo riesgo (sin clientes activos).
2. Lanzá un lote real sostenido (≥ 200 líneas, ≥ 30 minutos) con
   `G_min = 3.0s` y un destino de prueba que el bot atienda.
3. Observá FloodWaits en los logs estructurados
   (`sudo journalctl -u cc-core | grep -i floodwait`; con Story 4.3, la
   alerta de FloodWait).

**Pasa si:** 0 FloodWaits en toda la corrida.
**Falla si:** aparece cualquier FloodWait → el governor ya subió `G_min`;
adoptá el valor final sugerido como nuevo `G_min` configurado (Story 2.4) y
repetí la corrida hasta verde. Documentá el valor validado acá:

| Fecha | Corrida | G_min probado | FloodWaits | Veredicto |
| --- | --- | --- | --- | --- |
| _pendiente_ | | 3.0s | | |

## Gate 2 — Test de volumen de atribución (supuesto A1: el bot SIEMPRE responde con `reply_to`)

### Capa A — simulación

```bash
cd backend   # venv activo
python -m scripts.attribution_volume_test                       # exit 0 = PASA
python -m scripts.attribution_volume_test --tenants 10 --lines 1000
python -m scripts.attribution_volume_test --missing-reply-to-rate 0.02   # demo: FALLA
```

Valida: 5000 envíos intercalados de 50 tenants, respuestas fuera de orden y
con ediciones (❌→✅) → unmatched = 0, cero atribuciones cruzadas entre
tenants, las ediciones no duplican atribución.

### Capa B — comandos reales (manual, owner; requiere Stories 2.5 + 3.1 desplegadas)

El supuesto se verificó empíricamente con 1 comando y 1 tipo de gate
(confianza media, impacto crítico). A volumen y con comandos reales variados:

1. Enviá ≥ 500 comandos reales por la plataforma, cubriendo **todos los
   gates del catálogo** que se vayan a usar en el lanzamiento, con varios
   lotes intercalados.
2. Revisá el bucket de respuestas sin atribuir (logs estructurados de 3.1;
   con Story 4.3, métrica de unmatched).

**Pasa si:** unmatched ≈ 0 (cada caso aislado debe poder explicarse: mensaje
del bot no relacionado con un envío, etc.).
**Falla si:** el bot responde sin `reply_to` para algún tipo de comando →
NO onboardear; la atribución multi-tenant no es confiable para ese gate.

| Fecha | Comandos | Gates cubiertos | Unmatched | Veredicto |
| --- | --- | --- | --- | --- |
| _pendiente_ | | | | |

## Gate 3 — Backups instalados y restaurables

1. Timer instalado y corriendo: `deploy/README.md` paso 12.
2. Un dump de hoy existe en `/var/backups/cc` y la corrida terminó en
   "backup done".
3. Simulacro de restauración ejecutado una vez con éxito
   ([`backups-y-restauracion.md`](backups-y-restauracion.md)).

## Gate 4 — Procedimientos de recuperación listos

1. Runbook de re-auth leído y entendido ([`reauth-telegram.md`](reauth-telegram.md)).
2. `anon.session` sana en el VPS (`-rw------- cc cc /var/lib/cc/anon.session`).
3. `TELEGRAM_API_ID`/`TELEGRAM_API_HASH` presentes en `/srv/cc/backend/.env`
   (sin ellos la re-auth de emergencia no puede correr).

## Checklist final (firmar antes de onboardear)

| # | Gate | Estado |
| --- | --- | --- |
| 1A | Load test simulado en verde (`scripts.load_test_gmin`, exit 0) | ☐ |
| 1B | `G_min` validado contra Telegram real (0 FloodWaits) | ☐ |
| 2A | Atribución simulada en verde (`scripts.attribution_volume_test`, exit 0) | ☐ |
| 2B | Volumen real: unmatched ≈ 0 con comandos reales | ☐ |
| 3 | Backup diario corriendo + simulacro de restauración OK | ☐ |
| 4 | Runbook re-auth listo, sesión sana, credenciales presentes | ☐ |

Con todo en verde, el onboarding sigue la regla de ramp-up gradual:
[`plan-de-lanzamiento.md`](plan-de-lanzamiento.md).
