---
title: 'Gate visible command (display_value) — split real command from what clients see'
type: 'feature'
created: '2026-06-16'
status: 'done'
baseline_commit: 'a0b4c0289a3878c0273ed54e55ca01465f2ce6b5'
context: ['{project-root}/CLAUDE.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A gate's `value` (e.g. `/xx`) is both the real command the engine sends AND the string shown to clients everywhere. The owner wants the real command hidden from clients, with a separate owner-authored label shown instead.

**Approach:** Add a required gate field `display_value` ("Comando visible"), distinct from `name` (friendly label) and `value` (real command). Every client-facing surface renders `display_value` where it renders `value` today; the real `value` becomes owner-only — exposed solely in `/admin/gates`, never sent to clients. The send engine keeps prepending/sending the real `value` unchanged. `display_value` is snapshotted onto batches/capture-sessions like `value`/`name` already are, so history stays correct after edits/retirement.

## Boundaries & Constraints

**Always:** `display_value` REQUIRED on create/edit; existing gates backfill `display_value = value`. Engine (`apply_gate`, send_worker) keeps sending the real `value` verbatim — no send-behavior change. Snapshot `display_value` into `batches`/`capture_sessions` at batch start; existing snapshot rows backfill from their `gate_value`. Capture-session reuse still matches on the real `gate_value`. Mirror backend validators in the frontend. Migration runs before restart.

**Ask First:** Changing `value` length/semantics, renaming `name`, or removing `value` from `/admin/gates`.

**Never:** Expose the real `value` to any non-owner surface (public `/api/gates`, client WS events, `/api/sessions*`). Do not touch legacy root app (`app.py`/`core.py`/`static/`). Do not change how the command is prepended or sent.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Behavior | Error |
|----------|--------------|-------------------|-------|
| Owner creates gate | value `/xx`, name `Visa`, display_value `Comando 01` | stored with all three; selector shows `Visa · Comando 01`; engine sends `/xx ` | blank display_value → validation error (Spanish copy) |
| Client reads catalog | GET `/api/gates` | items carry id, name, display_value, category — NO `value` | n/a |
| Client sends batch | POST with `gate_id` | server resolves real `value` from id, prepends it; client never receives `value` | unknown/retired id → `gate_not_found` (unchanged) |
| Owner edits display_value used by old session | gate.display_value changed | old session keeps its snapshotted value; new batches use the new one | n/a |
| Migration on existing data | rows lack the new columns | backfilled (value / gate_value), then NOT NULL | n/a |

</frozen-after-approval>

## Code Map

- **DB/model:** `models.py` (Gate + Batch/CaptureSession snapshots), new Alembic rev in `backend/migrations/versions/`.
- **Owner API (keeps `value`):** `api/admin.py` (schemas/validators/CRUD), `db/repos/gates.py` (`create`).
- **Client APIs (drop `value`, add `display_value`):** `api/gates.py`, `api/sessions.py`, `api/ws.py`, `services/batches.py`, `api/batches.py`, `db/repos/batches.py`, `db/repos/capture_sessions.py`.
- **Frontend:** `lib/ws.ts`, `types/api.ts`, `components/batch/send-form.tsx`, `app/admin/gates/page.tsx`, `components/sessions/active-session-card.tsx`, `app/(client)/sessions/page.tsx`, `app/(client)/sessions/[id]/page.tsx`, `app/admin/tenants/[id]/page.tsx`.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/db/models.py` -- add `Gate.display_value` (String(80), not null); `Batch.gate_display_value` + `CaptureSession.gate_display_value` (String(80), not null) snapshots.
- [x] `backend/migrations/versions/<rev>_gate_display_value.py` -- down_revision = current head: add 3 columns nullable; backfill `gates.display_value=value`, `batches/capture_sessions.gate_display_value=gate_value`; ALTER NOT NULL; `downgrade` drops them.
- [x] `backend/app/api/admin.py` -- `GATE_DISPLAY_VALUE_MAX=80` + `_validate_gate_display_value` (required, printable, ≤80; mirror `_validate_gate_name`); add `display_value` to `CreateGateRequest`/`UpdateGateRequest`/`GateOut`/`gate_to_out`; set it in `create_gate` (→repo) and `update_gate`.
- [x] `backend/app/db/repos/gates.py` -- `create(...)` gains `display_value: str`, writes it on `Gate(...)`.
- [x] `backend/app/api/gates.py` -- public list returns value-less shape (id, name, display_value, category_id, category_name); new `PublicGateOut`/mapper. Selector still submits `gate_id`.
- [x] `backend/app/api/batches.py` -- read `gate.display_value`; pass `gate_display_value` to `create_batch` and `resolve_for_batch`; `apply_gate` keeps using real `gate_value`.
- [x] `backend/app/db/repos/batches.py` & `backend/app/db/repos/capture_sessions.py` -- `create_batch`/`resolve_for_batch` accept + snapshot `gate_display_value`; reuse-match still keys on `gate_value`.
- [x] `backend/app/services/batches.py` -- `state_data` includes `gate_display_value` in `batch.state`.
- [x] `backend/app/api/sessions.py` & `backend/app/api/ws.py` -- session list/detail + WS `snapshot`/`session.active` carry `gate_display_value`; drop `gate_value` from these client-facing payloads (keep `gate_name`).
- [x] `frontend/lib/ws.ts` + `frontend/types/api.ts` + `frontend/components/batch/send-form.tsx` -- GateOut gains `display_value` (drop required client `value`); live/session field `gate_display_value` replaces `gate_value`; selector option = `name` + `display_value` (mono); active chip = `name · display_value`.
- [x] `frontend/app/admin/gates/page.tsx` -- add "Comando visible" input + `validateDisplayValue` to create + edit forms; gate row shows both `value` (real) and `display_value`.
- [x] `frontend/components/sessions/active-session-card.tsx`, `app/(client)/sessions/page.tsx`, `app/(client)/sessions/[id]/page.tsx`, `app/admin/tenants/[id]/page.tsx` -- render `display_value` where `gate_value` showed; sessions list groups by `display_value`.
- [ ] Backend test -- `_validate_gate_display_value` + gate create/update round-trip (display_value persisted; public `/api/gates` omits `value`).

**Acceptance Criteria:**
- Given an owner gate with value `/xx`, display_value `Comando 01`, when a client opens the send form, then it shows `Comando 01` and never `/xx`.
- Given that gate, when the client sends a batch, then the engine prepends/sends `/xx ` exactly as before.
- Given GET `/api/gates` as a client, when inspected, then no field contains the real `value`.
- Given the owner later edits display_value, when the client opens an older session, then it shows the display_value snapshotted at that batch's start.
- Given pre-existing data, when the migration runs, then all `display_value`/`gate_display_value` are non-null and `alembic upgrade head` succeeds.

## Verification

**Commands:**
- `cd backend && .venv/bin/alembic upgrade head` -- migration applies cleanly on a populated DB.
- `cd backend && .venv/bin/pytest` -- all tests pass, including new gate display_value tests.
- `cd frontend && npm run build` -- type-checks + builds (lint alone insufficient — tsc runs in build).

**Manual checks:**
- Client: send form + session header/active card show `display_value`, never the raw command. Owner: `/admin/gates` shows + edits both `value` and `Comando visible`.

## Suggested Review Order

**Data model & migration**

- Entry point: the new owner-only `value` vs client-visible `display_value` split.
  [`models.py:181`](../../backend/app/db/models.py#L181)

- History-safe snapshots on batch + capture-session (same idiom as gate_value/name).
  [`models.py:277`](../../backend/app/db/models.py#L277)

- Three-step migration: add nullable → backfill from value/gate_value → NOT NULL.
  [`a9d4e6c2f813:27`](../../backend/migrations/versions/a9d4e6c2f813_gate_display_value.py#L27)

**Owner API (still holds the real value)**

- `display_value` validator (required, ≤80, printable) mirroring the name rule.
  [`admin.py:520`](../../backend/app/api/admin.py#L520)

- `GateOut` keeps `value` and adds `display_value`; create/update write it.
  [`admin.py:579`](../../backend/app/api/admin.py#L579)

**Hiding the real value from every client surface**

- New value-less public catalog shape (clients pick by id).
  [`gates.py:28`](../../backend/app/api/gates.py#L28)

- WS payloads emit `gate_display_value`, never `gate_value`.
  [`batches.py:112`](../../backend/app/services/batches.py#L112)

- `BatchOut` + `SessionOut` carry display only.
  [`batches.py:92`](../../backend/app/api/batches.py#L92)
  [`sessions.py:60`](../../backend/app/api/sessions.py#L60)

- Export filename now slugs from display, closing the last value leak.
  [`exports.py:73`](../../backend/app/services/exports.py#L73)

**Engine unchanged (load-bearing)**

- Resolves `display_value` for snapshots but still sends the REAL `value`.
  [`batches.py:146`](../../backend/app/api/batches.py#L146)

**Frontend binding**

- Store renamed `gateValue`→`gateDisplayValue` / `sessionDisplayValue`.
  [`ws.ts:74`](../../frontend/lib/ws.ts#L74)

- Selector + live-batch label use display; append-id match is a soft fallback.
  [`send-form.tsx:131`](../../frontend/components/batch/send-form.tsx#L131)

- Owner catalog: new "Comando visible" field + shows both Real and Visible.
  [`gates/page.tsx:107`](../../frontend/app/admin/gates/page.tsx#L107)

**Tests**

- Validator + create/update round-trip + public-omits-value.
  [`test_gate_display_value.py:66`](../../backend/tests/test_gate_display_value.py#L66)
