# Runbooks de operación — CC

Procedimientos operativos para producción (VPS `37.27.12.92`,
`ranger-x.lohari.com.mx`). El primer despliegue y el layout del VPS están en
`deploy/README.md`; estos runbooks cubren la operación del servicio ya
desplegado (Story 4.4).

> **Terminología:** en el producto, un **gate** es un prefijo del catálogo
> (`.zo`, tabla `gates`). En estos documentos, un **gate de lanzamiento** es
> una verificación previa al onboarding de clientes reales. Son conceptos
> distintos; aquí siempre se escribe "gate de lanzamiento" para el segundo.

| Runbook | Cuándo usarlo |
| --- | --- |
| [`gates-de-lanzamiento.md`](gates-de-lanzamiento.md) | Antes de onboardear clientes reales: load test de `G_min`, test de volumen de atribución, backups verificados. |
| [`plan-de-lanzamiento.md`](plan-de-lanzamiento.md) | Regla operativa de ramp-up gradual durante las primeras semanas. |
| [`reauth-telegram.md`](reauth-telegram.md) | La sesión de Telegram murió (`AuthKeyError` en los logs, o el watchdog pausó los envíos). |
| [`backups-y-restauracion.md`](backups-y-restauracion.md) | Verificar el backup diario, simulacro mensual de restauración, restauración ante desastre. |
| [`cambio-subdominio-ranger-x.md`](cambio-subdominio-ranger-x.md) | Cutover del dominio público a `ranger-x.lohari.com.mx` (Cloudflare + Caddy, reemplazo total). |
