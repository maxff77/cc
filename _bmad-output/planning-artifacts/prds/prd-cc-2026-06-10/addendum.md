# Addendum — PRD cc

Profundidad técnica y de tuning que pertenece a arquitectura/diseño, no al PRD.

## Stack (tentativo)

- Frontend web: **Next.js + HeroUI** (decisión de Richard, a validar en arquitectura).
- Núcleo de envío/captura: lógica actual en Python/Telethon (ver `core.py`,
  `app.py`) — a evolucionar para multi-tenant.

## Tenancy modelo B — aislamiento y atribución (a diseñar en arquitectura)

Todos los clientes comparten una sola cuenta Telegram y un solo bot destino.
Problemas a resolver en el diseño técnico (NO en el PRD):

- **Atribución de respuestas**: cómo mapear cada respuesta del bot al cliente que
  originó la línea, dado que todas las líneas salen por la misma sesión al mismo
  bot. (Posibles caminos: marca/prefijo correlacionable, secuenciación temporal,
  estado por message_id — a evaluar.)
- **Aislamiento de datos**: cada cliente solo ve sus sesiones/respuestas.
- **Punto único de baneo**: mitigaciones operativas (monitoreo, throttle global).

## Tuning de intervalo de envío (FR12–FR13)

- Histórico de Richard usando el sistema solo: intervalo ~2–3 s.
- La mayoría de usuarios están acostumbrados a ~20 s.
- **Banda objetivo de operación: ~10–20 s.**
- **Adaptación por concurrencia**: a más clientes activos simultáneos → intervalo
  más alto (más cerca de 20 s); a menos clientes → más bajo (~10 s).
- Fórmula exacta de adaptación, umbrales y prioridad del owner = a definir en
  arquitectura. El owner puede operar a intervalo más agresivo por su prioridad.

## Escalado futuro: multi-número (fuera del MVP)

- Techo del MVP: ~50 clientes activos simultáneos sobre **una sola cuenta**.
- Dirección futura de Richard: operar **varias cuentas/números a la vez** para
  repartir la carga de envío y subir el techo de concurrencia. Requiere resolver
  enrutamiento de cuentas, balanceo y atribución multi-cuenta. No en el MVP.
