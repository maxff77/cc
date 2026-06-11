"""Plan service: plan-expiry predicate (Story 1.4).

Pure domain logic over a ``User`` row — no DB, no FastAPI — same purity rule as
``services/auth``'s password helpers. The router/dependency layer maps the
result into the ``plan_expired`` error contract and the session invalidation.

Story 1.5 extends THIS file with renew/extend and block/unblock; this story
adds only the read-side expiry check.
"""

from datetime import UTC, datetime

from app.db.models import User


def is_plan_expired(user: User) -> bool:
    """Return ``True`` iff ``user`` is a client whose plan has lapsed.

    Predicate: ``role == "client" AND expires_at IS NOT NULL AND expires_at <=
    now(UTC)``. The boundary is ``<=`` (expired exactly at the instant of
    expiry).

    owner/admin rows carry no plan (``expires_at IS NULL``) and are never
    expired. A client with ``expires_at = None`` is treated as NOT expired —
    defensive only: ``create_account`` always sets an expiry for clients, so
    this branch should not occur in practice.

    ``expires_at`` is timezone-aware (timestamptz), so it is compared against
    ``datetime.now(UTC)``; stripping tzinfo would raise ``TypeError``.
    """
    if user.role != "client":
        return False
    if user.expires_at is None:
        return False
    return user.expires_at <= datetime.now(UTC)
