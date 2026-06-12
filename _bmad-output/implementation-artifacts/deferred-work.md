# Deferred Work

## Deferred from: code review of 1-6-reset-de-contrasena-con-cambio-forzado (2026-06-11)

- Generated API types document only 200/422 responses — the 400/401/403/404 error codes the frontend routes on (`password_reuse`, `password_change_required`, `forbidden`, `user_not_found`) are untyped in `frontend/types/api.ts`. Pre-existing FastAPI/openapi-generator behavior across all stories; fixing it means declaring `responses=` on every route, not a 1.6 concern.

## Deferred from: code review of 1-7-despliegue-en-produccion-con-https-y-re-auth-de-telegram-en-el-vps (2026-06-11)

- Caddy `handle /ws` is exact-match (no `/ws/*`) and bare `/api` falls through to the Next.js catch-all — the real WebSocket path shape is unknown until Story 2.2 ships the endpoint; widen the matcher then (`deploy/Caddyfile`).
- deploy.sh partial-failure window: if `npm run build` fails after `alembic upgrade head`, the old backend keeps running on the migrated schema with no rollback path — accepted at MVP scale (additive migrations) (`deploy/deploy.sh`).
- telegram_auth.py: a corrupt/foreign pre-existing session file produces a raw sqlite/Telethon traceback with no delete-and-rerun hint — rare; the full re-auth runbook is Story 4.4's deliverable (`backend/scripts/telegram_auth.py`).
- AC4 pending owner action: `anon.session` absent in production — interactive re-auth (phone→code→2FA) only Richard can run; commands in story 1.7 Review Findings. Blocks story 1.7 → done.
