"""User-management service (Story 1.3).

Orchestrates account creation: duplicate-email check, tenant-per-user, role +
plan-expiry derivation, password hashing. Multi-step, so it lives here — routers
never drive the ORM directly (architecture Structure Patterns).

User management is GLOBAL/cross-tenant by design (an admin manages all clients);
the authorization boundary is the route's ``require_role`` dependency, enforced
in ``app.api.admin`` — not a tenant filter here.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.db.repos import users as users_repo
from app.errors import email_taken
from app.services.auth import hash_password


async def create_account(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    role: str,
    plan_days: int | None,
) -> User:
    """Create a user (and its own tenant); returns the unflushed-then-flushed row.

    - Lowercases the email (canonical storage) and rejects duplicates with
      ``email_taken`` (case-insensitive, via the repo).
    - Creates a fresh tenant named after the email (one tenant per user).
    - Sets ``expires_at = now(UTC) + plan_days`` ONLY for ``role == "client"``;
      owner/admin rows carry no plan (``None``).

    The caller commits. Authorization (who may create which role, plan_days
    validation) is enforced in the router BEFORE calling this.
    """
    email = email.lower()
    if await users_repo.get_by_email(session, email) is not None:
        raise email_taken()

    tenant = await users_repo.create_tenant(session, name=email)

    expires_at: datetime | None = None
    if role == "client":
        # plan_days is validated as a positive int by the router for clients.
        assert plan_days is not None  # noqa: S101 — router guarantees this
        expires_at = datetime.now(UTC) + timedelta(days=plan_days)

    try:
        return await users_repo.create_user(
            session,
            tenant_id=tenant.id,
            email=email,
            password_hash=hash_password(password),
            role=role,
            expires_at=expires_at,
        )
    except IntegrityError as exc:
        # The pre-check above is racy: a concurrent insert of the same email
        # only trips the DB unique constraint at flush. Map that to the same
        # email_taken contract instead of a 500. (get_session rolls the
        # transaction back when this AppError propagates.)
        raise email_taken() from exc
