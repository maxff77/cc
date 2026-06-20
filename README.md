# Ranger-X Check

**Reenviador de mensajes de Telegram multi-tenant (SaaS).** Los clientes pegan líneas; la plataforma las envía a través de **una sola cuenta de usuario de Telegram compartida** (Telethon/MTProto — cuenta de usuario, no un bot) hacia un bot verificador, paceadas y repartidas en round-robin de forma justa entre todos los tenants. Las respuestas ✅/❌ del bot se capturan, se atribuyen de vuelta a la línea y al tenant que las originó, y se guardan en dos vistas: **Completa** (cada revisión de respuesta capturada) y **Filtrada** (datos `CC:` deduplicados).

En producción: **https://ranger-x.lohari.com.mx**

> ⚠️ **Este repo tiene dos bases de código. Solo una es producción.**
> - **PRODUCCIÓN:** `backend/` (FastAPI + PostgreSQL, multi-tenant) y `frontend/` (Next.js + HeroUI).
> - **LEGACY / CÓDIGO MUERTO:** `app.py`, `core.py`, `auto_sender.py`, `static/`, `respuestas/` — prototipo mono-tenant, basado en archivos. **Nada en `backend/`/`frontend/` lo importa.** Se conserva solo como artefacto histórico; no editarlo para cambios de producción.

Documentación completa para agentes IA: **[CLAUDE.md](./CLAUDE.md)** (guía canónica) y **[docs/index.md](./docs/index.md)** (índice + arquitectura, modelos de datos, contratos de API).

---

## Arquitectura (resumen)

```
Tenants ─┐
         ├─▶ FastAPI (backend/) ── send worker ──▶ Telethon ──▶ bot verificador / grupos CC
Frontend ┘        scheduler (round-robin + paceo)                    │ ✅/❌
   cockpit        send_log (intención write-ahead)                   ▼
   admin          ▼                                          capture + atribución
              PostgreSQL ◀────────────────────────────────────────┘
                 │
                 └── WebSocket /ws (estado en vivo, server→client) ──▶ Frontend
```

- **`backend/`** — FastAPI async único. Lifespan: conecta el gateway de Telegram (no-fatal — bootea aunque no esté autorizado; enviar da 503), arranca el send worker, corre boot recovery, libera el capture consumer. El send worker registra la intención en `send_log` **antes** de enviar (write-ahead) y el `message_id` **después** (retry-forever / fail-stop). Telethon vive solo en `core/telegram.py` con `parse_mode=None`.
- **`frontend/`** — Next.js (App Router) + HeroUI, tema claro/oscuro, copy en español. Cockpit (envío + paneles Completa/Filtrada en vivo), historial, panel admin. El estado en vivo llega por WebSocket (`lib/ws.ts`); los comandos van por REST.

Detalle: [docs/architecture.md](./docs/architecture.md).

---

## Desarrollo local

### Backend (FastAPI, puerto 8000)

```bash
cd backend
python -m venv .venv && .venv/bin/pip install -e .   # primera vez
.venv/bin/alembic upgrade head                        # crear/refrescar el esquema
.venv/bin/uvicorn app.main:app --reload --port 8000   # servidor de dev
```

Requiere PostgreSQL. La config vive en `backend/.env` (ver `backend/.env.example`). Variable obligatoria: `DATABASE_URL` (asyncpg). Telegram es permisivo por defecto: sin credenciales, la app igual importa y corre los tests; enviar simplemente da 503.

Bootstrap (desde `backend/`, venv activo):

```bash
OWNER_EMAIL=... OWNER_PASSWORD=... python -m scripts.bootstrap_owner   # owner idempotente
python -m scripts.seed_user                                            # usuario de dev
python -m scripts.telegram_auth                                        # auth Telethon (solo VPS)
```

### Frontend (Next.js, dev 3000 / prod 3100)

```bash
cd frontend
npm install        # primera vez (Node 22+)
npm run dev        # next.config.mjs proxea /api y /ws → 127.0.0.1:8000
```

---

## Tests, lint, build

```bash
cd backend && .venv/bin/pytest        # tests backend (pytest + pytest-asyncio)
cd frontend && npm run lint           # eslint
cd frontend && npm run build          # ⚠️ corre tsc — el lint solo NO atrapa errores de tipos
```

> Antes de pushear a `main`: corré `npm run build`. El lint no atrapa errores de tipo y una vez rompió el deploy.

---

## Deploy

**Automático: cada push a `main`** dispara GitHub Actions (`.github/workflows/deploy.yml`), que entra por SSH al VPS, corre el idempotente `deploy/deploy.sh` (pull → pip → `alembic upgrade head` → npm build → reinicia `cc-core`/`cc-web`) y hace smoke-test de `/api/health`. Las migraciones siempre corren antes del reinicio.

Fallback manual (en el VPS, como root): `sudo bash /srv/cc/deploy/deploy.sh`.

**Topología:** VPS único `37.27.12.92`. systemd: `cc-core` (uvicorn :8000), `cc-web` (Next.js :3100), `cc-backup` (`pg_dump` diario). Caddy v2 reverse-proxy (`/api` + `/ws` → :8000, resto → :3100, HTTPS automático). PostgreSQL en Docker (`lohari-postgres`), el backend conecta directo a la IP del contenedor. Detalle en `deploy/` y `docs/runbooks/`.

---

## Invariantes críticas (no romper)

- 🔒 **Una sola cuenta de Telegram compartida** — un `anon.session` para todo el deployment; nunca correr dos `cc-core` a la vez. `(chat_id, message_id)` es la clave de atribución. Reautenticar a otra cuenta reinicia la secuencia → hay que limpiar `send_log`/`responses` antes, o las respuestas se mal-atribuyen entre tenants.
- 🔒 **`tenant_id` solo viene de la sesión** — nunca del body ni del path.
- 🔒 **Telethon solo en `core/telegram.py`** — `parse_mode=None` es load-bearing.
- **Write-ahead + fail-stop en el send worker** — intención antes de enviar; `message_id` después, retry-forever. No "optimizar" esto.
- 🔒 **Los datos `CC:` capturados son sensibles** — nunca leer el contenido de la carpeta legacy `respuestas/`.

Lista completa: [CLAUDE.md › Critical invariants](./CLAUDE.md).

---

## Solución de problemas

| Problema | Solución |
|---|---|
| `POST /api/batches` devuelve 503 | El gateway de Telegram no está autorizado. Correr `scripts.telegram_auth` en el VPS. |
| El login "funciona" pero no persiste la sesión | `COOKIE_SECURE` debe ser `false` en dev (http) y `true` en prod (https detrás de Caddy). |
| Alembic falla / esquema desactualizado | `cd backend && .venv/bin/alembic upgrade head`. |
| El deploy rompe en build | Correr `npm run build` local antes de pushear (el lint no atrapa errores de tipos). |
| Pausa global que no se levanta | Es el watchdog (pérdida de sesión o colapso de tasa de respuestas). El owner la resume desde `/api/watchdog/resume`; nunca auto-resume. |
