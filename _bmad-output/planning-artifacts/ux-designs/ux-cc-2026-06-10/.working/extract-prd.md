# Extracto UX — PRD cc (SaaS Telegram Forwarding Platform)

## Product Goals
- Convertir forwarder personal en servicio web vendible: multi-tenant, clientes comparten una sola cuenta de Telegram
- Métrica primaria: ≥10 clientes pagos en el primer mes post-launch
- Guardrail: tasa de baneo de cuenta ≈ 0 (estabilidad de cuenta es primordial)
- Secundaria: retención / churn

## Personas
- **Owner (Richard):** founder, despliega en VPS, prioridad de envío
- **Admin:** gestiona cuentas de clientes (alta, renovación, bloqueo, reset de contraseña) — no gestiona otros admins
- **Client:** opera su espacio tenant aislado; sube lotes, ve sesiones

## Journeys nombrados (verbatim del PRD)
- "Cliente carga un lote pegando líneas de texto, elige un prefijo del catálogo global y dispara el envío" (FR9)
- "El planificador reparte el canal de envío por round-robin entre los clientes activos" (FR10)
- "Durante un lote en curso, el cliente puede pausar, reanudar y detener su propio envío, y ve progreso en vivo y ETA" (FR15)
- "El cliente puede ver, renombrar y continuar una sesión... con vistas Completa y Filtrada y seguimiento en vivo" (FR17)
- "Al expirar el plan, el cliente queda bloqueado por completo... y ve un mensaje que lo dirige a un canal de contacto externo" (FR5)

## FRs que implican pantallas
### Auth & cuentas
- FR1: alta manual de clientes por admin/owner (email + password)
- FR3–FR4: UI de expiración de plan con controles de renovación/extensión
- FR6–FR7: reset de contraseña → cambio forzado en próximo login
- FR8: login email+password; aislamiento de datos por tenant visible en UI

### Carga y envío de lotes
- FR9: formulario de texto de lote + selector de prefijo (catálogo fijo — sin texto libre)
- FR12–FR13: intervalo de envío visible (adaptativo, NO editable por usuario); consciente de concurrencia
- FR15: barra de progreso en vivo + ETA, pausa/reanudar/detener solo del propio lote

### Sesiones y respuestas
- FR16–FR17: lista de sesiones agrupada por prefijo; vistas duales Completa/Filtrada con live tracking
- FR17: renombrar, continuar (resume dedup), eliminar sesión
- FR18: exportar/descargar `.txt` (ambas vistas)
- FR19: eliminar sesión (sin edición en MVP)
- FR20: override admin/owner: ver contenido de sesiones de cualquier cliente (soporte)

### Multi-rol
- Owner dashboard: crear/gestionar admins, gestionar todos los clientes, cola con prioridad
- Admin dashboard: gestionar clientes (crear, renovar, bloquear, resetear password)
- Client workspace: vista tenant aislada de lotes, sesiones, exports

## Requisitos UI/UX explícitos
- Sin auto-registro: provisión manual por admin/owner
- Sin self-service de credenciales: admin genera password temporal, entrega out-of-band (sin email en MVP)
- Prefijo: dropdown/selector de catálogo global
- Intervalo de envío no editable: controlado por sistema
- Progreso en vivo + ETA durante envío
- Vistas duales de sesión: Completa (cruda) y Filtrada (datos `CC:` dedupeados) — ambas exportables
- Mensaje de expiración: redirección clara a canal de venta externo (WhatsApp/Telegram)

## Plataforma
- SaaS web multi-tenant
- Stack frontend (tentativo): Next.js + HeroUI
- Deploy: subdominio en VPS de Richard; HTTPS requerido (NFR5)
- Desktop/browser primario; sin mención mobile

## Accesibilidad / i18n / branding
- Sin menciones explícitas en PRD/addendum
- Idioma de interfaz: Español (terminología de producto: "cliente", "prefijo", "sesión")

## Constraints críticos que afectan UX
- Una sola cuenta Telegram compartida: sin identidad Telegram por cliente
- Mecanismo de atribución de respuestas ABIERTO (riesgo técnico #1): cómo rutear respuestas al cliente correcto
- Intervalo de envío ~10–20 s (adaptativo por concurrencia)
- Techo de 50 clientes concurrentes enviando
- Sin límite de tamaño de lote en MVP
- Sin edición de contenido de sesión: ver/exportar/eliminar
- Round-robin + prioridad de owner (excepción intencional a la equidad)
- Expiración como corte duro: sin degradación gradual
- Asimetría admin: gestionan clientes, no otros admins

## Preguntas abiertas/diferidas (del PRD)
1. Mecanismo de atribución de respuestas (marca correlacionable / secuencia temporal / estado por message_id)
2. Fórmula exacta de adaptación de intervalo y umbrales de concurrencia
3. ¿Owner puede operar con intervalos menores que clientes regulares?
