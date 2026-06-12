# CC — first-deploy runbook (Story 1.7)

**Subsequent deploys are one command: `sudo bash /srv/cc/deploy/deploy.sh`.**
The script refuses to run off `main` — if the first install was done from a
story branch (the 1.7 deploy was), switch once after the merge:
`cd /srv/cc && sudo -u cc git fetch && sudo -u cc git switch main`.
Everything below is the one-time first install on the VPS (37.27.12.92).

## 1. DNS

Production subdomain: **`cc.lohari.com.mx`**.

In the DNS panel for `lohari.com.mx`, create an A record:

```
cc    A    37.27.12.92
```

That subdomain is the value for `CC_DOMAIN` (step 9) — only the runbook
names it; config files use the `{$CC_DOMAIN}` placeholder.

## 2. System user and directories

```bash
sudo useradd --system --home /srv/cc cc
sudo mkdir -p /srv/cc /var/lib/cc
sudo chown cc:cc /srv/cc /var/lib/cc
sudo chmod 700 /var/lib/cc
```

`/var/lib/cc` will hold `anon.session` — outside the repo (git pull never
touches it) and outside anything Caddy serves.

## 3. Clone

Prerequisites: git, Python 3.12+, Node 22+ (frontend was generated for it).
Install Node via **NodeSource** (`https://deb.nodesource.com`) or the distro
package so that `/usr/bin/npm` exists — `cc-web.service` hardcodes that path
(nvm/asdf installs land elsewhere and the unit fails with `203/EXEC`).

```bash
sudo -u cc git clone <repo-url> /srv/cc
```

## 4. Backend

```bash
cd /srv/cc
sudo -u cc python3.12 -m venv backend/.venv
sudo -u cc backend/.venv/bin/pip install -e ./backend
sudo -u cc cp backend/.env.example backend/.env
sudo -u cc chmod 600 backend/.env   # holds every prod credential
sudo -u cc nano backend/.env   # prod values: see "Production (Story 1.7)" section
```

Required prod values: `DATABASE_URL` (step 5), `COOKIE_SECURE=true`,
`TRUST_FORWARDED_FOR=true`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`.
Real credentials — never committed.

## 5. Postgres (Dockerized on this VPS)

Postgres runs in Docker (`lohari-postgres`), fronted by `lohari-pgbouncer`
on `:5432` in **transaction** pool mode — which breaks asyncpg's prepared
statements. The backend must connect **directly to the postgres container**,
NOT through pgbouncer.

```bash
sudo docker exec -it lohari-postgres psql -U postgres
postgres=# CREATE ROLE cc LOGIN;
postgres=# \password cc          -- prompts; keeps the password out of shell history/ps
postgres=# CREATE DATABASE cc OWNER cc;
postgres=# \q
# Container IP on the lohari-net bridge (reachable from the host):
sudo docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' lohari-postgres
```

`DATABASE_URL=postgresql+asyncpg://cc:<password>@<container-ip>:5432/cc`

> ⚠️ **The container IP (`172.18.0.5` at deploy time) is not stable across
> container recreates.** If the backend suddenly loses the DB after Docker
> maintenance: re-run the `docker inspect` above, update `DATABASE_URL` in
> `/srv/cc/backend/.env`, then `sudo systemctl restart cc-core`.

```bash
cd /srv/cc/backend && sudo -u cc .venv/bin/alembic upgrade head
```

(Generic VPS with native Postgres: `sudo -u postgres psql`, same `CREATE
ROLE` / `\password` / `CREATE DATABASE`, and `127.0.0.1:5432` in the URL.)

## 6. Owner seed

```bash
cd /srv/cc/backend
sudo -u cc OWNER_EMAIL=<email> OWNER_PASSWORD=<password> .venv/bin/python -m scripts.bootstrap_owner
```

Then unset/clear those values from the shell history (pattern documented in
`backend/.env.example`). Idempotent: re-running updates the same owner.

## 7. Telegram re-auth — ON the VPS

The Telethon session is **always created on the VPS, never copied from
another machine** (a session created elsewhere risks invalidation when first
used from a datacenter IP).

```bash
cd /srv/cc/backend
sudo -u cc .venv/bin/python -m scripts.telegram_auth
# phone → login code → optional 2FA password
ls -l /var/lib/cc/anon.session   # must be: -rw------- cc cc
```

If Telegram auth ever dies in production (`AuthKeyError` in the logs),
re-run this script on the VPS (full runbook arrives with Story 4.4).

## 8. Frontend build

```bash
cd /srv/cc/frontend
sudo -u cc npm ci && sudo -u cc npm run build
```

## 9. Caddy

First check what holds the public ports:

```bash
ss -tlnp | grep -E ':80|:443'
```

**Caddy is ALREADY running on this VPS** (verified 2026-06-11: port 80
answers `Server: Caddy`, serving other lohari sites). Do **NOT** reinstall
or overwrite `/etc/caddy/Caddyfile` — add the cc site as a separate
**imported** file (this is what the live deploy did; idempotent to re-run):

```bash
sudo cp /srv/cc/deploy/Caddyfile /etc/caddy/cc.caddy
sudo sed -i 's/{$CC_DOMAIN}/cc.lohari.com.mx/' /etc/caddy/cc.caddy
grep -q 'import cc.caddy' /etc/caddy/Caddyfile \
    || echo 'import cc.caddy' | sudo tee -a /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

The domain is substituted directly (sed) instead of using an
`Environment=CC_DOMAIN=…` drop-in: a drop-in is only applied at process
START — `systemctl reload` never sees a new value, and `caddy validate`
from a shell can't see it either. `/etc/caddy/cc.caddy` is server-local,
never committed.

Caddy obtains the Let's Encrypt certificate for the new site on reload.

If instead **nginx** held the ports, replicate the routes in
`deploy/Caddyfile` as nginx `location` blocks + certbot.

## 10. systemd services

```bash
sudo cp /srv/cc/deploy/cc-core.service /srv/cc/deploy/cc-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cc-core cc-web
systemctl status cc-core cc-web
```

## 11. Smoke test (AC5)

1. Open `https://cc.lohari.com.mx` → redirected to `/login`; check the
   padlock (valid certificate).
2. Log in as the owner → lands on home.
3. `curl -s -o /dev/null -w '%{http_code}\n' https://cc.lohari.com.mx/api/health`
   → `200` (uvicorn answering over HTTPS through Caddy).
4. Try a wrong password → inline Spanish error on the login form.
