"""Data access for capture sessions (Story 3.1; Historial reads/deletes 3.3).

TENANT-SCOPED — this is NOT the gates/users global exception: every function
takes ``tenant_id`` explicitly. Callers are the batches handler (binding at
batch start), the Historial router (3.3) and the capture/attribution pipeline
— the latter runs OUTSIDE any request (like the worker section of
repos/batches.py, the documented exception) but still resolves one explicit
``tenant_id`` per call, derived from ``send_log``, never from a request.

Pure ORM, flush not commit — callers own the transaction.
"""

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CaptureSession, Response


async def get_active(
    session: AsyncSession, tenant_id: int, *, for_update: bool = False
) -> CaptureSession | None:
    """The tenant's single ACTIVE capture session, or ``None``.

    At most one exists — DB-enforced by the partial unique index
    ``uq_capture_sessions_one_active_per_tenant``.

    ``for_update=True`` locks the active row until commit (same knob as
    ``get_for_tenant``). ``new_session`` passes it so it serializes against a
    concurrent Nueva-sesión / Continuar / batch-start the way
    ``continue_session`` locks its target — instead of leaning only on the
    partial unique index tripping at commit.
    """
    stmt = select(CaptureSession).where(
        CaptureSession.tenant_id == tenant_id,
        CaptureSession.is_active,
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalars().first()


async def ensure_perpetual(
    session: AsyncSession, tenant_id: int
) -> CaptureSession:
    """Get-or-create the tenant's ONE ever-living capture session (sessionless
    cockpit, PR-1) — a PURE singleton, no rotation/activation churn.

    Exactly one ``is_active=true`` row exists per tenant for its whole life: the
    cockpit no longer rotates sessions per gate, so there is by definition no
    "prior active row to clear" — the "clear prior row FIRST" flip pattern of
    ``create_active``/``activate`` does NOT apply here. The partial unique index
    ``uq_capture_sessions_one_active_per_tenant`` guards ONLY the single
    first-ever-creation race; this code does NOT run a deactivate-all UPDATE and
    never touches ``is_active``/``id`` on an existing row.

    SELECT the active row FOR UPDATE → return it if found; else INSERT a fresh
    ``is_active=true`` row with EMPTY gate snapshots (``resolve_for_batch``
    refreshes them in place on the first batch). On a concurrent first-ever
    INSERT the partial index trips ⇒ rollback + re-SELECT returns the single
    winning row. Flush-not-commit — the caller owns the transaction (and owns
    the IntegrityError fallback if it batches more work into the same txn).
    """
    active = await get_active(session, tenant_id, for_update=True)
    if active is not None:
        return active
    capture_session = CaptureSession(
        tenant_id=tenant_id,
        gate_value="",
        gate_name="",
        gate_display_value="",
        is_active=True,
    )
    session.add(capture_session)
    try:
        await session.flush()
    except IntegrityError:
        # Lost the first-ever-creation race — the single row exists now.
        await session.rollback()
        existing = await get_active(session, tenant_id, for_update=True)
        if existing is None:  # not the one-active conflict — surface it
            raise
        return existing
    return capture_session


async def list_for_tenant(
    session: AsyncSession, tenant_id: int
) -> list[CaptureSession]:
    """ALL of the tenant's sessions, newest first (Story 3.3 Historial list).

    No pagination — MVP scale (NFR2); the grouping by gate is presentation
    (client-side).
    """
    stmt = (
        select(CaptureSession)
        .where(CaptureSession.tenant_id == tenant_id)
        .order_by(CaptureSession.id.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_for_tenant(
    session: AsyncSession,
    tenant_id: int,
    session_id: int,
    *,
    for_update: bool = False,
) -> CaptureSession | None:
    """TENANT-SCOPED lookup by id (Story 3.3 detail/rename/delete).

    Another tenant's id returns ``None`` (the handler 404s — existence is
    never leaked; exact mirror of ``batches_repo.get_batch``).

    ``for_update=True`` locks the row until commit (same knob as
    ``batches_repo.get_batch``). The DELETE path must pass it so it
    serializes with a concurrent ``POST /api/batches`` binding this same
    session: the batch INSERT's FK check takes ``FOR KEY SHARE`` on this row,
    which conflicts with ``FOR UPDATE`` — see ``delete_session``.
    """
    stmt = select(CaptureSession).where(
        CaptureSession.id == session_id,
        CaptureSession.tenant_id == tenant_id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalars().first()


async def delete(
    session: AsyncSession, capture_session: CaptureSession
) -> None:
    """Hard-delete one capture session (Story 3.3 AC 5).

    Zero manual child cleanup: ``responses`` rows die via the DB FK CASCADE
    (models.py) and batches survive with ``capture_session_id`` NULL (FK SET
    NULL) — the lote history is the lote's, not the session's.
    """
    await session.delete(capture_session)
    await session.flush()


async def create_active(
    session: AsyncSession,
    tenant_id: int,
    gate_value: str,
    gate_name: str,
    gate_display_value: str,
    special_mode: bool = False,
    cookie_mode: bool = False,
) -> CaptureSession:
    """Deactivate the previous active session and insert the new active one.

    Activation by replacement — legacy: "sessions are replaced by
    reassignment", never closed. The UPDATE runs first so the partial unique
    index (the belt) never trips on the honest path. ``gate_display_value`` is
    the client-visible "Comando visible" snapshot; ``special_mode`` snapshots
    the gate category's special-mode flag; ``cookie_mode`` snapshots its
    cookie-vault flag (cookie-vault feature — the snapshot WRITE path; the
    reader is Phase 2).
    """
    await session.execute(
        update(CaptureSession)
        .where(CaptureSession.tenant_id == tenant_id, CaptureSession.is_active)
        .values(is_active=False)
    )
    capture_session = CaptureSession(
        tenant_id=tenant_id,
        gate_value=gate_value,
        gate_name=gate_name,
        gate_display_value=gate_display_value,
        special_mode=special_mode,
        cookie_mode=cookie_mode,
        is_active=True,
    )
    session.add(capture_session)
    await session.flush()
    return capture_session


async def activate(
    session: AsyncSession, capture_session: CaptureSession
) -> None:
    """Reactivate an EXISTING session as the tenant's active one (Story 3.4).

    Mirror of ``create_active``'s UPDATE-first dance (the partial unique
    index ``uq_capture_sessions_one_active_per_tenant`` never trips on the
    honest path) — but flipping an existing row instead of inserting.

    The ``id != capture_session.id`` exclusion in the UPDATE is load-bearing:
    if the UPDATE included the target, "continuing" the ALREADY-active session
    would set ``is_active=False`` in the DB while the loaded ORM instance
    stays ``True`` in memory — the ``True → True`` assignment registers no
    change, the flush emits no UPDATE and the row would end up INACTIVE. With
    the exclusion the already-active path is a clean no-op (idempotent) and
    the closed→active path registers ``False → True`` and flushes.
    """
    await session.execute(
        update(CaptureSession)
        .where(
            CaptureSession.tenant_id == capture_session.tenant_id,
            CaptureSession.is_active,
            CaptureSession.id != capture_session.id,
        )
        .values(is_active=False)
    )
    capture_session.is_active = True
    await session.flush()


async def resolve_for_batch(
    session: AsyncSession,
    tenant_id: int,
    gate_value: str,
    gate_name: str,
    gate_display_value: str,
    special_mode: bool = False,
    cookie_mode: bool = False,
) -> CaptureSession:
    """Bind a new batch to the tenant's ONE perpetual capture session
    (sessionless cockpit, PR-1) — get-or-create, then refresh the gate snapshots
    IN PLACE.

    The cockpit no longer rotates sessions per gate: a gate change REUSES the
    same session (no second row, no ``is_active``/``id`` churn) and just
    overwrites the ``gate_value``/``gate_name``/``gate_display_value`` +
    ``special_mode``/``cookie_mode`` snapshots so the capture pipeline parses
    THIS batch's replies under the right gate. The CC dedup widens to
    tenant-lifetime by construction (one ``capture_session_id`` for the tenant's
    whole life) — accepted invariant.

    Per the PR-2 note: the per-batch gate snapshot is overwritten here, so
    history MUST key on ``responses.batch_id → batches.gate_*``, not this
    session snapshot — the batch keeps carrying its own immutable gate columns.
    """
    active = await ensure_perpetual(session, tenant_id)
    changed = False
    if active.gate_value != gate_value:
        active.gate_value = gate_value
        changed = True
    if active.gate_name != gate_name:
        active.gate_name = gate_name
        changed = True
    if active.gate_display_value != gate_display_value:
        active.gate_display_value = gate_display_value
        changed = True
    if active.special_mode != special_mode:
        active.special_mode = special_mode
        changed = True
    if active.cookie_mode != cookie_mode:
        active.cookie_mode = cookie_mode
        changed = True
    if changed:
        await session.flush()
    return active


async def resolve_for_backfill(
    session: AsyncSession,
    tenant_id: int,
    gate_value: str,
    gate_name: str,
    gate_display_value: str,
    cookie_mode: bool = False,
) -> CaptureSession | None:
    """Late-reply backfill (attribution path) — READ-ONLY (sessionless cockpit,
    PR-1).

    🔒 The capture consumer must NEVER INSERT or activate a session: a partial-
    index IntegrityError here would bubble to the capture poison-drop path and
    LOSE the reply. So this is a plain SELECT of the tenant's perpetual active
    session; if none exists yet (a reply arriving before the tenant's first
    batch ever ran ``ensure_perpetual``), it returns ``None`` and the caller
    defers to the existing ``send_log``/``batch`` attribution WITHOUT inserting
    or activating anything — activation stays an API-only act at batch start.

    The gate args are accepted for caller signature parity (the old fallback
    snapshotted them onto an INACTIVE row); they are unused now that the path
    never inserts.
    """
    return await get_active(session, tenant_id)


async def clear_view(
    session: AsyncSession, capture_session: CaptureSession
) -> int | None:
    """Stamp the cockpit "Limpiar" view-cutoff (sessionless cockpit, PR-1).

    Sets ``cleared_response_id = MAX(responses.id)`` so the DISPLAY reads (and
    ONLY the display reads) hide every row with ``Response.id <= cutoff``. This
    is the ``hidden_at`` discipline as an ``id`` HIGH-WATER-MARK: it deletes
    ZERO ``responses`` rows (the approved-✅ history survives for PR-2) and is
    invisible to every integrity / attribution / reconciler / dedup / credit /
    ``awaiting_reply`` query. ``id`` (monotonic) — not ``created_at`` (txn-start
    ``now()`` ties) — makes the cutoff tie-immune.

    ``MAX(responses.id)`` is the GLOBAL high-water-mark across the whole table:
    ids are monotonic, so any row already captured for this session has
    ``id <= MAX`` and is hidden, and any row captured AFTER the clear gets a
    larger id and reappears. Returns the new cutoff (``None`` only if the
    ``responses`` table is empty). Flush-not-commit — the caller owns the txn.

    ponytail: ``MAX`` sees only COMMITTED rows, and the capture consumer commits
    in its OWN transaction. A reply mid-capture (id already allocated from the
    sequence, not yet committed) can land just above the stamped cutoff and
    reappear ONCE in the cockpit. Strictly a transient DISPLAY glitch (the cutoff
    touches display reads only — never integrity/attribution/dedup), self-heals
    on the next Limpiar (a higher MAX hides it). Not worth an advisory lock on
    this rare, rate-paced, single-consumer path; revisit only if it ever bites.
    """
    cutoff = (
        await session.execute(select(func.max(Response.id)))
    ).scalar_one_or_none()
    capture_session.cleared_response_id = cutoff
    await session.flush()
    return cutoff
