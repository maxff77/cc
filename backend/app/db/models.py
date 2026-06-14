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
    value: Mapped[str] = mapped_column(String(20))
    # Friendly label shown to clients. Required; not unique — two gates may
    # share a name, ``value`` is the identity. (Story 2.2 owner decision:
    # clients see name + category + value.)
    name: Mapped[str] = mapped_column(String(80))
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
    — nothing references a target historically (``send_log`` has no chat_id; the
    ``message_id`` alone attributes replies), so retiring config leaves no orphan.
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
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SendLog(Base):
    """Write-ahead record of one send attempt per line (Story 2.5).

    Written by the send worker (2.5), read by capture/attribution (3.1):
    ``reply_to_msg_id → send_log → tenant/batch/line``. One row per LINE
    (``uq_send_log_line_id``) — retries of the same line REUSE the row; the
    intent is recorded in the SAME transaction as the 'sending' claim (BEFORE
    calling Telegram) and ``message_id`` is filled in after delivery, so a
    crash between send and record cannot create orphan replies. A row with
    ``message_id`` NULL means "attempted, delivery unconfirmed" — boot
    reconciliation resolves it. ``message_id`` is BigInteger: Telegram ids may
    outgrow int4 over time.
    """

    __tablename__ = "send_log"
    __table_args__ = (
        UniqueConstraint("line_id", name="uq_send_log_line_id"),
        # The hot attribution lookup of Story 3.1 (reply_to_msg_id → row).
        Index("ix_send_log_message_id", "message_id"),
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
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
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
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default=false(), nullable=False
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
      per ``message_id`` (via ``ix_responses_message_id``) IS the durable
      per-message state of AC 5 — it replaces the legacy in-memory dict, so
      edit dedup survives restarts and ``catch_up`` replays.
    - ``'cc'``: one session-new extracted CC value — ``text`` is the VALUE,
      ``status`` is NULL. Per-session dedup is DB-enforced by the partial
      unique index ``uq_responses_session_cc`` (FR17: Story 3.4's "continuar"
      reactivates the session and this dedup-from-existing-rows IS the
      "dedup set preserved" — no preloading code, the rows are the set).

    ``batch_id``/``line_id`` are SET NULL on purpose: the capture survives
    batch cleanup — the session is the real owner.
    """

    __tablename__ = "responses"
    __table_args__ = (
        # The per-message_id state lookup of AC 5 (same width as
        # send_log.message_id).
        Index("ix_responses_message_id", "message_id"),
        # Session-scoped CC dedup, guaranteed by Postgres — not just by code.
        Index(
            "uq_responses_session_cc",
            "capture_session_id",
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
