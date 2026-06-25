# Data Models — Ranger-X Check

> Generated: 2026-06-24. Source of truth: `backend/app/db/models.py` + Alembic migrations (head `b3f1a7c9d2e4`). PostgreSQL via async SQLAlchemy 2 / asyncpg. 18 tables.

## Conventions (read first)

- **`tenant_id` always comes from the session**, never from request body/path. Tenant-scoped tables CASCADE on tenant delete.
- **GLOBAL tables** (no `tenant_id` — a deliberate, documented exception): `gates`, `gate_categories`, `send_targets`, `plans`, `gift_keys`, `watchdog_state`, `system_settings`, `credentials`.
- **Snapshot / denormalize on purpose:** `batches` copy the gate string/name/`display_value`/`credit_cost`/mode flags verbatim at creation. `capture_sessions` now holds ONE perpetual row per tenant whose gate snapshot is **refreshed in place per batch** (not per-creation), so history that must survive a gate change is keyed off `batches`/`responses.batch_id`, NOT the session snapshot. No FK to `gates` from either — retiring or renaming a gate must never rewrite history.
- **Enums are plain `String`, not DB enums** (a deliberate decision) — `state`, `kind`, `status`, `reason` etc. widen without `ALTER TYPE`.
- **Repos use flush-not-commit** (the request owns the transaction) and `SELECT … FOR UPDATE` on read-modify-write paths.
- **Partial unique indexes do real work** — several invariants are DB-enforced, not just code-enforced (see the callouts below).
- `message_id` is **per-chat, not account-global** — supergroups/channels each have their own sequence. Attribution always keys on the `(chat_id, message_id)` PAIR.

---

## Identity & auth

### `tenants`
The isolation root. `credit_balance` (int, default 0) — debited once per captured ✅ on costed gates; topped up by plan assignment/renewal and owner recharge. The charge happens outside any request (capture pipeline is tenant-keyed), which is why credits live here, not on `users`. Cascades to `users`.

### `users`
`tenant_id` (FK CASCADE), `email` (unique), `password_hash` (argon2), `role` (`owner`/`admin`/`client`). Flags: `is_blocked`, `must_change_password` (while true, everything except change-password 403s). `expires_at` (plan expiry; NULL for owner/admin). `plan_id` (FK → `plans`, **RESTRICT**; NULL falls back to legacy global interval / no line cap). `contact` (Telegram handle for renewal outreach, no `@`).

### `auth_sessions`
`user_id` (FK CASCADE), `token` (opaque `secrets.token_urlsafe(32)`, unique+indexed — the cookie carries only this; server resolves it, no signing). `expires_at`, `revoked_at` (valid iff `revoked_at IS NULL`).

---

## Gate catalog (GLOBAL)

### `gate_categories`
Owner-managed grouping. `name` (unique). `special_mode` (bool) — gates here capture status from the `Approveds! ✅: N` count instead of bare ✅-glyph presence, stripping the stats segments. `cookie_mode` (bool) — gates here run Amazon cookie-mode (per-account cookies prepended, serialized send, rotation). Both flags are **snapshotted onto `CaptureSession`** at batch start. FK from `gates` is RESTRICT (`category_in_use`).

### `gates`
`value` (the REAL command the engine prepends, e.g. `.zo` — **OWNER-ONLY, never exposed to clients**), `name` (friendly label), `display_value` (the client-visible "Comando visible" shown everywhere `value` used to appear — decoupled so clients never see the real command), `credit_cost` (int, charged per captured ✅; 0 = free), `category_id` (FK RESTRICT). Soft-delete via `deleted_at`.
- **Partial unique:** `uq_gates_value_active(value) WHERE deleted_at IS NULL` — uniqueness among active entries only; a retired value can be recreated.

### `send_targets`
The configurable target chats the shared account sends to (any chat the account can message). `chat_id` (BigInteger, unique — the marked peer id, account-global, doubles as the capture filter), `label`, `enabled`. The worker round-robins over enabled + currently-resolvable targets. Resolution state is NOT stored (derived from the live gateway). Hard-deletable. Seeded on a fresh DB from `TELEGRAM_TARGET`.

### `gate_cookies` (TENANT-SCOPED)
One stored per-account cookie for a tenant on a cookie-mode gate. `tenant_id` (CASCADE), `gate_id` (FK to `gates`, NO `ondelete`/relationship — gates soft-delete, the vault outlives an active gate), `value` (🔒 **PLAINTEXT** credential, Text — the deliberate CC precedent; never echoed to a client, never logged), `value_hash` (sha256 hex of `value.strip()` — the dedup key), `label`, `status` (`active`; Phase-2 rotation reserved).
- **Unique:** `uq_gate_cookies_tenant_gate_hash(tenant_id, gate_id, value_hash)` — keyed on the hash (not raw value) because a cookie can exceed the btree row limit. Store-first / catch-IntegrityError, never SELECT-then-INSERT.
- **Index:** `ix_gate_cookies_tenant_gate_status_id` keeps the FIFO active-cookie rotation pick off a full partition scan.

---

## Sending

### `batches`
One send batch (lote) per tenant. `tenant_id` (CASCADE). **Snapshots:** `gate_value`, `gate_name`, `gate_display_value`, `gate_credit_cost`, `gate_id` (denormalized; no FK on `gate_id`). `state` (String): `sending` | `paused` | `stopping` | `stopped` | `completed` | `cancelled` (plan expiry mid-batch, terminal) | `waiting` (admission queue). `priority` (0=client, 1=admin, 2=owner — derived from creator role; read only by `scheduler.pick_next`). `capture_session_id` (FK SET NULL — the session outlives batch cleanup).
- **Amazon cookie-mode serialize gate (all NULL on non-cookie-mode batches):** `awaiting_verdict_until` (the serialize gate — while set+future, `active_senders` excludes this tenant, resolved in SQL against DB `now()`), `awaiting_message_id` + `awaiting_chat_id` (the attempt-fence — a verdict is accepted only if it matches the awaited `.amz` message), `pause_reason` (`cookies_exhausted` | `verdict_timeout`, rides the `batch.state` WS frame).
- **Partial unique:** `uq_batches_one_live_per_tenant(tenant_id) WHERE state IN ('sending','paused','stopping','waiting')` — DB-enforced "one live batch per tenant".

### `batch_lines`
One line of a batch — the FULL message with the gate already applied. `batch_id` (CASCADE), `tenant_id` (denormalized, CASCADE), `position`, `text` (Text), `state` (`queued`/`sending`/`sent`/`failed`/`cancelled`). `fail_code` (snake_case exception name; NULL unless failed). Cookie-mode: `failed_cookie_id` (FK → `gate_cookies` **SET NULL** — load-bearing: cookies hard-delete on dead verdict), `verdict_timeout_retries` (durable retry budget — survives restart, replaces the old process-memory set). `sent_at`.
- **Unique:** `uq_batch_lines_batch_id_position`. **Index:** `ix_batch_lines_batch_id_state` (the worker's hot next-queued-line query).

### `send_log` — the attribution write-ahead record
One row per line (`uq_send_log_line_id` — retries reuse the row). Written by the send worker, read by capture/attribution: `(chat_id, reply_to_msg_id) → send_log → tenant/batch/line`. Intent is recorded in the SAME transaction as the `sending` claim, BEFORE calling Telegram; `chat_id`/`message_id` are filled in after delivery. A row with `message_id` NULL = "attempted, delivery unconfirmed" → boot reconciliation resolves it.
- 🔒 `message_id` is per-chat. `chat_id` (BigInteger, marked peer id) namespaces it — attribution matches on the PAIR, never `message_id` alone, or replies mis-attribute across chats/tenants. Both BigInteger (supergroup `-100…` ids and Telegram message ids outgrow int4).
- `reply_purged_at` (nullable timestamp, migration `c4e2f7a1b903`) — tombstone set when a **Historial delete** removes the line's `responses` rows but (by invariant) leaves `send_log` intact. The reply reconciler keys "awaiting a reply" on the ABSENCE of a `kind='full'` response, so without this a deleted message looked awaiting again and was re-fetched & re-inserted within one ~45s pass (deleted history resurrected). `awaiting_sent_keys` / `count_awaiting_beyond_window` skip tombstoned rows (`reply_purged_at IS NULL`).
- **Indexes:** `ix_send_log_message_id`, `ix_send_log_chat_message(chat_id, message_id)` (the hot lookup).

---

## Capture (Completa / Filtrada)

### `capture_sessions`
ONE perpetual capture session per tenant (get-or-create via `ensure_perpetual`; never rotated/renamed/continued/closed). `tenant_id` (CASCADE). **Snapshots:** `gate_value`, `gate_name`, `gate_display_value`, `special_mode`, `cookie_mode` — refreshed IN PLACE per batch; `is_active` stays true and the id never churns. `name` (friendly label, NULL → UI falls back to a `created_at` format).
- `cleared_response_id` (BigInteger, nullable; NULL = nothing cleared) — the **Limpiar** view-cutoff, an id high-water-mark (`MAX(responses.id)` at clear time). Applied ONLY to the cockpit display reads + cockpit export (`Response.id > cleared_response_id`); NEVER to any integrity/dedup/attribution/reconciler/credit/awaiting_reply query. An id cutoff, not a timestamp (`Response.id` is monotonic, tie-immune).
- **Partial unique:** `uq_capture_sessions_one_active_per_tenant(tenant_id) WHERE is_active` — still present, now guarding the single first-ever-creation race for the perpetual row.

### `responses`
One table, both row types, discriminated by `kind`:
- `kind='full'` (**Completa**) — `text` = whole reply, `status` = `ok` (✅) / `rejected` (❌). Latest `full` row per `(chat_id, message_id)` IS the durable per-message state (replaces the legacy in-memory dict; survives restarts and `catch_up`).
- `kind='cc'` (**Filtrada**) — `text` = the extracted CC VALUE, `status` NULL.
- Columns: `tenant_id` (CASCADE), `capture_session_id` (CASCADE), `batch_id`/`line_id` (SET NULL — capture outlives batch cleanup), `chat_id` (BigInteger, namespaces `message_id`), `message_id` (BigInteger), `hidden_at` (RETAINED but now an INERT no-op — the old clear-declined soft-hide is superseded by the `capture_sessions.cleared_response_id` view-cutoff in PR-1; kept for forward-compat).
- **Partial unique:** `uq_responses_session_msg_cc(capture_session_id, chat_id, message_id, text) WHERE kind='cc'` — DB-enforced CC dedup, scoped **PER-MESSAGE** (migration `d1f4a8e2c5b6`, 2026-06-22). The same CC value approved on two distinct messages lands twice, so **Datos CC mirrors Aprobadas one-row-per-approved-reply**; only a capture retry / reconciler edit-replay of the SAME `(chat_id, message_id)` stays idempotent. `text` is truncated to ≤600 chars for the btree row limit. (Superseded the pre-2026-06-22 `uq_responses_session_cc(capture_session_id, text)`, which collapsed duplicates across messages — the "Datos CC < Aprobadas" complaint.) The Limpiar cutoff does NOT touch the dedup SELECT. **Indexes:** `ix_responses_message_id`, `ix_responses_chat_message(chat_id, message_id)`.
- **PR-2 Historial DESTRUCTIVELY deletes `responses` rows** (one message / one gate / all, via `/api/history`) — removing ONLY `responses` (children), never `batches`/`send_log`/`batch_lines`. Group-by-gate keys on `responses.batch_id → batches.gate_*`, so keep `batch_id`/`line_id` populated and the join alive.

---

## Plans, keys, billing

### `plans` (GLOBAL catalog)
Owner-managed pricing tiers. `name` (unique), `price_usd` (Numeric 10,2), `duration_days`, `antispam_seconds` (Numeric 6,2 — per-tenant scheduler cooldown, never below the global floor), `max_lines_per_batch`, `credits` (granted/added on assignment+renewal; 0 = time-only), `is_active` (soft-retire — referenced plans can't be deleted, `User.plan_id` is RESTRICT), `is_default` (the "basic" tier gift keys grant).
- **Partial unique:** `uq_plans_one_default(is_default) WHERE is_default` — at most one default plan. Flag a new default → clear the prior one FIRST to dodge the index. Ships EMPTY (nothing seeded).

### `gift_keys` (GLOBAL)
Single-use redeemable key. `code` (unique), `days`, `credits` (int default 0 — **admin-chosen at mint**, migration `f4b9c2e7a1d3`; the deliberate relaxation of "admin never picks a value", credits only; ADDED to `tenants.credit_balance` on claim), `plan_id` (FK RESTRICT — snapshot of the default plan; a later default change never re-prices an outstanding key). A key must grant at least one of `days`/`credits` (validated at the route, not the DB): `days==0 && credits>0` is a credits-only key. `status` (`active`/`claimed`/`revoked`). The table IS its own audit log: `created_by_user_id` / `claimed_by_user_id` / `revoked_by_user_id` (all FK SET NULL) + timestamps. Single-use enforced at claim under `SELECT … FOR UPDATE` (only `active` → `claimed`).

---

## Credential vault (GLOBAL)

### `credentials`
A personal email+password vault, **single global table — NO tenant scoping** (owner decision 2026-06-23). Created tenant-scoped in migration `e7d2c9a4b1f8`; `tenant_id` was dropped in `b3f1a7c9d2e4` (which also re-exposed pre-refactor rows once stranded under the caller's real tenant — the owner's data-recovery, no rows moved). `email` (String 320, **NOT unique** — re-saves / different passwords are intentional), `password` (🔒 **PLAINTEXT** Text — the deliberate CC / `gate_cookies` precedent; by owner request it IS echoed back on POST + GET so the holder can read saved passwords, and never logged), `used` (bool default false), `created_at` (the FIFO order — `id ASC` = creation order, drives `GET /oldest`).
- No FKs, no unique constraints, no dedup. Auth is NOT the session cookie: every endpoint requires a single shared `X-Api-Key` (`settings.credentials_api_key`) compared in constant time; unset key ⇒ vault closed (503). Reads carry `Cache-Control: no-store`. See [api-contracts.md › Credentials](./api-contracts.md).

## System / ops (GLOBAL)

### `audit_log`
One audited cross-tenant support read — written ONLY by the support view in `api/admin.py` (the single place tenant isolation is intentionally crossed). `actor_user_id` (FK SET NULL — trail survives the admin's removal), `tenant_id` (the TARGET, CASCADE), `action` (snake_case), `capture_session_id` (NO FK — historical reference, must not die/null when the client hard-deletes their session).

### `watchdog_state`
Durable latch of the watchdog's GLOBAL send pause. ONE row (id=1, app-enforced get-or-create). `paused`, `reason` (`reply_rate_collapse` | `session_lost` | `account_changed`), `detail`, `paused_at`, `resumed_at`. `account_changed` is the fail-closed latch set at boot by `services/account_guard.py` when `anon.session` was re-authed to a different Telegram account while attribution data exists. The in-process singleton (`core/watchdog.py`) is the operating authority (zero queries per worker step); this row survives a restart — CI deploys on every push, and a pause that evaporated on deploy would be the auto-resume that AC forbids.

### `system_settings`
Owner-tunable runtime config as key/value rows (hot, from the UI, no redeploy — deliberately NOT in env). `key` (PK, String 64), `value` (String 200). Keys: `max_active_senders` (admission cap; `"0"`/missing = disabled), `send_interval_seconds` (scheduler `G_min`), `telegram_account_id` (last-seen account fingerprint, written by `account_guard`), `live_forward_channel` (resolved marked chat id for Amazon-live forwarding; `""`/missing = disabled).
