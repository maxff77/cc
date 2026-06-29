# email-inbound

Worker HTTP en Cloudflare que recibe los correos de `*@mail.lohari.com.mx` (vía
webhook de **ForwardEmail**), los guarda en **D1**, y expone un endpoint
autenticado para leerlos. Base para automatizar OTPs después.

## Por qué ForwardEmail y no Cloudflare Email Routing

El apex `lohari.com.mx` corre el correo **M365/Outlook de Richard**. El onboarding
de Cloudflare Email Routing **siempre** agrega MX + un segundo SPF al **apex** →
rompería su correo (SPF duplicado = permerror). No hay forma de activarlo solo en
el subdominio. Por eso el ingreso va por **ForwardEmail**, que pone MX **solo en
`mail.lohari.com.mx`** (apex intacto) y hace catch-all real `*@mail...`.

🔒 NUNCA activar Cloudflare Email Routing en la zona `lohari.com.mx` ni tocar el MX
del apex.

## Arquitectura

```
*@mail.lohari.com.mx
  → MX mail.lohari.com.mx → ForwardEmail (catch-all)
      ├─ forward-email=maxtrooff77@gmail.com        → copia a Gmail
      └─ forward-email=https://<worker>/inbound     → POST JSON → Worker → D1
  Worker GET /emails (Bearer)  → leer lo guardado (para OTP futuro)

Apex lohari.com.mx (M365): INTACTO.
```

## Setup del Worker (una vez)

```bash
cd workers/email-inbound
npm install
npx wrangler login
npx wrangler d1 create emails            # copiar database_id → wrangler.toml
npx wrangler d1 execute emails --remote --file schema.sql
npx wrangler secret put AUTH_TOKEN       # token aleatorio largo (para GET /emails)
npx wrangler deploy                      # → https://email-inbound.<sub>.workers.dev
```

## Conectar el webhook de ForwardEmail

En Cloudflare DNS, **agregar un SEGUNDO** TXT en `mail` (dejar el de Gmail):

```
Name   Type   Value
mail   TXT    forward-email=maxtrooff77@gmail.com                         (ya existe)
mail   TXT    forward-email=https://email-inbound.<sub>.workers.dev/inbound   (nuevo)
```

ForwardEmail reenvía a **ambos** destinos: copia a Gmail Y POST al Worker → D1.
No lleva secreto en la URL (el TXT es público); el Worker autentica el webhook
verificando que el POST venga de las IPs de `mx1/mx2.forwardemail.net` (resueltas
en vivo por DoH). Si algún día pasas a plan pago de ForwardEmail, conviene migrar a
verificar el header `X-Webhook-Signature` (HMAC).

## Verificar

```bash
# manda un correo a prueba@mail.lohari.com.mx, luego:
npx wrangler d1 execute emails --remote \
  --command "SELECT id,from_addr,to_addr,subject FROM emails ORDER BY id DESC LIMIT 5"

# leer vía endpoint
curl -H "Authorization: Bearer <AUTH_TOKEN>" https://email-inbound.<sub>.workers.dev/emails

# guard de lectura (sin token) → 401
curl -i https://email-inbound.<sub>.workers.dev/emails
```

## Payload de ForwardEmail (referencia)

JSON estilo `mailparser`: `from.value[0].address`, `subject`, `text`, `html`,
`raw`, `headers`. El alias que recibió el correo está en `session.recipient` y
`recipients[]` (no en `to`). Para quitar adjuntos/raw del POST: añadir
`?attachments=false&raw=false` a la URL del webhook.

## Multi-dominio (agregar más dominios después)

El mismo Worker + la misma D1 sirven N dominios — `to_addr` ya guarda el dominio.
Agregar un dominio = darlo de alta en ForwardEmail (o, si es 100% tuyo, Cloudflare
Email Routing nativo con catch-all) y apuntar su catch-all al mismo
`/inbound`. Opcional cuando llegue el dominio #2: columna `domain` en `emails`.

## Leer OTP (fase siguiente)

`GET /emails?to=otp@mail.lohari.com.mx&limit=1` y regex sobre `text`. Aún no
implementado a propósito.
