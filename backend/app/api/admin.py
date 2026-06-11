"""Admin router: user management (Story 1.3).

`/api/admin/users` — list/create/delete clients (and, for the owner, admins).

Authorization is enforced SERVER-SIDE here (the security boundary — the UI only
mirrors it). The actor's role/identity comes ONLY from ``require_role`` /
``get_current_user`` (the session), never from the request body. These queries
are GLOBAL/cross-tenant by design (an admin manages all clients) — see the
``db.repos.users`` module note.
"""

import re
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.db.base import get_session
from app.db.models import User
from app.db.repos import users as users_repo
from app.errors import forbidden, invalid_plan_days, user_not_found
from app.services import users as users_service

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Role gates as module-level singletons (so the factory call isn't performed in
# argument defaults — ruff B008).
require_admin_or_owner = require_role("admin", "owner")
require_owner = require_role("owner")


# --- Schemas (snake_case, pydantic v2) -----------------------------------

# Pragmatic email shape check (no email-validator dependency). The boundary
# rejects empty/garbage emails before they reach the DB; login looks up
# case-insensitively so we canonicalise to lowercase here too.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PASSWORD_MIN = 8
# Upper bound on plan length; guards datetime/timedelta overflow on a fat-finger
# value. ~100 years is far beyond any real plan. Lower bound stays in the route
# (so a missing/<=0 value surfaces the invalid_plan_days code, not a 422).
PLAN_DAYS_MAX = 36500


class CreateUserRequest(BaseModel):
    email: str
    password: str
    role: str = "client"
    plan_days: int | None = None

    @field_validator("email")
    @classmethod
    def _canonical_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("email inválido")
        return v

    @field_validator("password")
    @classmethod
    def _password_length(cls, v: str) -> str:
        if len(v) < _PASSWORD_MIN:
            raise ValueError("contraseña demasiado corta")
        return v


class UserOut(BaseModel):
    id: int
    email: str
    role: str
    tenant_id: int
    expires_at: datetime | None
    is_blocked: bool


class UserListResponse(BaseModel):
    items: list[UserOut]


def _to_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        role=user.role,
        tenant_id=user.tenant_id,
        expires_at=user.expires_at,
        is_blocked=user.is_blocked,
    )


@router.get("/users", response_model=UserListResponse)
async def list_users(
    actor: User = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_session),
) -> UserListResponse:
    """List manageable users.

    admin → clients only; owner → clients + admins (never other owners).
    """
    roles = ["client", "admin"] if actor.role == "owner" else ["client"]
    users = await users_repo.list_by_roles(session, roles)
    return UserListResponse(items=[_to_out(u) for u in users])


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(
    body: CreateUserRequest,
    actor: User = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    """Create a client (admin/owner) or an admin (owner only).

    Authorization matrix (server-enforced):
    - role must be 'client' or 'admin' (else forbidden).
    - creating 'admin' is owner-only (admin caller → forbidden).
    - 'client' requires a positive plan_days (else invalid_plan_days);
      'admin' ignores plan_days.
    """
    if body.role not in ("client", "admin"):
        raise forbidden()

    if body.role == "admin" and actor.role != "owner":
        raise forbidden()

    plan_days = body.plan_days
    if body.role == "client":
        if plan_days is None or plan_days <= 0 or plan_days > PLAN_DAYS_MAX:
            raise invalid_plan_days()
    else:
        plan_days = None  # admins carry no plan

    user = await users_service.create_account(
        session,
        email=body.email,
        password=body.password,
        role=body.role,
        plan_days=plan_days,
    )
    await session.commit()
    return _to_out(user)


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: int,
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Remove an admin (owner-only, AC6).

    1.3 only removes admins — a non-admin target is forbidden (client
    removal/block is Story 1.5; the owner can never delete an owner). The user's
    now-empty tenant may be left orphaned (acceptable at MVP).
    """
    target = await users_repo.get_user_by_id(session, user_id)
    if target is None:
        raise user_not_found()
    if target.role != "admin":
        raise forbidden()
    await users_repo.delete_user(session, target)
    await session.commit()
