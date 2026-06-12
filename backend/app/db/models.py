"""ORM models for migration #1.

ONLY tenants, users and auth_sessions — later stories add their own tables
via new Alembic migrations (no tables ahead of need).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, false, func, text
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
    # Friendly label shown to clients (the value/prefix stays internal). Required;
    # not unique — two gates may share a name, ``value`` is the identity.
    name: Mapped[str] = mapped_column(String(80))
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
