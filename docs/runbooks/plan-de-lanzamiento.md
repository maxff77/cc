# Plan de lanzamiento: ramp-up gradual (regla operativa)

**Por qué existe esta regla:** el riesgo de baneo no es solo de ritmo.
Cincuenta tenants mandando líneas con formato idéntico parecen un bot para
el anti-spam de Telegram **aunque el ritmo sea seguro** (riesgo de patrón de
contenido, deep-dive de la arquitectura). La mitigación es estructural: el
volumen crece **gradualmente** durante las primeras semanas, con monitoreo
entre fases. Esta es una regla operativa del owner, no una feature del
código.

**Prerequisito:** todos los gates de lanzamiento en verde
([`gates-de-lanzamiento.md`](gates-de-lanzamiento.md)).

## Fases

| Fase | Semana | Clientes activos máx. | Objetivo |
| --- | --- | --- | --- |
| 0 — Piloto | 1 | 1–2 (de confianza) | Validar el flujo completo con tráfico real bajo. |
| 1 — Temprana | 2 | ≤ 5 | El scheduler multi-tenant trabaja dentro de la banda 10–20s (`P(n)` llega a 20s en n=5). |
| 2 — Crecimiento | 3–4 | ≤ 10, con admission control activado (Story 4.2) | Cadencia estable cerca de la banda; cola de espera en vez de degradar a todos. |
| 3 — Régimen | 5+ | según demanda | Subir de a ~5 por semana mientras todo siga verde. |

## Condiciones para pasar de fase (todas, sostenidas toda la fase)

- **0 alertas de FloodWait** (Story 4.3) — es el indicador líder de baneo.
- **Unmatched replies ≈ 0** — la salud de la atribución es parte del
  guardarraíl.
- **0 disparos del watchdog** de reply-rate / sesión (Story 4.1).
- Sin quejas de cadencia de los clientes activos (la ETA honesta de la UI
  se mantiene dentro de lo prometido).

## Regla de retroceso (en cualquier fase)

Ante **FloodWaits repetidos**, caída del reply-rate, o crecimiento anormal
del bucket de unmatched:

1. Pausa global de envíos (watchdog la hace sola si está desplegado;
   manual si no).
2. NO onboardear más clientes hasta entender la causa.
3. Si el governor subió `G_min`, adoptar el nuevo valor como configuración
   antes de reanudar.
4. Reanudación explícita del owner, empezando con menos clientes activos
   que cuando ocurrió el incidente.

## Durante todo el ramp-up

- Revisar a diario: alertas de FloodWait, bucket de unmatched, disparos del
  watchdog, `systemctl status cc-core cc-backup.timer`.
- Variar el onboarding (no activar 5 clientes el mismo día a la misma hora).
- Mantener al owner como único usuario con prioridad (su tope del 50% de
  slots ya está acotado por el scheduler).
- El backup diario y su verificación semanal siguen corriendo
  ([`backups-y-restauracion.md`](backups-y-restauracion.md)).
