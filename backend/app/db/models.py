"""ORM models.

Tables arrive story by story via Alembic migrations (no tables ahead of need):
tenants/users/auth_sessions (1.2+), gates (2.1), gate_categories + batches +
batch_lines (2.2), send_log (2.5), capture_sessions + responses (3.1),
audit_log (3.6), watchdog_state (4.1), system_settings (4.2).
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # Per-tenant credit balance (credits feature). Costed gates (``Gate.
    # credit_cost > 0``) debit this once per captured ✅; plan assignment/renewal
    # and owner recharge credit it. Lives on the TENANT (not the User) because
    # the capture pipeline is tenant-keyed — the charge happens outside any
    # request and needs no tenant→user resolution. Default 0: existing tenants
    # start at 0 and are simply blocked from costed gates until granted credits.
    credit_balance: Mapped[int] = mapped_column(
        server_default=text("0"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    users: Mapped[list["User"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", passive_deletes=True
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # 'owner' | 'admin' | 'client' — enforced in app logic (Story 1.3)
    role: Mapped[str] = mapped_column(String(20))
    # Read at login (Story 1.2); the admin action that sets it is Story 1.5.
    is_blocked: Mapped[bool] = mapped_column(
        Boolean, server_default=false(), nullable=False
    )
    # Set by the admin password-reset action (Story 1.6); read at auth time.
    # While True, get_current_user 403s everything except change-password.
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, server_default=false(), nullable=False
    )
    # plan expiry; set at client creation = now + plan_days. Enforcement/lockout
    # is Story 1.4. Nullable: owner/admin rows carry no plan; only 'client' rows
    # get an expires_at.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The owner-managed pricing plan this client is on (plan-catalog feature).
    # Nullable: owner/admin rows carry no plan, and a client with plan_id NULL
    # falls back to the legacy behavior (global send interval, no line cap).
    # RESTRICT (not SET NULL/CASCADE): a plan referenced by ≥1 user cannot be
    # deleted — the service guards it explicitly (plan_in_use) and the DB
    # enforces it too, so historical assignments never dangle. Retire a plan
    # via ``is_active=false`` instead of deleting it.
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("plans.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    # Telegram handle for renewal outreach (sin '@'; un solo formato canónico).
    # Optional — clientes previos quedan NULL hasta que se llene.
    contact: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant: Mapped["Tenant"] = relationship(back_populates="users")
    auth_sessions: Mapped[list["AuthSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Opaque cookie value (secrets.token_urlsafe(32) ≈ 43 chars). The cookie
    # carries only this token; the server resolves it — unguessable + DB-backed,
    # so no signing is needed.
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Set on logout/revocation; a session is valid iff revoked_at IS NULL.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="auth_sessions")


class GateCategory(Base):
    """Owner-managed category for gates (Story 2.2 owner addition).

    GLOBAL catalog like ``gates`` — no tenant scoping. Plain unique ``name``
    (NO soft-delete: deleting a category that still has gates is rejected at
    the API with ``category_in_use``; the FK below is ``RESTRICT`` so the DB
    enforces it too). Categories are a browsing aid — batches never snapshot
    them.
    """

    __tablename__ = "gate_categories"
    __table_args__ = (UniqueConstraint("name", name="uq_gate_categories_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    # Owner toggle (special-mode feature): gates in this category capture in
    # "special mode" — reply status comes from the ``Approveds! ✅: N`` count
    # (N≥1 ⇒ ok) instead of bare ✅-glyph presence, and the Approveds!/Deads!
    # stats segments are stripped from the stored reply. Snapshotted onto the
    # CaptureSession at batch start (the capture pipeline reads the snapshot,
    # never this row), so toggling it never rewrites in-flight captures.
    special_mode: Mapped[bool] = mapped_column(
        Boolean, server_default=false(), nullable=False
    )
    # Owner toggle (cookie-vault feature, Phase 1): gates in this category run
    # in "cookie mode" — the client stores per-account cookies (``gate_cookies``)
    # that Phase 2 will prepend before each line. Phase 1 is the vault only; this
    # boolean drives (a) the cockpit's cookie-manager visibility via the public
    # gate payload and (b) the POST /api/cookies cookie-mode gate. Snapshotted
    # onto the CaptureSession at batch start (same idiom as ``special_mode``),
    # so toggling it never rewrites in-flight sessions; the snapshot READER is
    # Phase 2 (no capture-pipeline code reads it yet).
    cookie_mode: Mapped[bool] = mapped_column(
        Boolean, server_default=false(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Gate(Base):
    """Catalog entry for a gate (Story 2.1).

    GLOBAL catalog — intentionally NO tenant_id: the owner curates one shared
    list for all tenants. ``value`` is stored verbatim with its dot (e.g.
    ``.zo``). Soft-delete via ``deleted_at`` (NULL = active): retiring an entry
    hides it from selectors but keeps the row, since batches/sessions snapshot
    the gate string at creation and history must never be rewritten.
    """

    __tablename__ = "gates"
    __table_args__ = (
        # Uniqueness among ACTIVE entries only — a retired value can be
        # re-created as a new active row.
        Index(
            "uq_gates_value_active",
            "value",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # The REAL command the engine prepends + sends. OWNER-ONLY: never exposed
    # to clients (public /api/gates omits it; only /admin/gates shows it). The
    # client-visible string is ``display_value`` instead.
    value: Mapped[str] = mapped_column(String(20))
    # Friendly label shown to clients. Required; not unique — two gates may
    # share a name, ``value`` is the identity. (Story 2.2 owner decision:
    # clients see name + category + display_value.)
    name: Mapped[str] = mapped_column(String(80))
    # Owner-authored "Comando visible": the string clients see EVERYWHERE the
    # real ``value`` used to show (selector, session headers, history). Decoupled
    # from ``value`` on purpose — clients must never see the real command.
    # Required; not unique. Backfilled from ``value`` on existing rows.
    display_value: Mapped[str] = mapped_column(String(80))
    # Credits charged per captured ✅ for a batch on this gate (credits
    # feature). 0 (default) ⇒ the gate is free — no charge, no balance gate.
    # >0 ⇒ each first-✅ on a line of a batch using this gate debits the
    # tenant's ``credit_balance``, and a tenant at balance 0 is blocked from
    # starting/appending a batch here. Snapshotted onto ``Batch.gate_credit_cost``
    # at batch creation so editing this later never re-prices live/old batches.
    credit_cost: Mapped[int] = mapped_column(
        server_default=text("0"), nullable=False
    )
    # Every gate belongs to exactly one category (Story 2.2). RESTRICT: a
    # category cannot be dropped while gates (active OR retired) reference it.
    category_id: Mapped[int] = mapped_column(
        ForeignKey("gate_categories.id", ondelete="RESTRICT"), index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # NO lazy default on purpose: async lazy-loads raise MissingGreenlet.
    # Loaders eager-load explicitly (``selectinload``) or refresh the attribute.
    category: Mapped["GateCategory"] = relationship()


class GateCookie(Base):
    """One stored per-account cookie for a tenant on a cookie-mode gate
    (cookie-vault feature, Phase 1).

    TENANT-SCOPED — unlike the global ``gates``/``gate_categories`` catalog,
    every cookie belongs to exactly one tenant: a client stores, lists and
    deletes only their own. ``tenant_id`` always comes from the session at the
    route, never from body/path.

    🔒 ``value`` holds the credential PLAINTEXT in Postgres — the deliberate CC
    precedent (access-control + TLS at rest); real encryption is Phase 2. The
    value is NEVER echoed to a client (the API returns a masked form only) and
    is never logged. ``value_hash`` is the sha256 hex of the canonical
    (``value.strip()``) string: the unique index keys on the HASH, not the
    value, because a cookie can exceed the ~2704-byte btree row limit that the
    ``uq_responses_session_msg_cc`` text index runs into. Dedup is DB-enforced by
    ``uq_gate_cookies_tenant_gate_hash`` — store-first / catch-IntegrityError,
    never SELECT-then-INSERT.

    ``gate_id`` has an FK to ``gates`` but NO ``ondelete`` and no relationship:
    gates are SOFT-deleted (``deleted_at``), so a retired gate's row persists and
    its cookies stay listable/deletable — the vault outlives an *active* gate,
    mirroring the batch/session snapshot stance. ``status`` is reserved for
    Phase-2 rotation
    (``'active'`` on every row, no reader yet) — plain String, no DB enum (the
    2.2 decision).
    """

    __tablename__ = "gate_cookies"
    __table_args__ = (
        # Per-(tenant, gate) dedup, guaranteed by Postgres — not by code. Keyed
        # on the sha256 hash (not the raw value) so an oversized cookie still
        # fits the btree; mirrors the ``uq_responses_session_msg_cc`` precedent but
        # over a fixed-width hash instead of a length-truncated text.
        Index(
            "uq_gate_cookies_tenant_gate_hash",
            "tenant_id",
            "gate_id",
            "value_hash",
            unique=True,
        ),
        # FIFO active-cookie pick (Phase 2 rotation): the worker selects the
        # oldest ``status='active'`` cookie by ``id ASC`` for ``(tenant, gate)``
        # (``repos.gate_cookies.get_active_for_rotation``). This composite index
        # keeps that pick — run on every cookie-mode send — off a full
        # partition scan; the trailing ``id`` makes the ORDER BY index-only.
        Index(
            "ix_gate_cookies_tenant_gate_status_id",
            "tenant_id",
            "gate_id",
            "status",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    # The gate the client filed this cookie under. FK to ``gates`` but no
    # ``ondelete``/relationship: gates soft-delete (``deleted_at``), so a retired
    # gate's row persists and its cookies stay listable/deletable — the vault
    # outlives an active gate (same stance as the batch/session snapshots).
    gate_id: Mapped[int] = mapped_column(ForeignKey("gates.id"))
    # PLAINTEXT credential (CC precedent). NEVER echoed to a client and never
    # logged. The full value lives here (Text, unbounded); the hash, not this,
    # sits in the unique btree.
    value: Mapped[str] = mapped_column(Text)
    # sha256 hex digest (64 chars) of the canonical ``value.strip()`` — the
    # dedup key. Computed by the router (the repo stays dumb), so the same bytes
    # the validator saw key the index.
    value_hash: Mapped[str] = mapped_column(String(64))
    # Optional client-authored label shown next to the masked value.
    label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Reserved for Phase-2 rotation — ``'active'`` on every row, no reader yet.
    status: Mapped[str] = mapped_column(
        String(10), server_default=text("'active'"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SendTarget(Base):
    """A Telegram destination the shared account sends to (multi-target sending).

    GLOBAL config — intentionally NO tenant_id: the owner curates ONE shared
    list of chats (the checker bot directly + the CC groups where it lives), and
    the send worker round-robins across the ENABLED + currently-resolvable ones
    to spread per-chat load. ``chat_id`` is the marked peer id — account-global,
    equals ``event.chat_id`` so it doubles as the capture filter — and is
    BigInteger because supergroup/channel ids (``-100…``) overflow int4.

    Resolution state is NOT a column: it is transient (depends on the live
    Telethon session) and derived from the gateway at read time. Hard-deletable
    — nothing references a target historically, so retiring config leaves no
    orphan. (Attribution keys on ``send_log(chat_id, message_id)``, never on a
    ``send_targets`` row.)
    """

    __tablename__ = "send_targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    label: Mapped[str] = mapped_column(String(80))
    enabled: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Batch(Base):
    """One send batch (lote) for a tenant (Story 2.2).

    ``gate_value``/``gate_name`` are SNAPSHOTS copied verbatim from the catalog
    row at creation — denormalized on purpose (Story 2.1 design): retiring or
    renaming a gate never rewrites history. No FK to ``gates``. The category is
    deliberately NOT snapshotted (browsing aid, not send history).

    ``state`` is a plain String, NOT a DB enum: ``'sending' | 'completed'``
    (2.2) + ``'paused' | 'stopping' | 'stopped'`` (2.3) + ``'cancelled'``
    (2.5, plan expiry mid-batch — terminal, NOT live) + ``'waiting'`` (4.2,
    admission control: created over the cap, FIFO-queued until a slot frees)
    — no ALTER TYPE needed.
    """

    __tablename__ = "batches"
    __table_args__ = (
        # DB enforcement of "one live batch per tenant" (Story 2.3, absorbing
        # the 2.2 review finding). Predicate = LIVE_STATES in repos/batches.py;
        # widen BOTH together if a live state is ever added ('waiting' joined
        # in Story 4.2 — a queued-for-admission batch IS the tenant's one
        # live batch).
        Index(
            "uq_batches_one_live_per_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text(
                "state IN ('sending', 'paused', 'stopping', 'waiting')"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    gate_value: Mapped[str] = mapped_column(String(20))
    gate_name: Mapped[str] = mapped_column(String(80))
    # Snapshot of the gate's client-visible "Comando visible" at batch start —
    # same denormalize-on-purpose idiom as gate_value/gate_name. Clients render
    # THIS (never gate_value) in live + historical views, so an owner editing
    # display_value later never rewrites an old batch's label.
    gate_display_value: Mapped[str] = mapped_column(String(80))
    # Snapshot of the gate's ``credit_cost`` at batch start (credits feature) —
    # same denormalize idiom. The capture pipeline charges THIS per first-✅, so
    # an owner re-pricing the gate never re-prices an in-flight or historical
    # batch. 0 (default) ⇒ free batch (existing rows backfill to 0).
    gate_credit_cost: Mapped[int] = mapped_column(
        server_default=text("0"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(20))
    # Scheduler priority tier (Story 2.4, generalized): 0=client, 1=admin,
    # 2=owner — higher sends first. Derived from the creator's role at batch
    # creation; only written there, only read by core.scheduler.pick_next.
    priority: Mapped[int] = mapped_column(
        SmallInteger, server_default=text("0"), nullable=False
    )
    # Capture session bound at batch start (Story 3.1, AC 3): attribution
    # resolves reply → send_log → line → batch → THIS session, even after the
    # batch lands completed/stopped/cancelled (the 2.5 promise: "Story 3.1
    # attributes their replies even on a cancelled batch"). SET NULL — the
    # session is the real owner of the captures and outlives batch cleanup.
    # NULL on pre-3.1 batches; attribution backfills it lazily.
    capture_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("capture_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # --- Amazon cookie-mode serialize gate (Phase 2) ------------------------
    # All five are NULL on every non-cookie-mode batch (and on every existing
    # row — backfill is NULL, no behavior change). They are the durable,
    # authoritative serialize gate + attempt-fence for the cookie-mode send
    # flow: the worker sends the atomic ``.cookie``/``.amz`` pair then HOLDS the
    # tenant until the bot's verdict for that line arrives.
    #
    # ``awaiting_verdict_until`` is the serialize gate: while it is set and in
    # the future, ``repos.batches.active_senders`` excludes this tenant (the
    # skip is resolved in SQL against DB ``now()``, NOT the scheduler's
    # ``time.monotonic`` clock — mixing the two is meaningless and this also
    # makes the gate survive a restart for free). ``func.now() + 90s`` on send;
    # cleared on a matching verdict.
    awaiting_verdict_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The ``.amz`` ``(chat_id, message_id)`` the worker is currently awaiting —
    # the attempt-fence. A rotation/timeout resend is a NEW ``message_id`` for
    # the SAME line, so a verdict signal is accepted ONLY if it matches the
    # message_id stored here (verified in-txn under the batch FOR UPDATE); a
    # verdict for a superseded attempt or an already-cleared await is dropped.
    # BigInteger because channel/supergroup message-id sequences ride alongside
    # ``-100…`` chat ids (see SendLog).
    awaiting_message_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    awaiting_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Why a paused cookie-mode batch is paused: ``'cookies_exhausted'`` (no
    # active cookie left to rotate to) or ``'verdict_timeout'`` (the bot went
    # silent past the retry-once). Both are ordinary ``STATE_PAUSED`` (no new
    # state) discriminated by THIS reason — it rides the ``batch.state`` WS
    # frame so the cockpit can render the right prompt. NULL for a plain
    # client-initiated pause.
    pause_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # SNAPSHOT of the gate's catalog id at cookie-mode batch creation — keys the
    # active-cookie pick ``(tenant_id, gate_id)``. NO FK / no relationship, by
    # design: same denormalize-on-purpose stance as ``gate_value``/``gate_name``
    # (history survives a gate edit). Never re-resolve gate_id from
    # ``gate_value`` at send time — a retired+recreated value would mis-key
    # cookies across gate generations. NULL on non-cookie-mode + existing rows.
    gate_id: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class BatchLine(Base):
    """One line of a batch — the FULL message with the gate already applied.

    ``tenant_id`` is denormalized (the batch already carries it) for isolation
    queries and the ``send_log`` write-ahead rows (2.5). ``state``:
    ``'queued' | 'sending' | 'sent'`` (2.2) + ``'failed'`` (retry cap hit) and
    ``'cancelled'`` (plan expired mid-batch) since 2.5 — neither is "pending",
    so a batch with failed/cancelled lines still drains to completion.
    """

    __tablename__ = "batch_lines"
    __table_args__ = (
        # Ordering invariant: positions are unique within a batch.
        UniqueConstraint(
            "batch_id", "position", name="uq_batch_lines_batch_id_position"
        ),
        # The worker's hot query (next queued line per batch).
        Index("ix_batch_lines_batch_id_state", "batch_id", "state"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column()
    text: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20))
    # Machine-readable failure code (snake_case of the exception class name,
    # Story 2.5) — the frontend maps it to Spanish copy; NULL unless 'failed'.
    fail_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # The cookie that produced this line's last dead verdict (Phase 2 cookie
    # rotation) — diagnostic/audit only, FK to ``gate_cookies`` with NO
    # relationship. ``ondelete='SET NULL'`` is LOAD-BEARING: a cookie is HARD-
    # DELETED both on manual delete (the client removes it) and on a dead verdict
    # (the rotation purges it from the vault); without SET NULL the Postgres
    # default RESTRICT would raise ForeignKeyViolation on every such delete (the
    # 500 behind the "error inesperado"). NULL until a cookie-dead verdict stamps
    # the sent cookie; re-nulled when that cookie is deleted.
    failed_cookie_id: Mapped[int | None] = mapped_column(
        ForeignKey("gate_cookies.id", ondelete="SET NULL"), nullable=True
    )
    # Durable Phase-2 verdict-timeout retry budget. The cookie-mode timeout sweep
    # retries a silent ``.amz`` line ONCE, then pauses ``verdict_timeout``. This
    # counter (0 = fresh, >=1 = the one retry already burned) replaces the old
    # process-memory ``send_worker._timeout_retried`` set so the budget survives a
    # restart — a crash loop around the 90s timeout no longer grants a fresh retry
    # (and a fresh ``.cookie``+``.amz`` resend on the shared account) per restart.
    # Reset to 0 at every fresh ``.amz`` attempt (``requeue_line_with_intent_reset``
    # — rotation / resume / timeout-resend base); bumped in ``_resend_cookie_line``.
    # ``server_default="0"`` (string literal, not ``text("0")``) — inside this
    # class body the imported ``text`` is shadowed by the ``text`` column above.
    verdict_timeout_retries: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SendLog(Base):
    """Write-ahead record of one send attempt per line (Story 2.5).

    Written by the send worker (2.5), read by capture/attribution (3.1):
    ``(chat_id, reply_to_msg_id) → send_log → tenant/batch/line``. One row per
    LINE (``uq_send_log_line_id``) — retries of the same line REUSE the row;
    the intent is recorded in the SAME transaction as the 'sending' claim
    (BEFORE calling Telegram) and ``chat_id``/``message_id`` are filled in after
    delivery, so a crash between send and record cannot create orphan replies.
    A row with ``message_id`` NULL means "attempted, delivery unconfirmed" —
    boot reconciliation resolves it.

    🔒 ``message_id`` is NOT account-global: supergroups/channels each carry
    their OWN per-chat message-id sequence (starting at 1), so the SAME id is
    reused across the multi-destination send targets. ``chat_id`` (the marked
    peer id of the destination the line was sent to) namespaces it — attribution
    matches on the (chat_id, message_id) PAIR, never message_id alone, or a
    bot reply mis-attributes across chats/tenants. ``chat_id`` is NULL on rows
    written before this fix (unrecoverable — the destination wasn't recorded).
    Both are BigInteger: supergroup ids (``-100…``) and Telegram message ids
    outgrow int4.
    """

    __tablename__ = "send_log"
    __table_args__ = (
        UniqueConstraint("line_id", name="uq_send_log_line_id"),
        # Legacy single-column index (kept; still serves message_id scans).
        Index("ix_send_log_message_id", "message_id"),
        # The hot attribution lookup: (chat_id, reply_to_msg_id) → row.
        Index("ix_send_log_chat_message", "chat_id", "message_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"), index=True
    )
    line_id: Mapped[int] = mapped_column(
        ForeignKey("batch_lines.id", ondelete="CASCADE")
    )
    # Marked peer id of the destination this line was sent to — namespaces the
    # per-chat message_id (see the class docstring). NULL = pre-fix row.
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Set by a Historial delete (api/history) to STOP the reply reconciler from
    # resurrecting a user-purged message. The reconciler treats a line with no
    # 'full' response row as "awaiting a reply" and re-fetches it from Telegram,
    # so a delete that (by invariant) removes only `responses` rows and leaves
    # send_log intact would be undone within one ~45s pass. The tombstone is
    # per-line and terminal (line ids are never reused); NULL = not purged.
    # Mirrors the `responses.hidden_at` soft-state idiom.
    reply_purged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class CaptureSession(Base):
    """One save session per tenant+gate (Story 3.1) — the legacy active
    ``Sesion`` generalized per tenant; activation deactivates the previous one
    (legacy: sessions are replaced by reassignment, never closed).

    ``gate_value``/``gate_name`` are SNAPSHOTS copied verbatim from the catalog
    — same idiom and justification as ``Batch``: retiring or renaming a gate
    never rewrites history; no FK to ``gates``. ``name`` is the friendly label
    (Story 3.3 rename, 200-char cap mirroring legacy ``escribir_nombre``);
    NULL ⇒ the UI falls back to a ``created_at`` format (``nombre_bonito``).
    """

    __tablename__ = "capture_sessions"
    __table_args__ = (
        # DB enforcement of "one ACTIVE capture session per tenant" — same
        # idiom as uq_batches_one_live_per_tenant.
        Index(
            "uq_capture_sessions_one_active_per_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    gate_value: Mapped[str] = mapped_column(String(20))
    gate_name: Mapped[str] = mapped_column(String(80))
    # Snapshot of the gate's client-visible "Comando visible" (same idiom as
    # Batch.gate_display_value): the Historial / support views render THIS, not
    # the real gate_value.
    gate_display_value: Mapped[str] = mapped_column(String(80))
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Snapshot of the gate category's ``special_mode`` (same denormalize idiom
    # as ``gate_value``): the capture pipeline reads THIS, never the live
    # category. Set at session creation and refreshed to the gate's current
    # value when a NEW batch reuses the active session (``resolve_for_batch``),
    # so an owner's toggle takes effect on the tenant's next batch. CLOSED /
    # historical sessions are never rewritten.
    special_mode: Mapped[bool] = mapped_column(
        Boolean, server_default=false(), nullable=False
    )
    # Snapshot of the gate category's ``cookie_mode`` (same denormalize idiom as
    # ``special_mode``): set at session creation and refreshed to the gate's
    # current value when a NEW batch reuses the active session
    # (``resolve_for_batch``), so an owner's toggle takes effect on the tenant's
    # next batch. The WRITE path ships in Phase 1; the READER (cookie rotation in
    # the send/capture pipeline) is Phase 2 — nothing reads this column yet.
    cookie_mode: Mapped[bool] = mapped_column(
        Boolean, server_default=false(), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default=false(), nullable=False
    )
    # Cockpit "Limpiar" view-cutoff (sessionless cockpit, PR-1). An ``id``
    # HIGH-WATER-MARK, not a timestamp: when the client clears the live panels,
    # this is stamped to ``MAX(responses.id)`` and the DISPLAY reads (and ONLY
    # the display reads) AND ``Response.id > cleared_response_id`` so every row
    # captured at or before the clear is hidden from the cockpit/snapshot/export.
    # It follows the ``hidden_at`` discipline exactly — every integrity /
    # attribution / reconciler / dedup (``add_new_cc``) / credit /
    # ``awaiting_reply`` query IGNORES it, so Limpiar deletes ZERO rows and the
    # approved-✅ history survives for the deferred PR-2. ``id`` (monotonic, the
    # ``_list_last`` sort key) — NOT ``created_at`` (txn-start ``now()``, so
    # rows of one capture transaction share a timestamp) — makes the cutoff
    # tie-immune. NULL = nothing cleared (every pre-existing row).
    cleared_response_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Response(Base):
    """One captured row (Story 3.1): full revisions AND filtered CC data.

    A single table holds BOTH row types (architecture: "responses (full +
    filtered/deduped rows)"), discriminated by ``kind``:
    - ``'full'``: one revision of a bot reply — ``text`` is the whole reply,
      ``status`` is ``'ok'`` (✅) or ``'rejected'`` (❌). The LATEST 'full' row
      per ``(chat_id, message_id)`` (via ``ix_responses_chat_message``) IS the
      durable per-message state of AC 5 — it replaces the legacy in-memory
      dict, so edit dedup survives restarts and ``catch_up`` replays. ``chat_id``
      is part of the key because message ids are per-chat, not account-global
      (see ``SendLog``): keying on message_id alone would collapse two distinct
      replies that share an id across two supergroups.
    - ``'cc'``: one message-new extracted CC value — ``text`` is the VALUE,
      ``status`` is NULL. Per-MESSAGE dedup is DB-enforced by the partial
      unique index ``uq_responses_session_msg_cc`` (keyed on session + chat +
      message + text): each approved card contributes its CC, so the same value
      seen on two messages lands twice (Datos CC mirrors Aprobadas), while a
      capture retry / reconciler edit-replay of ONE message stays idempotent.

    ``batch_id``/``line_id`` are SET NULL on purpose: the capture survives
    batch cleanup — the session is the real owner.
    """

    __tablename__ = "responses"
    __table_args__ = (
        # Legacy single-column index (kept).
        Index("ix_responses_message_id", "message_id"),
        # The per-message state lookup of AC 5, namespaced per chat (message
        # ids are per-chat, not account-global — see SendLog).
        Index("ix_responses_chat_message", "chat_id", "message_id"),
        # Per-MESSAGE CC dedup, guaranteed by Postgres — not just by code. Keyed
        # on (session, chat, message, text) so the same CC value on two distinct
        # approved messages lands twice (Datos CC mirrors Aprobadas), while a
        # retry/edit-replay of ONE message stays idempotent. The net, not the
        # mechanism (the single capture consumer is).
        Index(
            "uq_responses_session_msg_cc",
            "capture_session_id",
            "chat_id",
            "message_id",
            "text",
            unique=True,
            postgresql_where=text("kind = 'cc'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    capture_session_id: Mapped[int] = mapped_column(
        ForeignKey("capture_sessions.id", ondelete="CASCADE"), index=True
    )
    batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("batches.id", ondelete="SET NULL"), nullable=True
    )
    line_id: Mapped[int | None] = mapped_column(
        ForeignKey("batch_lines.id", ondelete="SET NULL"), nullable=True
    )
    # Marked peer id of the chat the reply arrived in — namespaces message_id
    # (per-chat, not account-global). NULL on rows written before this fix.
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_id: Mapped[int] = mapped_column(BigInteger)
    # 'full' (complete revision) | 'cc' (filtered datum) — plain String, no
    # DB enum (2.2 decision).
    kind: Mapped[str] = mapped_column(String(10))
    # 'ok' (✅) | 'rejected' (❌); NULL on 'cc' rows.
    status: Mapped[str | None] = mapped_column(String(10), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Soft-hide marker (clear-declined). When set, this 'full' row is dropped
    # from the Completa display/export reads (``list_full``/``full_count`` with
    # the default ``include_hidden=False``) but STILL seen by every integrity
    # query — ``responded_line_count``, the reconciler's ``_answered_full_exists``
    # (``send_log``), ``last_full_revision`` and ``has_ok_revision`` never filter
    # ``hidden_at``. The row is retained so a hidden ❌ can't be resurrected by the
    # reply reconciler (45s/72h) nor spike the "esperando respuesta" counter.
    # NULL ⇒ visible (every pre-existing row and every 'cc' row).
    hidden_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Credential(Base):
    """A stored email+password entry (personal credential vault).

    GLOBAL — one flat table, NO tenant scoping (owner decision 2026-06-23): a
    single operator owns the whole vault behind the shared ``X-Api-Key``. It was
    tenant-scoped at first; ``tenant_id`` was dropped in migration
    ``b3f1a7c9d2e4``, which also re-exposed the pre-refactor rows once stranded
    under the caller's real tenant.

    🔒 ``password`` holds the credential PLAINTEXT in Postgres — the deliberate
    CC / ``gate_cookies`` precedent (access-control + TLS at rest). It is echoed
    back ONLY to the key holder and never logged. No uniqueness on ``email``: the
    same email may be stored more than once on purpose (re-saves / different
    passwords).
    """

    __tablename__ = "credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320))
    # ponytail: plaintext vault (gate_cookies precedent). Hash only if these ever
    # become auth credentials rather than stored secrets to read back.
    password: Mapped[str] = mapped_column(Text)
    used: Mapped[bool] = mapped_column(
        Boolean, server_default=false(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AuditLog(Base):
    """One audited cross-tenant support read (Story 3.6, AC 2).

    Written ONLY by the support view in ``api/admin.py`` — the single place
    tenant isolation is intentionally crossed (architecture: "Owner/admin
    cross-tenant access goes through explicit ``for_tenant(id)`` support
    paths, audit-logged"). Three recorded FK decisions:

    - ``actor_user_id`` is ``SET NULL``: the trail survives the removal of
      the admin who looked (the record outlives the actor).
    - ``tenant_id`` (the TARGET tenant of the cross) is ``CASCADE``: the
      trail is a support trail — it dies with the tenant. This also keeps the
      tests' ``cleanup_users`` teardown untouched.
    - ``capture_session_id`` carries NO FK on purpose: it is a historical
      reference — the audit record must neither die nor null out when the
      client hard-deletes their session (3.3).
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    # snake_case action name, e.g. 'support_sessions_list'.
    action: Mapped[str] = mapped_column(String(40))
    capture_session_id: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class WatchdogState(Base):
    """Durable latch of the watchdog's GLOBAL send pause (Story 4.1).

    ONE row (id=1, app-enforced — ``repos/watchdog.save_state`` is get-or-
    create on that id). GLOBAL system state, deliberately NO ``tenant_id``
    (same documented exception class as ``gates``): the watchdog pauses the
    whole shared account, never one tenant.

    The in-process singleton (``core/watchdog.py``) is the operating
    authority (the worker gates on memory, zero queries per step); this row
    is what survives a restart — CI deploys on every push to main, and a
    watchdog pause that evaporates on deploy would be exactly the automatic
    resume AC 3 forbids. ``reason`` is a plain String, no DB enum (2.2
    decision): ``'reply_rate_collapse' | 'session_lost'``.
    """

    __tablename__ = "watchdog_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    paused: Mapped[bool] = mapped_column(
        Boolean, server_default=false(), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SystemSetting(Base):
    """Owner-tunable runtime configuration as a key/value row (Story 4.2).

    GLOBAL — no tenant scoping: these are knobs of the shared system, curated
    by the owner alone. Deliberately NOT in ``app.config.Settings`` (env):
    "owner-configurable" means hot, from the UI, surviving restarts and
    needing no redeploy. First key: ``max_active_senders`` (the admission-
    control cap; ``"0"``/missing row = disabled). Values are short strings
    parsed defensively by the owning service.
    """

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Plan(Base):
    """An owner-managed pricing plan (the plan-catalog feature).

    GLOBAL catalog — intentionally NO tenant_id: the owner curates one shared
    list of tiers for all clients (same deliberate exception as ``gates`` and
    ``system_settings``). A client links to one plan via ``User.plan_id``;
    assigning/renewing derives ``expires_at`` from ``duration_days``.

    The catalog ships EMPTY — the owner creates plans from ``/admin/plans``;
    nothing is seeded. Retirement is a soft state (``is_active=false``), never
    a delete while referenced: the ``User.plan_id`` FK is ``RESTRICT`` and the
    service raises ``plan_in_use``, so historical assignments never dangle.

    Money/seconds use ``Numeric`` (exact, no float drift): ``price_usd`` for
    display/billing, ``antispam_seconds`` as the per-tenant scheduler cooldown
    (never below the global floor — the account-wide ban protector).
    """

    __tablename__ = "plans"
    __table_args__ = (
        UniqueConstraint("name", name="uq_plans_name"),
        # At most ONE plan is the gift-key default ("basic" tier), DB-enforced
        # (gift-keys feature). Flagging a new default clears the prior one FIRST
        # (services/plans.set_default_plan) to dodge this index — the documented
        # "flip carefully to dodge the partial index" pattern.
        Index(
            "uq_plans_one_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    price_usd: Mapped[float] = mapped_column(Numeric(10, 2))
    duration_days: Mapped[int] = mapped_column()
    antispam_seconds: Mapped[float] = mapped_column(Numeric(6, 2))
    max_lines_per_batch: Mapped[int] = mapped_column()
    # Credits granted to a tenant when this plan is assigned/renewed (credits
    # feature). ADDED to the tenant's ``credit_balance`` each time (a renewal
    # tops up); the owner can also recharge independently. 0 (default) ⇒ a
    # time-only plan that grants no credits.
    credits: Mapped[int] = mapped_column(
        server_default=text("0"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true"), nullable=False
    )
    # The owner-designated DEFAULT ("basic") plan that gift keys grant to a
    # plan-less claimer (gift-keys feature). At most one plan is the default
    # (partial unique index above). false on every plan until the owner flags
    # one from /admin/plans — admins NEVER choose a key's tier (anti-abuse).
    is_default: Mapped[bool] = mapped_column(
        Boolean, server_default=false(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GiftKey(Base):
    """A single-use redeemable gift key (gift-keys feature).

    An admin/owner MINTS a key carrying only ``days`` + a SNAPSHOT of the
    owner-designated default ("basic") plan (``plan_id``); a client CLAIMS it to
    add days — never credits. The table IS the audit log: ``created_by_user_id``
    / ``claimed_by_user_id`` + the timestamps record who minted and who claimed,
    so the owner can spot admin abuse. GLOBAL, no tenant scoping (the gate/plan-
    catalog convention) — identity comes from the session at the route.

    ``status`` is a plain String ('active' | 'claimed' | 'revoked'), no DB enum
    (2.2 decision). Single-use is enforced at claim under ``SELECT … FOR UPDATE``
    on the row: only an 'active' key transitions to 'claimed'.

    ``plan_id`` is RESTRICT (like ``users.plan_id``): a referenced plan can't be
    deleted, and the snapshot means a later default change never re-prices an
    outstanding key. The user FKs are SET NULL (mirror ``audit_log.actor``): the
    trail survives the removal of the minting/claiming user.
    """

    __tablename__ = "gift_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    days: Mapped[int] = mapped_column()
    # Credits the claimer's tenant gains on claim (gift-key-credits feature).
    # Admin-chosen at mint (the deliberate relaxation of "admin never picks a
    # value" — credits only). 0 ⇒ a days-only key. ``days==0 && credits>0`` is a
    # credits-only key; a key must grant at least one of the two (validated at
    # the route, not the DB). ADDED to ``tenants.credit_balance`` at claim.
    credits: Mapped[int] = mapped_column(
        server_default=text("0"), nullable=False
    )
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("plans.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(10), server_default=text("'active'"), nullable=False
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    claimed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    revoked_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
