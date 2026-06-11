"""Authentication service: password hashing, sessions, login throttle.

Pure-ish orchestration over ``app.db.repos.users``. No FastAPI / HTTP here —
the router (``app.api.auth``) maps these into responses and the error contract.
"""

import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import AuthSession, User
from app.db.repos import users as users_repo

# A single module-level hasher — library defaults are the recommended argon2id
# parameters (meets NFR5).
_ph = PasswordHasher()

# Precomputed hash used to equalize timing on the unknown-email path so the
# "no such user" branch costs the same as a real "wrong password" verify (no
# user-enumeration timing oracle). The plaintext is irrelevant — it is never
# matched against anything.
DUMMY_HASH = _ph.hash("cc-dummy-password-for-timing-equalization")


def hash_password(raw: str) -> str:
    """Return an argon2id hash for ``raw``."""
    return _ph.hash(raw)


def generate_temp_password() -> str:
    """One-time temp password for the admin reset action (Story 1.6).

    16 url-safe chars (~96 bits) — paste-safe for out-of-band delivery. The
    plaintext must exist ONLY in the reset response: never log or persist it.
    """
    return secrets.token_urlsafe(12)


def verify_password(stored_hash: str, raw: str) -> bool:
    """Return ``True`` iff ``raw`` matches ``stored_hash``.

    Any verification failure (mismatch, malformed hash) is treated as ``False``
    — never raised — so callers branch on a plain boolean.
    """
    try:
        return _ph.verify(stored_hash, raw)
    except (VerifyMismatchError, InvalidHashError):
        return False
    except Exception:
        # Defensive: any unexpected argon2 error counts as a failed verify.
        return False


# --- Session helpers (over the users repo) -------------------------------


async def create_session(session: AsyncSession, user: User) -> AuthSession:
    """Create and persist a fresh session row for ``user``; returns the row.

    The opaque ``token`` (``secrets.token_urlsafe(32)``) is the only value the
    cookie carries.
    """
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(days=settings.session_ttl_days)
    return await users_repo.add_session(
        session, user_id=user.id, token=token, expires_at=expires_at
    )


async def get_valid_session(
    session: AsyncSession, token: str
) -> AuthSession | None:
    """Return the live session (user eagerly loaded) for ``token`` or ``None``."""
    return await users_repo.get_active_session_with_user(session, token)


async def revoke_session(session: AsyncSession, token: str) -> None:
    """Revoke the session carrying ``token`` (idempotent)."""
    await users_repo.mark_session_revoked(session, token)


# --- Login throttle ------------------------------------------------------
#
# In-process counter keyed by (email_lowercased, client_ip). Per-process and
# resets on restart — acceptable at single-process MVP scale (one cc-core).


@dataclass
class _Bucket:
    count: int = 0
    window_start: float = 0.0


@dataclass
class LoginThrottle:
    """Sliding fixed-window failure counter per (email, ip)."""

    max_attempts: int
    window_seconds: int
    _buckets: dict[tuple[str, str], _Bucket] = field(default_factory=dict)

    def _key(self, email: str, ip: str) -> tuple[str, str]:
        return (email.lower(), ip)

    def _prune(self, now: float) -> None:
        """Drop buckets whose window has elapsed so the dict stays bounded.

        Without this, every distinct (email, ip) that ever failed would leak a
        bucket forever (a slow memory DoS under credential stuffing).
        """
        expired = [
            key
            for key, bucket in self._buckets.items()
            if now - bucket.window_start >= self.window_seconds
        ]
        for key in expired:
            del self._buckets[key]

    def is_blocked(self, email: str, ip: str, *, now: float | None = None) -> bool:
        """Return ``True`` if this (email, ip) is currently throttled."""
        now = time.monotonic() if now is None else now
        bucket = self._buckets.get(self._key(email, ip))
        if bucket is None:
            return False
        if now - bucket.window_start >= self.window_seconds:
            return False  # window elapsed → no longer blocked
        return bucket.count >= self.max_attempts

    def register_failure(
        self, email: str, ip: str, *, now: float | None = None
    ) -> None:
        """Record a failed attempt, opening a new window when the old one lapsed."""
        now = time.monotonic() if now is None else now
        self._prune(now)
        key = self._key(email, ip)
        bucket = self._buckets.get(key)
        if bucket is None or now - bucket.window_start >= self.window_seconds:
            self._buckets[key] = _Bucket(count=1, window_start=now)
        else:
            bucket.count += 1

    def reset(self, email: str, ip: str) -> None:
        """Clear the counter (called on successful login)."""
        self._buckets.pop(self._key(email, ip), None)


login_throttle = LoginThrottle(
    max_attempts=settings.throttle_max_attempts,
    window_seconds=settings.throttle_window_seconds,
)
