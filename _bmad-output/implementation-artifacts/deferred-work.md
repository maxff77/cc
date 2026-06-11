# Deferred Work

## Deferred from: code review of 1-6-reset-de-contrasena-con-cambio-forzado (2026-06-11)

- Generated API types document only 200/422 responses — the 400/401/403/404 error codes the frontend routes on (`password_reuse`, `password_change_required`, `forbidden`, `user_not_found`) are untyped in `frontend/types/api.ts`. Pre-existing FastAPI/openapi-generator behavior across all stories; fixing it means declaring `responses=` on every route, not a 1.6 concern.
