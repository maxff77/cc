# CC — first-deploy runbook (Story 1.7)

**Subsequent deploys are one command: `sudo bash /srv/cc/deploy/deploy.sh`.**
Everything below is the one-time first install on the VPS (37.27.12.92).

## 1. DNS

Create an A record for the chosen subdomain pointing at `37.27.12.92`.
That subdomain is the value for `CC_DOMAIN` (step 9) — nothing in the repo
hardcodes it.

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

```bash
sudo -u cc git clone <repo-url> /srv/cc
```

## 4. Backend

```bash
cd /srv/cc
sudo -u cc python3.12 -m venv backend/.venv
sudo -u cc backend/.venv/bin/pip install -e ./backend
sudo -u cc cp backend/.env.example backend/.env
sudo -u cc nano backend/.env   # prod values: see "Production (Story 1.7)" section
```

Required prod values: `DATABASE_URL` (step 5), `COOKIE_SECURE=true`,
`TRUST_FORWARDED_FOR=true`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`.
Real credentials — never committed.

## 5. Postgres (already running on the VPS)

```bash
sudo -u postgres psql -c "CREATE ROLE cc LOGIN PASSWORD '<strong-password>';"
sudo -u postgres psql -c "CREATE DATABASE cc OWNER cc;"
cd /srv/cc/backend && sudo -u cc .venv/bin/alembic upgrade head
```

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

First check nothing else holds the public ports:

```bash
ss -tlnp | grep -E ':80|:443'
```

If **nginx** is already listening there, skip Caddy and replicate the routes
in `deploy/Caddyfile` as nginx `location` blocks + certbot. Otherwise:

```bash
sudo apt install caddy
sudo systemctl edit caddy        # add:  [Service]
                                 #       Environment=CC_DOMAIN=<subdomain>
sudo cp /srv/cc/deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy obtains the Let's Encrypt certificate automatically.

## 10. systemd services

```bash
sudo cp /srv/cc/deploy/cc-core.service /srv/cc/deploy/cc-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cc-core cc-web
systemctl status cc-core cc-web
```

## 11. Smoke test (AC5)

1. Open `https://<subdomain>` → redirected to `/login`; check the padlock
   (valid certificate).
2. Log in as the owner → lands on home.
3. `curl -s -o /dev/null -w '%{http_code}\n' https://<subdomain>/api/health`
   → `200` (uvicorn answering over HTTPS through Caddy).
4. Try a wrong password → inline Spanish error on the login form.
