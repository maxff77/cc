# Runbook — cambio de subdominio a `ranger-x.lohari.com.mx`

Migración **reemplazo total**: el nuevo dominio público es
`ranger-x.lohari.com.mx`. El viejo `cc.lohari.com.mx` se retira (sin redirect).

Nada del dominio está hardcodeado en el código: Caddy usa el placeholder
`{$CC_DOMAIN}` sustituido al instalar en el VPS, y el backend no fija
CORS ni cookie-domain. El único cambio funcional en el repo es la URL del
smoke test en `.github/workflows/deploy.yml` (ya apunta a `ranger-x`).

> ⚠️ **Orden importa.** El smoke test del deploy hace `curl` contra
> `https://ranger-x.lohari.com.mx/api/health`. Si haces push a `main` **antes**
> de que Caddy sirva el nuevo dominio con cert válido, el deploy falla en el
> smoke test. Haz los pasos 1–3 antes del push.

## 1. Cloudflare (Richard) — alta del nuevo registro

1. En la zona `lohari.com.mx`, crear registro **A**:
   `ranger-x` → `37.27.12.92`.
2. Copiar el **mismo modo de proxy** (naranja/gris) que tiene hoy `cc`.
   Let's Encrypt necesita el `:80` alcanzable para el challenge HTTP-01; si `cc`
   funcionaba, `ranger-x` con el mismo modo también.
3. **No borres `cc` todavía** — se quita en el paso 5, tras verificar.

## 2. VPS — Caddy sirve el nuevo dominio

SSH al VPS (`37.27.12.92`) como root. El sitio vive en `/etc/caddy/cc.caddy`
(archivo server-local, importado por el Caddyfile principal — nunca tocar el
principal).

```bash
# Sustituir el dominio viejo por el nuevo en el sitio importado.
sudo sed -i 's/cc\.lohari\.com\.mx/ranger-x.lohari.com.mx/' /etc/caddy/cc.caddy

# Validar y recargar — Caddy provisiona el cert Let's Encrypt automáticamente.
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

> Si `/etc/caddy/cc.caddy` se regeneró desde el repo en algún deploy, vuelve a
> instalarlo con el placeholder y el nuevo valor:
> ```bash
> sudo cp /srv/cc/deploy/Caddyfile /etc/caddy/cc.caddy
> sudo sed -i 's/{$CC_DOMAIN}/ranger-x.lohari.com.mx/' /etc/caddy/cc.caddy
> sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy
> ```

## 3. Verificar el nuevo dominio

```bash
# Cert + health (esperar a que el cert se emita, ~10-30s la primera vez).
curl -s -o /dev/null -w '%{http_code}\n' https://ranger-x.lohari.com.mx/api/health   # 200
```

Abrir `https://ranger-x.lohari.com.mx` en el navegador → redirige a `/login`,
candado válido. **Los usuarios deben volver a iniciar sesión**: la cookie de
sesión es host-only y estaba ligada al dominio viejo (esto es esperado).

## 4. Push a `main` (deploy + smoke test contra ranger-x)

Con el dominio nuevo ya sirviendo, hacer push de este cambio. GitHub Actions
despliega y el smoke test ahora valida `https://ranger-x.lohari.com.mx`.

## 5. Retirar el dominio viejo

1. **VPS:** confirmar que `/etc/caddy/cc.caddy` ya no contiene `cc.lohari.com.mx`
   (el sed del paso 2 lo reemplazó). Caddy deja de servir el viejo al recargar.
2. **Cloudflare (Richard):** borrar el registro A de `cc`.
3. Verificar que `https://cc.lohari.com.mx` ya no resuelve / no responde.

## Rollback

Revertir es simétrico: en Caddy `sed` de `ranger-x` → `cc`, recargar, y
re-apuntar el DNS de `cc`. El cert viejo de `cc` se re-provisiona solo.
