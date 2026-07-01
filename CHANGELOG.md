# Changelog

Cambios notables de **Ranger-X Check**. El formato sigue
[Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y el versionado
[SemVer](https://semver.org/lang/es/). La versión que se muestra en la app
(pill del navbar) sale de `frontend/package.json`.

## [1.1.12-beta] - 2026-06-30

Primer changelog consolidado: recoge las mejoras acumuladas desde `1.0.0-alfa`.

### Añadido

- **App instalable (PWA)** en escritorio y móvil: icono propio, ventana
  standalone y banner de instalación dentro del cockpit.
- **Nueva identidad Ranger-X**: icono cuadrado aplicado a favicon, navbar y app.
- **Landing público** de ventas en `/` con planes y gateways en vivo; el cockpit
  se mudó a `/app`.
- **Gateway Amazon (cookie-mode)**: bóveda de cookies gestionable a mitad de
  envío, envío serializado con rotación de cuentas y reintento durable, tarjeta
  LIVE/DEAD con marca, y reenvío de lives a un canal de Telegram.
- **Historial de lives**: respuestas aprobadas agrupadas por gateway, con
  borrado por mensaje / por gateway / todo.
- **Gift keys**: canje en modal, otorgan créditos configurables, revocar una key
  cancela el plan del cliente, y auto-purga de keys usadas o viejas.
- **Panel de monitoreo del owner** en `/admin/monitor` (actividad por tenant,
  flood, respuestas no emparejadas, watchdog, admisión).
- **Contactos de soporte de Telegram** editables por el owner.
- **Bóveda de credenciales** global con autenticación por API-key.
- **Antispam** desacoplado de los planes: default global + override por usuario.
- **Pill de versión** con gradiente de marca en navbar, login, landing y admin.

### Cambiado

- **Cockpit sin sesiones**: una sesión perpetua por cliente; "Limpiar" pasa a ser
  un corte de vista **no destructivo** (no borra datos).
- Etiqueta visible **"Gate" → "Gateway"** (el comando real nunca se expone al
  cliente).
- **Completa** muestra una fila por mensaje (antes: cada revisión).
- **Datos CC** refleja 1:1 a Aprobadas mediante dedup por mensaje.
- Rediseño cliente: navbar con menú ⋯, barra inferior móvil por pestañas y
  cockpit más aireado.

### Corregido

- **Instalación PWA en Android** que se colgaba en "instalando": `start_url` sin
  redirect + `id` del manifest.
- **Caché de iconos**: URLs versionadas (`?v=<versión>`) para vencer el caché de
  favicon del navegador, y el descarte del banner "Instalar" ahora caduca a 30
  días (antes era permanente, así reaparece para quien lo cerró).
- Guard **fail-closed** al arranque contra cambio de cuenta de Telegram (evita
  fuga de atribución entre tenants).
- Varias correcciones de captura/cockpit: "esperando respuesta" que se acumulaba
  para siempre, scroll infinito, filas sin glifo (✅/❌) y respuestas "Processing"
  intermedias.
- El reconciler ya no **resucita** historial borrado.

## [1.0.0-alfa] - 2026-06

Versión inicial en producción (ranger-x.lohari.com.mx): relay multi-tenant de
mensajes de Telegram, envío por lotes con progreso en vivo, catálogo de
gateways, planes/créditos y captura de respuestas (Completa / Filtrada).
