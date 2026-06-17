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
from app.db.repos import plans as plans_repo
from app.db.repos import users as users_repo
from app.errors import email_taken, invalid_plan
from app.services.auth import hash_password


async def create_account(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    role: str,
    plan_days: int | None,
    plan_id: int | None = None,
    contact: str | None = None,
) -> User:
    """Create a user (and its own tenant); returns the unflushed-then-flushed row.

    - Lowercases the email (canonical storage) and rejects duplicates with
      ``email_taken`` (case-insensitive, via the repo).
    - Creates a fresh tenant named after the email (one tenant per user).
    - Plan expiry, for ``role == "client"`` only (owner/admin carry no plan):
      - ``plan_id`` given → validates the plan EXISTS and is ACTIVE (else
        ``invalid_plan``), links ``user.plan_id`` and sets
        ``expires_at = now(UTC) + plan.duration_days``. This is the
        plan-catalog path and takes precedence over ``plan_days``.
      - else legacy path → ``expires_at = now(UTC) + plan_days`` (no plan link).

    The caller commits. Authorization (who may create which role, plan_days
    validation) is enforced in the router BEFORE calling this.
    """
    email = email.lower()
    if await users_repo.get_by_email(session, email) is not None:
        raise email_taken()

    tenant = await users_repo.create_tenant(session, name=email)

    expires_at: datetime | None = None
    resolved_plan_id: int | None = None
    if role == "client":
        if plan_id is not None:
            # Plan-catalog path: the plan must exist AND be active. A retired
            # or unknown plan is rejected — clients are only sold live tiers.
            plan = await plans_repo.get_by_id(session, plan_id)
            if plan is None or not plan.is_active:
                raise invalid_plan()
            resolved_plan_id = plan.id
            expires_at = datetime.now(UTC) + timedelta(days=plan.duration_days)
            # Credit grant (credits feature): a fresh tenant starts at 0, so
            # assigning a plan grants its ``credits`` package. Set on the freshly
            # created tenant; the caller's commit persists it in the same
            # transaction. Legacy plan_days path grants none.
            tenant.credit_balance = plan.credits
        else:
            # Legacy path: plan_days is validated as a positive int by the
            # router for clients.
            assert plan_days is not None  # noqa: S101 — router guarantees this
            expires_at = datetime.now(UTC) + timedelta(days=plan_days)

    try:
        user = await users_repo.create_user(
            session,
            tenant_id=tenant.id,
            email=email,
            password_hash=hash_password(password),
            role=role,
            expires_at=expires_at,
            contact=contact,
        )
    except IntegrityError as exc:
        # The pre-check above is racy: a concurrent insert of the same email
        # only trips the DB unique constraint at flush. Map that to the same
        # email_taken contract instead of a 500. (get_session rolls the
        # transaction back when this AppError propagates.) Scoped to the user
        # INSERT alone: the plan-FK link below has its own except so a
        # plan-related violation never mis-reports as a duplicate email.
        raise email_taken() from exc

    if resolved_plan_id is not None:
        # repos.users.create_user has no plan_id kwarg (that repo is owned
        # elsewhere); set the link on the flushed row and re-flush so the FK
        # lands in the SAME transaction the caller commits. A plan deleted
        # between validation and here (narrow race; the FK is RESTRICT) trips
        # the FK at flush — surface invalid_plan, not email_taken.
        try:
            user.plan_id = resolved_plan_id
            await session.flush()
        except IntegrityError as exc:
            raise invalid_plan() from exc
    return user


async def set_contact(
    session: AsyncSession, target: User, contact: str | None
) -> User:
    """Set (or clear) a user's Telegram contact handle; flush, caller commits.

    ``contact`` must already be normalized/validated by the router (canonical
    handle without ``@``, or ``None`` to clear).
    """
    target.contact = contact
    await session.flush()
    return target
