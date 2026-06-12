"""ORM models.

Tables arrive story by story via Alembic migrations (no tables ahead of need):
tenants/users/auth_sessions (1.2+), gates (2.1), gate_categories + batches +
batch_lines (2.2).
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
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


class Batch(Base):
    """One send batch (lote) for a tenant (Story 2.2).

    ``gate_value``/``gate_name`` are SNAPSHOTS copied verbatim from the catalog
    row at creation — denormalized on purpose (Story 2.1 design): retiring or
    renaming a gate never rewrites history. No FK to ``gates``. The category is
    deliberately NOT snapshotted (browsing aid, not send history).

    ``state`` is a plain String, NOT a DB enum: ``'sending' | 'completed'``
    (2.2) + ``'paused' | 'stopping' | 'stopped'`` (2.3); 2.5 adds
    ``cancelled`` — no ALTER TYPE needed later.
    """

    __tablename__ = "batches"
    __table_args__ = (
        # DB enforcement of "one live batch per tenant" (Story 2.3, absorbing
        # the 2.2 review finding). Predicate = LIVE_STATES in repos/batches.py;
        # widen BOTH together if a live state is ever added.
        Index(
            "uq_batches_one_live_per_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text("state IN ('sending', 'paused', 'stopping')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    gate_value: Mapped[str] = mapped_column(String(20))
    gate_name: Mapped[str] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(20))
    # Set when the creator's role == owner; CONSUMED by Story 2.4's scheduler
    # (owner priority) — only written here.
    is_owner_priority: Mapped[bool] = mapped_column(
        Boolean, server_default=false(), nullable=False
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
    queries and Story 2.5's send_log. ``state``: ``'queued' | 'sending' |
    'sent'`` in this story (2.5 adds ``failed``/``cancelled``).
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
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
