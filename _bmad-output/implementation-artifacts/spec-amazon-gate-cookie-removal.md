---
title: 'Amazon gate — cookie removal: fix manual delete + auto-purge dead cookies'
type: 'bugfix'
created: '2026-06-19'
status: 'done'
baseline_commit: '22f63933d32dd2de87cf540bad611f0ba9d0b5e2'
context: ['{project-root}/CLAUDE.md', '{project-root}/_bmad-output/implementation-artifacts/spec-amazon-gate-send-rotation.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Two cookie-vault defects on the Amazon (cookie-mode) gate. (1) Manual cookie deletion fails with "Ocurrió un error inesperado.": the worker stamps `BatchLine.failed_cookie_id = <sent cookie>` on every cookie-mode send (`send_worker.py:636`), but the FK `fk_batch_lines_failed_cookie_id_gate_cookies` was created with NO `ondelete` (Postgres default RESTRICT), so deleting any already-sent cookie raises `ForeignKeyViolation`; `delete_cookie` doesn't catch it → unmapped 500 → the frontend can't parse `{code,message}` and shows the generic fallback. (2) On a dead-cookie verdict the rotation `mark_dead`s the cookie (kept greyed); the owner wants the bad cookie **deleted from the vault** (owner choice 2026-06-19), then rotation continues to the next cookie and pauses `cookies_exhausted` when none remain.

**Approach:** Change the FK to `ON DELETE SET NULL` (one migration) — this nulls the diagnostic `failed_cookie_id` on referencing lines and unblocks BOTH manual delete and engine purge. In `_apply_verdict`'s `cookie_dead` branch, swap `mark_dead` for the existing tenant-scoped hard `delete_by_id`; the rest of the rotation/exhaustion machinery is unchanged. Harden `delete_cookie` to never 500, and refetch the vault list on mount so purges show.

## Boundaries & Constraints

**Always:**
- **All Phase-2 rotation invariants survive.** This is a surgical `mark_dead`→`delete_by_id` swap inside the SAME one-txn, batch-`FOR UPDATE`, attempt-fenced `cookie_dead` branch (`send_worker.py:999-1040`). Keep: the attempt-fence (`awaiting_message_id`), the `state==SENDING` guard, reading the cookie id from `line.failed_cookie_id` (the cookie ACTUALLY sent — never re-derive "oldest active"), re-queue-with-intent-reset, the exhaustion pause.
- **Hard-delete is tenant-scoped:** `gate_cookies_repo.delete_by_id(session, tenant_id, dead_cookie_id)` (arg order differs from `mark_dead`). `tenant_id` from the locked line/batch, never body/path.
- **The FK migration is the real fix.** `failed_cookie_id` is diagnostic-only (no relationship). Drop + recreate the named constraint with `ondelete='SET NULL'`; `down_revision='a7c3e9f1b204'` (current head — verify `alembic heads`); ships before restart.
- **Purge-txn ordering:** capture `dead_cookie_id = line.failed_cookie_id` first → `delete_by_id` → set `line.failed_cookie_id = None` in the ORM (match the DB SET NULL; no stale-id UPDATE) → `count_active_for` (now sees the row gone → exhaustion decision) → re-queue. One commit under the batch `FOR UPDATE`. The next attempt's `_arm_await` re-stamps the new cookie.
- **`delete_cookie` never 500s:** wrap delete+commit so an `IntegrityError` rolls back to a mapped `{code,message}`, never an unmapped 500. Value never logged/echoed (existing contract).
- **Idempotency holds via the attempt-fence, not `mark_dead`.** A replayed dead verdict for the now-superseded `message_id` is dropped by the fence — cookie deleted exactly once.
- Migrations before restart; Telethon stays in `core/telegram.py`; legacy `app.py`/`core.py`/`static/` untouched.

**Never:**
- No second migration to drop the `status` column or `mark_dead`/rotation helpers — surviving rows stay `status='active'`, so the `status='active'` filters in `get_active_for_rotation`/`count_active_for` remain correct. `mark_dead` just becomes unused (remove its call; deleting the helper is optional cleanup).
- Do NOT touch classification (`parse_amazon_verdict`), Approved/Declined/format-error paths, the verdict-timeout sweep, the `.cookie` send, the serialize gate, or any non-cookie-mode path.
- No new WS event — the list refetch-on-mount covers visibility. The worker purge deletes via the repo, NOT via the HTTP endpoint.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Behavior | Error Handling |
|----------|--------------|-------------------|----------------|
| Delete a used cookie | `DELETE /api/cookies/{id}`, referenced by ≥1 `failed_cookie_id` | 204; row gone, references SET NULL by the FK; vault (next mount) drops it | no FK violation; any `IntegrityError` → rollback + mapped error, never 500 |
| Delete unknown/foreign cookie | not owned | 404 `cookie_not_found` (unchanged no-op) | unchanged |
| Dead verdict, another cookie remains | cookie-mode batch, `cookie_dead`, ≥1 other cookie | sent cookie DELETED; same line re-queued; next `step()` resends behind next-oldest cookie (new `message_id`); dead attempt still attributed; Completa shows line once | one-txn under batch `FOR UPDATE` |
| Dead verdict, last cookie | only cookie dies | cookie deleted; `count_active_for==0` → pause `cookies_exhausted`, emit `batch.state`, re-queue line, clear await | after client adds cookie + resumes, the failed line sends next |
| Replayed dead verdict | reconciler re-feeds same reply | attempt-fence drops it; cookie deleted exactly once, no double-rotation/exhaustion | idempotent via the fence |
| Vault visibility | engine purged a cookie while manager unmounted | on `CookieManager` re-mount (idle or in `cookies_exhausted` notice) the list refetches; purged cookie gone | `refetchOnMount:"always"` overrides 30s `staleTime` |
| Approved/Declined/format-error, non-cookie-mode | — | unchanged — no delete, no rotation | unchanged |

</frozen-after-approval>

## Code Map

- `backend/app/db/models.py` — `BatchLine.failed_cookie_id` (L511-513): `ForeignKey("gate_cookies.id", ondelete="SET NULL")`; fix the now-wrong "no `ondelete`" comment.
- `backend/migrations/versions/c4e7a2f9b1d6_cookie_fk_ondelete_setnull.py` — NEW; `down_revision='d3f1a8c5e9b2'` (the REAL head — `a7c3e9f1b204` already had a child, see Change Log). upgrade: `drop_constraint('fk_batch_lines_failed_cookie_id_gate_cookies','batch_lines',type_='foreignkey')` then `create_foreign_key(...,['failed_cookie_id'],['id'],ondelete='SET NULL')`. downgrade reverses.
- `backend/app/core/send_worker.py` — `_apply_verdict` `cookie_dead` branch (L999-1040): `mark_dead`→`delete_by_id(session, tenant_id, dead_cookie_id)`; add `line.failed_cookie_id = None` before re-queue; fix comments referencing `mark_dead`/flushed-dead-status.
- `backend/app/api/cookies.py` — `delete_cookie` (L268-287): catch `IntegrityError` → rollback + mapped error (reuse `cookie_conflict_retry` or a small factory), never 500.
- `backend/app/db/repos/gate_cookies.py` — reuse `delete_by_id` for the purge; `mark_dead` becomes unused (no signature change).
- `frontend/lib/cookies.ts` — `useListCookies` (L23-32): add `refetchOnMount: "always"`.
- `backend/tests/test_amazon_rotation.py` — flip rotation assertions from `status='dead'` to "cookie row deleted"; keep exhaustion/idempotency assertions.
- `backend/tests/test_cookies.py` — add: deleting a cookie referenced by a `failed_cookie_id` returns 204 and nulls the reference (bug-#1 regression).

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/db/models.py` — FK gains `ondelete="SET NULL"` + corrected comment — schema half of the unblock.
- [x] `backend/migrations/versions/c4e7a2f9b1d6_cookie_fk_ondelete_setnull.py` — NEW; drop+recreate the FK with `ondelete='SET NULL'`, `down_revision='d3f1a8c5e9b2'` (real head) — applies before restart.
- [x] `backend/app/core/send_worker.py` — `cookie_dead` branch: `delete_by_id` instead of `mark_dead`, sync `line.failed_cookie_id=None`, fix comments — the purge-on-dead behavior; rotation/exhaustion intact.
- [x] `backend/app/api/cookies.py` — `delete_cookie` catches `IntegrityError` → mapped error — never 500.
- [x] `frontend/lib/cookies.ts` — `useListCookies` `refetchOnMount:"always"` — purged cookies vanish on next mount.
- [x] `backend/tests/test_amazon_rotation.py` + `backend/tests/test_cookies.py` — rotation asserts DELETED (not `dead`); new test: deleting a referenced cookie returns 204 — locks both fixes.

**Acceptance Criteria:**
- Given a cookie sent at least once (a `failed_cookie_id` references it), when the client deletes it, then 204, the row is gone, the reference is NULL, and no 500/"error inesperado".
- Given a cookie-mode batch with two active cookies and a `cookie_dead` verdict, when applied, then the sent cookie is removed from the vault and the line resends behind the next-oldest cookie (new `message_id`, dead attempt attributed, Completa shows it once).
- Given the last active cookie dies, when applied, then the cookie is deleted, the batch pauses `pause_reason='cookies_exhausted'`, the cockpit shows the add-cookies prompt, and after add+resume the failed line sends next.
- Given the same dead verdict re-fed by the reconciler, when processed, then the fence drops it and the cookie is deleted exactly once.
- Given `alembic upgrade head` then the full suite, when run, then the migration applies cleanly and no Phase-1/Phase-2 or non-cookie-mode test regresses.

## Spec Change Log

### 2026-06-19 — implementation corrections (no intent change)

- **Migration chain head.** The frozen Boundaries said `down_revision='a7c3e9f1b204'` "(verify `alembic heads`)". Verification showed the real head is `d3f1a8c5e9b2` (`batch_line_verdict_timeout_retries`, itself a child of `a7c3e9f1b204`). Chained the new migration `c4e7a2f9b1d6` behind `d3f1a8c5e9b2` to keep a single linear head (avoided a two-head branch). Confirmed `alembic heads` → one head; `alembic upgrade head` applied; `pg_constraint.confdeltype='n'` (SET NULL).
- **Bug-#1 regression test placement.** Put the "delete a sent-and-referenced cookie returns 204" test in `tests/test_amazon_rotation.py` (`test_delete_cookie_referenced_by_sent_line_returns_204`) instead of `tests/test_cookies.py` — stamping `failed_cookie_id` requires a real cookie-mode send, whose machinery (`_post_batch`/`send_worker.step()`/`fake_gateway`) lives in the rotation suite. `test_cookies.py` is unchanged.

### 2026-06-19 — review round 1 (blind + edge-case + acceptance, Opus)

No `intent_gap` / `bad_spec` → no loopback. Acceptance auditor: all 5 ACs PASS, no boundary violations, no scope creep. Blind hunter's findings (down_revision, constraint-name, IntegrityError import, stale-ORM, 409 copy) were all unverifiable-without-project-access assumptions, each confirmed safe by the grounded reviewers. Two LOW patches applied:

- **patch (edge LOW1):** `tests/test_amazon_rotation.py` `_drop_gate` teardown manually NULLed `failed_cookie_id` with a comment claiming "no ON DELETE" — now false under SET NULL. Removed the redundant pre-NULL block + fixed the comment (matches `test_cookies._drop_gate`).
- **patch (edge LOW2):** `app/db/repos/gate_cookies.py` `mark_dead` became dead code after the `delete_by_id` swap. Removed `mark_dead` + the now-unused `update` import; refreshed the rotation header + `exclude_id` docstrings to describe hard-delete (kept `exclude_id` param, `COOKIE_DEAD` const, and the `status='active'` filters as the spec's "Never drop the status concept" requires).

Rejected: edge LOW3 (`CookieOut.status` vestigial — reviewer said no fix needed; removing it is API scope creep) and a pre-existing `B904` at `cookies.py:235` (`store_cookie`, untouched).

## Design Notes

**One FK change fixes both bugs.** Manual delete and engine purge both hard-delete a `gate_cookies` row a `failed_cookie_id` may reference. `ON DELETE SET NULL` auto-nulls that diagnostic id at the DB (the owner accepted losing the "which cookie killed which line" trace). With the FK fixed, the purge is a literal `mark_dead`→`delete_by_id` swap; the attempt-fenced one-txn branch is otherwise preserved. The owner chose delete over greyed `dead` rows for a clean vault; the `status` column/filters are left in place (harmless, all rows `active`) to avoid a second migration.

## Verification

**Commands:**
- `cd backend && .venv/bin/alembic upgrade head` — applies; FK shows `ON DELETE SET NULL` (`\d batch_lines`).
- `cd backend && .venv/bin/pytest tests/test_amazon_rotation.py tests/test_cookies.py` — rotation deletes the cookie; exhaustion+resume; idempotent replay; referenced-cookie delete returns 204.
- `cd backend && .venv/bin/pytest` — no regression.
- `cd frontend && npm run build` — `tsc` + build pass.

**Manual checks:**
- Cockpit: delete a saved cookie that was used → it disappears, no "error inesperado".
- Cookie-mode send with a bad cookie → cookie gone from the vault after the verdict; with another cookie present the send continues on it; with none left the batch pauses with the add-cookies prompt.

## Suggested Review Order

**Schema unblock — the one root fix (entry point)**

- The FK that gates everything: `ON DELETE SET NULL` is what lets a referenced cookie be deleted at all.
  [`models.py:515`](../../backend/app/db/models.py#L515)
- The migration that applies it — drop + recreate the named constraint; `down_revision` is the real head `d3f1a8c5e9b2`.
  [`c4e7a2f9b1d6:36`](../../backend/migrations/versions/c4e7a2f9b1d6_cookie_fk_ondelete_setnull.py#L36)

**Rotation purge — the behavior change**

- `mark_dead`→`delete_by_id` + ORM `failed_cookie_id=None` sync, one txn under the batch `FOR UPDATE`; exhaustion path unchanged.
  [`send_worker.py:1011`](../../backend/app/core/send_worker.py#L1011)

**Endpoint hardening — never 500 again**

- The DELETE handler maps any `IntegrityError` to `cookie_delete_failed` (defense-in-depth; `cookie_not_found` still propagates as 404).
  [`cookies.py:292`](../../backend/app/api/cookies.py#L292)

**Frontend visibility**

- `refetchOnMount:"always"` so the vault drops engine-purged cookies on the manager's next mount despite the 30s staleTime.
  [`cookies.ts:37`](../../frontend/lib/cookies.ts#L37)

**Peripherals — tests**

- Bug-#1 regression: deleting a sent-and-referenced cookie returns 204 (the case the old RESTRICT FK 500'd).
  [`test_amazon_rotation.py:576`](../../backend/tests/test_amazon_rotation.py#L576)
