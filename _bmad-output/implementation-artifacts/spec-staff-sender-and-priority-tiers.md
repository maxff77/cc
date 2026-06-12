---
status: done
slug: staff-sender-and-priority-tiers
route: plan-code-review
created: 2026-06-12
---

# Spec: Owner/admins can use the sender, with 3-tier priority

## Intent

Owner and admins currently have no UI path to the sender (Envío), so they
"can't use it". Enable it and give staff scheduler priority over clients, in a
strict ranking: **owner > admins > clients**.

## Findings (root cause)

- **Backend already permits any role to send.** `POST /api/batches` uses
  `get_current_user` (no `require_role`); the comment states *"Any
  authenticated role may send."* No backend gate blocks owner/admin sending.
- **The block is navigation.** `auth._home_path_for` lands owner/admin on
  `/admin/users`. `AdminShell` nav has only *Usuarios | Gates*; `ClientNav`
  (which wraps `/` and `/sessions`) has only *Envío | Historial*. The two navs
  are disjoint → staff have no link to reach the sender.
- **Priority is binary today.** `batches.is_owner_priority` (bool) → scheduler
  alternates owner/client at ≤50%. No middle tier for admins.

## Decisions (confirmed with user)

- Priority ranking: **owner (high) > admins (2nd) > clients (3rd)**.
- Landing unchanged (owner/admin still land on `/admin/users`); add cross-nav
  links so staff can reach Envío/Historial and clients never see admin links.

## Scope — changes

### Backend

1. **Schema**: replace `batches.is_owner_priority` (bool) with
   `batches.priority` (smallint, `0`=client / `1`=admin / `2`=owner; higher
   sends first). New migration off head `f3a9c1d4e8b7`: add column, backfill
   `priority=2 WHERE is_owner_priority`, drop the bool. Downgrade reverses it.
2. **`db/models.py`**: `priority: Mapped[int]` (`SmallInteger`,
   `server_default=text("0")`).
3. **`db/repos/batches.py`**: `create_batch(..., priority: int)`;
   `ActiveSender.priority: int`; `active_senders` selects `Batch.priority`.
4. **`api/batches.py`**: map `user.role` → priority via
   `{"owner":2,"admin":1,"client":0}` (default 0), pass `priority=`.
5. **`core/scheduler.py`**: generalize `pick_next` to **hierarchical bounded
   alternation** — owner alternates against everyone-below (≤50%); within the
   non-owner slots, admin alternates against client (≤50%). Per-tier cursors
   + `_last_was_owner` / `_last_was_admin` flags. Pure-client and 2-tier
   (owner+client only) behaviour is unchanged (existing tests still pass).

### Frontend

6. **`components/ui/admin-shell.tsx`**: prepend *Envío* (`/`) and *Historial*
   (`/sessions`) to the admin nav (AdminShell renders only for admin/owner, so
   they always show).
7. **`components/client-nav.tsx`**: fetch `["me"]` (existing pattern from
   `watchdog-notice.tsx`); when `role !== "client"` append *Usuarios*
   (`/admin/users`) and, for owner, *Gates* (`/admin/gates`). Clients see no
   admin links.

## Acceptance criteria

- **AC1** — Given an owner or admin logged in, When they view any admin page,
  Then the header nav shows Envío and Historial links that reach the sender.
- **AC2** — Given an owner or admin on the sender (`/`), When the nav renders,
  Then it shows Usuarios (and Gates for owner) links back to admin; Given a
  client, Then no admin links appear.
- **AC3** — Given owner+admin+client all sending, When the scheduler picks,
  Then owner takes ≤50% of slots, admin ≤50% of the remaining, client the rest
  (ranking owner > admin > client); owner-only takes every slot.
- **AC4** — Given an admin creates a batch, Then `batches.priority == 1`; owner
  → `2`; client → `0`.
- **AC5** — Existing scheduler/batch tests (owner-vs-client alternation,
  fairness, bounded ≤50%) still pass after the bool→tier migration.

## Out of scope

- Legacy single-tenant app (`app.py`/`core.py`/`static/`).
- Changing the owner/admin landing page.
- `scripts/load_test_gmin.py` (independent simulation harness; owner-vs-client
  bound unaffected).

## Tasks

- [ ] Migration `*_batch_priority_tier.py` (head `f3a9c1d4e8b7`)
- [ ] `db/models.py` — `priority` column
- [ ] `db/repos/batches.py` — `create_batch`, `ActiveSender`, `active_senders`
- [ ] `api/batches.py` — role→priority map + docstring
- [ ] `core/scheduler.py` — 3-tier `pick_next` + `reset`
- [ ] `components/ui/admin-shell.tsx` — Envío/Historial links
- [ ] `components/client-nav.tsx` — role-aware admin links
- [ ] Tests: `test_scheduler.py` (helper + 3-tier cases), `test_batches.py`
      (priority asserts), `test_batch_controls.py` (ActiveSender priority)
- [ ] `.venv/bin/pytest`
