---
title: PRD — cc (Plataforma SaaS de envío y captura por Telegram)
status: final
created: 2026-06-10
updated: 2026-06-10
---

# PRD — Plataforma SaaS de envío y captura por Telegram

## Visión

Una plataforma web multi-tenant donde varios clientes, cada uno con su espacio
aislado, cargan lotes de líneas que se envían de forma controlada a través de
**una única cuenta de Telegram compartida**, y reciben de vuelta las respuestas
del bot **filtradas y guardadas por cliente**. Ningún cliente puede ver, afectar
ni poner en riesgo la sesión de otro, y el volumen de un cliente nunca degrada el
servicio del resto ni compromete la cuenta compartida.

El cliente trabaja con **prefijos preestablecidos** (elige de una lista, sin
escribir a mano) y **gestiona y visualiza sus propias sesiones** de forma simple.

## Riesgo / restricción central

El modelo de tenancy es **B (cuenta compartida)**: todos los clientes envían a
través de la misma cuenta de Telegram y el mismo bot. Esto impone dos
restricciones que gobiernan todo el producto:

- **Punto único de baneo.** Un ban de Telegram sobre la cuenta deja sin servicio
  a *todos* los clientes simultáneamente. Proteger la cuenta es el requisito #1.
- **Presupuesto de envío global.** El rate-limit de Telegram es compartido entre
  todos los tenants. El sistema debe planificar y secuenciar los envíos de todos
  los clientes contra un único cupo, sin FloodWait ni caídas del bot.

_(Cómo se resuelve técnicamente el aislamiento, la atribución de respuestas y el
control de cupo → `addendum.md` / arquitectura. El PRD define el QUÉ.)_

## Objetivo y métricas

**Objetivo.** Convertir el forwarder personal en un servicio web vendible:
Richard lo despliega en su VPS bajo un subdominio y vende acceso por tiempo a
múltiples clientes que comparten su cuenta de Telegram.

- **Métrica de éxito principal:** número de **clientes con plan vigente
  (pagando)**. **Meta de lanzamiento: ≥ 10 clientes pagando en el primer mes**
  post-lanzamiento (canal de ventas vía sellers ya disponible).
- **Contra-métrica (guardarraíl):** **tasa de baneo de la cuenta ≈ 0**. Crecer en
  clientes nunca debe lograrse a costa de la estabilidad de la cuenta compartida;
  un baneo deja a todos sin servicio.
- **Métrica secundaria:** retención / churn de clientes al vencer el plan.

## Glosario

- **Cliente con plan vigente (pagando):** cuenta de cliente cuyo plan por días no
  ha expirado. Es la base de la **métrica de éxito principal**.
- **Cliente concurrente (activo enviando):** cliente que está ejecutando un lote
  de envío en un momento dado. Es la unidad del **NFR2 (tope 50)** y del reparto
  round-robin (FR10). Un cliente con plan vigente que no está enviando **no**
  cuenta como concurrente.
- **Sesión:** conjunto de envíos+respuestas agrupado por prefijo dentro del
  espacio de un cliente.

## Características y requisitos

### F1 — Acceso y cuentas de clientes

Capa SaaS multi-tenant: alta manual de clientes, control de acceso por tiempo y
gestión de credenciales sin infraestructura de correo en el MVP.

- **FR1.** Un admin u owner crea cuentas de cliente manualmente, indicando correo
  y contraseña inicial. No hay auto-registro.
- **FR2.** El sistema tiene tres roles: **owner**, **admin** y **cliente**.
  - **Owner** (Richard): control total, incluye crear y quitar admins.
  - **Admin**: gestiona únicamente clientes (crear, renovar, bloquear, resetear
    contraseña). No gestiona otros admins.
  - **Cliente**: opera solo su propio espacio.
- **FR3.** Cada cliente tiene un **plan por tiempo medido en días** con una fecha
  de expiración. Al llegar la fecha, el acceso se corta automáticamente.
- **FR4.** Un admin u owner puede **renovar/extender** el plazo de un cliente
  (sumar días o fijar nueva fecha de expiración).
- **FR5.** Al expirar el plan, el cliente queda **bloqueado por completo** (no
  envía ni accede al espacio) y ve un mensaje que lo dirige a un **canal de
  contacto externo** (team/sellers, ej. WhatsApp/Telegram) para renovar su
  acceso. El canal de ventas no es un rol del sistema.
- **FR6.** Un admin u owner puede **resetear la contraseña** de un cliente: el
  sistema genera una contraseña temporal aleatoria y segura, que el admin ve en
  pantalla y entrega al cliente por su propio medio (sin envío automático de
  correo en el MVP).
- **FR7.** Tras un reset, el cliente está **forzado a cambiar su contraseña** en
  el siguiente inicio de sesión antes de poder operar.
- **FR8.** Autenticación por correo + contraseña. Cada cliente solo puede ver y
  operar **sus propios datos**; ninguna cuenta accede a los datos de otra
  (aislamiento de tenant).

### F2 — Envío en lote controlado

El cliente envía lotes; el sistema planifica todos los envíos de todos los
clientes contra el único canal compartido, protegiendo la cuenta.

- **FR9.** El cliente carga un lote pegando líneas de texto, **elige un prefijo
  del catálogo global** y dispara el envío. No escribe el prefijo a mano.
- **FR10.** El planificador reparte el canal de envío por **round-robin entre los
  clientes activos**: ningún cliente puede monopolizar el canal; todos los lotes
  en curso avanzan de forma intercalada.
- **FR11.** El **owner tiene prioridad**: cuando el owner envía, sus líneas se
  anteponen a la rotación de clientes. _(Excepción deliberada a la equidad de
  FR10: el owner puede demorar a los clientes mientras envía.)_
- **FR12.** El **intervalo entre envíos lo fija el sistema** y **no es editable
  por el cliente** (protege la cuenta compartida de FloodWait/baneo).
- **FR13.** El intervalo es **adaptativo según la concurrencia**: a más clientes
  activos simultáneamente, mayor el intervalo; a menos clientes, menor. (Banda
  objetivo y fórmula de adaptación = parámetros de tuning → `addendum.md`.)
- **FR14.** **Sin tope de tamaño de lote** en el MVP: ilimitado mientras el plan
  del cliente esté vigente.
- **FR15.** Durante un lote en curso, el cliente puede **pausar, reanudar y
  detener** su propio envío, y ve **progreso en vivo y ETA**. Estos controles son
  por cliente y no afectan los lotes de otros.

### F3 — Captura y gestión de respuestas/sesiones por cliente

- **FR16.** Cuando el bot responde, el sistema **captura la respuesta**, guarda la
  **respuesta completa** y extrae los datos `CC:` a una **vista filtrada
  deduplicada por sesión**. Cada respuesta se atribuye y guarda en el espacio del
  **cliente correcto** (garantía del producto; el mecanismo de atribución →
  `addendum.md`).
- **FR17.** Cada envío genera una **sesión** agrupada por prefijo. El cliente
  puede **ver, renombrar y continuar** una sesión (retomando la deduplicación),
  con vistas **Completa** y **Filtrada** y seguimiento en vivo.
- **FR18.** El cliente puede **exportar/descargar** sus resultados (vista completa
  y vista filtrada) en formato **`.txt`**.
- **FR19.** El cliente puede **borrar** sus propias sesiones. (Solo borrar; editar
  el contenido no está en el MVP.)
- **FR20.** **Owner y admins pueden ver el contenido** de las sesiones de
  cualquier cliente, para fines de soporte.

## Requisitos no funcionales (NFR)

- **NFR1 — Protección de la cuenta (crítico).** Con hasta 50 clientes
  concurrentes, el sistema mantiene el ritmo de envío dentro de límites seguros de
  Telegram: sin FloodWait sostenido ni baneos de la cuenta compartida.
- **NFR2 — Concurrencia.** El MVP soporta hasta **50 clientes concurrentes**
  (enviando lotes simultáneamente; ver Glosario), no necesariamente 50 clientes
  con plan vigente totales.
- **NFR3 — Aislamiento de tenant.** Separación estricta de datos entre clientes;
  ningún cliente accede a los datos, sesiones o envíos de otro.
- **NFR4 — Equidad y degradación elegante.** Ningún cliente puede acaparar el
  canal (round-robin). Al subir la concurrencia el servicio se vuelve más lento,
  no se cae.
- **NFR5 — Seguridad.** Contraseñas almacenadas con hash de derivación lenta
  (clase bcrypt/argon2, nunca texto plano ni hash rápido); protección del archivo
  de sesión de Telegram (`anon.session`) contra acceso no autorizado; todo el
  acceso web sobre HTTPS en el subdominio.
- **NFR6 — Durabilidad.** Las sesiones y resultados de cada cliente persisten y
  sobreviven a reinicios del servicio.

## Fuera de alcance (MVP)

- **Multi-número de Telegram** para repartir la carga — dirección futura para
  escalar más allá de 50 clientes; no en el MVP (todo va por una sola cuenta).
- Edición del contenido de sesiones (solo ver / borrar).
- Auto-registro de clientes (alta solo manual por admin/owner).
- Envío automático de correo (reset y entrega de credenciales son manuales).
- Planes por volumen/consumo (solo por tiempo en días).

## Notas para PM / riesgos abiertos

Decisiones de producto cerradas; estos son riesgos que el diseño técnico debe
resolver antes de construir:

- **Mecanismo de atribución (riesgo técnico #1).** FR16 garantiza que cada
  respuesta del bot llegue al cliente correcto, pero **cómo** se atribuye —dado
  que todo sale por una sola cuenta al mismo bot— sigue "a evaluar" en el
  addendum. Es el mayor riesgo de viabilidad del modelo B; resolver en
  arquitectura antes de comprometer fechas.
- **Banda segura de envío (NFR1).** El número exacto de envíos/seg seguros para no
  gatillar FloodWait debe fijarse con prueba de carga; la banda 10–20 s es el
  punto de partida, no un límite validado.
