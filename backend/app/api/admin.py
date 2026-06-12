"""Admin router: user management (Story 1.3).

`/api/admin/users` — list/create/delete clients (and, for the owner, admins).

Authorization is enforced SERVER-SIDE here (the security boundary — the UI only
mirrors it). The actor's role/identity comes ONLY from ``require_role`` /
``get_current_user`` (the session), never from the request body. These queries
are GLOBAL/cross-tenant by design (an admin manages all clients) — see the
``db.repos.users`` module note.
"""

import re
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import AwareDatetime, BaseModel, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.db.base import get_session
from app.db.models import Gate, GateCategory, User
from app.db.repos import gate_categories as gate_categories_repo
from app.db.repos import gates as gates_repo
from app.db.repos import users as users_repo
from app.errors import (
    category_exists,
    category_in_use,
    category_not_found,
    forbidden,
    gate_exists,
    gate_not_found,
    invalid_plan_days,
    invalid_renewal,
    renewal_would_shorten,
    user_not_found,
)
from app.services import auth as auth_service
from app.services import plans as plans_service
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


def _validate_plan_days(days: int | None) -> int:
    """Single copy of the plan-days bounds policy (creation AND renewal)."""
    if days is None or days <= 0 or days > PLAN_DAYS_MAX:
        raise invalid_plan_days()
    return days


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


class RenewPlanRequest(BaseModel):
    # Exactly one mode per request (FR4: "add days or set a new expiration
    # date"); the route enforces the XOR and the bounds. AwareDatetime rejects a
    # naive datetime at the boundary — ``expires_at`` is timestamptz and naive
    # comparisons raise TypeError (1.4 lesson).
    plan_days: int | None = None
    expires_at: AwareDatetime | None = None


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

    plan_days = (
        _validate_plan_days(body.plan_days)
        if body.role == "client"
        else None  # admins carry no plan
    )

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


# --- Client lifecycle: renew / block / unblock (Story 1.5) ----------------
#
# Non-CRUD actions as POST verb-suffix routes (architecture's
# ``/api/batches/{id}/pause`` convention). All gated by the module-level
# require_admin_or_owner singleton (FR4: "admin or owner"). Each follows
# delete_user's shape: target lookup → role guard → action → commit → _to_out.
# Targets are clients only — owner/admin rows carry no plan and admins are
# managed by the owner (1.3); a non-client target is forbidden().


async def _require_client_target(
    session: AsyncSession, user_id: int
) -> User:
    """Resolve a client target or raise (404 unknown, 403 non-client).

    The row is fetched ``FOR UPDATE``: all three lifecycle actions mutate it,
    and renew in particular is a read-modify-write (concurrent renewals must
    serialize, not lose an extension).
    """
    target = await users_repo.get_user_by_id(session, user_id, for_update=True)
    if target is None:
        raise user_not_found()
    if target.role != "client":
        raise forbidden()
    return target


@router.post("/users/{user_id}/renew", response_model=UserOut)
async def renew_plan(
    user_id: int,
    body: RenewPlanRequest,
    actor: User = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    """Renew a client's plan by adding days XOR setting a future date (AC1/AC2).

    Validation order: target exists & is a client → exactly one mode provided →
    mode-specific bounds. Login re-reads the new ``expires_at`` so a renewed
    expired client logs in normally (AC2) — no expiry code changes here.
    """
    target = await _require_client_target(session, user_id)

    if body.plan_days is not None:
        if body.expires_at is not None:
            raise invalid_renewal()  # both modes at once
        _validate_plan_days(body.plan_days)
        new_expiry = plans_service.compute_renewed_expiry(
            target.expires_at, body.plan_days
        )
    elif body.expires_at is not None:
        # AwareDatetime guarantees tz-aware. A past/now date is a lockout, not
        # a renewal (block is the tool for that); the upper bound mirrors
        # PLAN_DAYS_MAX so a stored far-future expiry can never overflow a
        # later add-days renewal (anchor + timedelta vs datetime.max).
        now = datetime.now(UTC)
        if body.expires_at <= now or body.expires_at > now + timedelta(
            days=PLAN_DAYS_MAX
        ):
            raise invalid_renewal()
        # Renew never shortens: an earlier-than-current date silently cuts an
        # active plan — that is a destructive action, not a renewal.
        if target.expires_at is not None and body.expires_at < target.expires_at:
            raise renewal_would_shorten()
        new_expiry = body.expires_at
    else:
        raise invalid_renewal()  # neither mode

    user = await plans_service.renew_plan(session, target, new_expiry)
    await session.commit()
    return _to_out(user)


async def _set_blocked(
    session: AsyncSession, user_id: int, *, blocked: bool
) -> UserOut:
    """Shared body of the block/unblock routes."""
    target = await _require_client_target(session, user_id)
    user = await plans_service.set_blocked(session, target, blocked=blocked)
    await session.commit()
    return _to_out(user)


@router.post("/users/{user_id}/block", response_model=UserOut)
async def block_user(
    user_id: int,
    actor: User = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    """Block a client and revoke their live sessions immediately (AC3)."""
    return await _set_blocked(session, user_id, blocked=True)


@router.post("/users/{user_id}/unblock", response_model=UserOut)
async def unblock_user(
    user_id: int,
    actor: User = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    """Unblock a client; they can log in again normally (AC4)."""
    return await _set_blocked(session, user_id, blocked=False)


# --- Password reset (Story 1.6) -------------------------------------------


class ResetPasswordResponse(BaseModel):
    # The ONLY place the temp plaintext ever appears — never in UserOut, logs,
    # or the DB (which stores only the argon2id hash).
    temp_password: str


@router.post("/users/{user_id}/reset-password", response_model=ResetPasswordResponse)
async def reset_password(
    user_id: int,
    actor: User = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_session),
) -> ResetPasswordResponse:
    """Reset a client's password to a one-time temp password (AC1).

    New hash + flag + revoke-all, atomically. Revocation mirrors block (1.5):
    any live session dies instantly, making "log in with the temp password"
    (AC2's entry point) the only path forward. Works on blocked/expired
    clients too — their login gates still apply in the existing order.
    """
    target = await _require_client_target(session, user_id)
    temp = auth_service.generate_temp_password()
    target.password_hash = auth_service.hash_password(temp)
    target.must_change_password = True
    await users_repo.revoke_all_sessions_for_user(session, target.id)
    await session.commit()
    return ResetPasswordResponse(temp_password=temp)


# --- Gate catalog CRUD (Story 2.1) -----------------------------------------
#
# Owner-only curation of the GLOBAL gate catalog (no tenant scoping — see the
# ``db.repos.gates`` module note). Values are stored VERBATIM, dot included
# (`.zo` stays `.zo`); delete is a soft-delete (``deleted_at``) so history that
# snapshots the gate string is never rewritten.

GATE_VALUE_MAX = 20
GATE_NAME_MAX = 80
_PG_INT_MAX = 2**31 - 1  # gates.id is int4; larger ids overflow the bind


def _validate_gate_value(value: str) -> str:
    """Single copy of the gate-value policy: trimmed, non-empty, no inner
    whitespace, ≤20 chars. Verbatim otherwise — the leading dot is data, not
    a format requirement."""
    value = value.strip()
    if not value:
        raise ValueError("gate vacío")
    if any(ch.isspace() or not ch.isprintable() for ch in value):
        raise ValueError("el gate no puede contener espacios ni caracteres invisibles")
    if len(value) > GATE_VALUE_MAX:
        raise ValueError("gate demasiado largo")
    return value


def _validate_gate_name(name: str) -> str:
    """Gate name policy: trimmed, non-empty, ≤80 chars. Spaces ARE allowed
    (it's a friendly label); only control/invisible chars are rejected."""
    name = name.strip()
    if not name:
        raise ValueError("nombre vacío")
    if any(not ch.isprintable() for ch in name):
        raise ValueError("el nombre no puede contener caracteres invisibles")
    if len(name) > GATE_NAME_MAX:
        raise ValueError("nombre demasiado largo")
    return name


class CreateGateRequest(BaseModel):
    value: str
    name: str
    category_id: int

    @field_validator("value")
    @classmethod
    def _valid_value(cls, v: str) -> str:
        return _validate_gate_value(v)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        return _validate_gate_name(v)


class UpdateGateRequest(BaseModel):
    value: str
    name: str
    category_id: int

    @field_validator("value")
    @classmethod
    def _valid_value(cls, v: str) -> str:
        return _validate_gate_value(v)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        return _validate_gate_name(v)


class GateOut(BaseModel):
    id: int
    value: str
    name: str
    category_id: int
    category_name: str
    created_at: datetime


class GateListResponse(BaseModel):
    items: list[GateOut]
    total: int


def gate_to_out(gate: Gate) -> GateOut:
    """Shared Gate → GateOut mapper (also used by the public gates router).

    Requires ``gate.category`` to be eagerly loaded (``selectinload`` /
    ``refresh``) — an async lazy-load here would raise.
    """
    return GateOut(
        id=gate.id,
        value=gate.value,
        name=gate.name,
        category_id=gate.category_id,
        category_name=gate.category.name,
        created_at=gate.created_at,
    )


async def _require_category(session: AsyncSession, category_id: int) -> GateCategory:
    """Resolve a category or raise 404 (out-of-int4 ids can't exist)."""
    if not 0 < category_id <= _PG_INT_MAX:
        raise category_not_found()
    category = await gate_categories_repo.get_by_id(session, category_id)
    if category is None:
        raise category_not_found()
    return category


@router.get("/gates", response_model=GateListResponse)
async def list_gates(
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> GateListResponse:
    """List active catalog entries (owner curation view)."""
    gates = await gates_repo.list_active(session)
    return GateListResponse(items=[gate_to_out(g) for g in gates], total=len(gates))


@router.post("/gates", response_model=GateOut, status_code=201)
async def create_gate(
    body: CreateGateRequest,
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> GateOut:
    """Add a gate to the catalog; duplicate ACTIVE value → 409 gate_exists."""
    category = await _require_category(session, body.category_id)
    if await gates_repo.get_active_by_value(session, body.value) is not None:
        raise gate_exists()
    try:
        gate = await gates_repo.create(
            session, value=body.value, name=body.name, category_id=category.id
        )
        await session.commit()
    except IntegrityError as exc:
        # The pre-check above is racy: a concurrent insert of the same value
        # only trips uq_gates_value_active at flush/commit. Map to the same
        # gate_exists contract instead of a 500.
        raise gate_exists() from exc
    await session.refresh(gate, ["category"])
    return gate_to_out(gate)


async def _require_active_gate(session: AsyncSession, gate_id: int) -> Gate:
    """Resolve an ACTIVE gate or raise 404 (missing and retired look the same).

    ``FOR UPDATE`` mirrors ``_require_client_target``: edit/delete are
    read-modify-write, so concurrent mutations serialize.
    """
    if not 0 < gate_id <= _PG_INT_MAX:
        # Out-of-range ids would overflow the int4 bind in asyncpg → 500;
        # they can't exist, so they are indistinguishable from "not found".
        raise gate_not_found()
    gate = await gates_repo.get_by_id(session, gate_id, for_update=True)
    if gate is None or gate.deleted_at is not None:
        raise gate_not_found()
    return gate


@router.patch("/gates/{gate_id}", response_model=GateOut)
async def update_gate(
    gate_id: int,
    body: UpdateGateRequest,
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> GateOut:
    """Edit a gate's value/name/category. History is untouched — batches snapshot the string."""
    gate = await _require_active_gate(session, gate_id)
    category = await _require_category(session, body.category_id)
    duplicate = await gates_repo.get_active_by_value(session, body.value)
    if duplicate is not None and duplicate.id != gate.id:
        raise gate_exists()
    gate.value = body.value
    gate.name = body.name
    gate.category_id = category.id
    try:
        await session.commit()
    except IntegrityError as exc:
        # Duplicate check is racy (the duplicate row isn't locked); a
        # concurrent create/edit of the same value trips the index at commit.
        raise gate_exists() from exc
    await session.refresh(gate, ["category"])
    return gate_to_out(gate)


@router.delete("/gates/{gate_id}", status_code=204)
async def delete_gate(
    gate_id: int,
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Retire a gate (soft-delete, AC5): hidden from selectors, row kept."""
    gate = await _require_active_gate(session, gate_id)
    await gates_repo.soft_delete(session, gate)
    await session.commit()


# --- Gate category CRUD (Story 2.2, owner addition) --------------------------
#
# Owner-only curation of the GLOBAL category list (no tenant scoping — see the
# ``db.repos.gate_categories`` module note). No soft-delete: deleting a
# category that still has ACTIVE gates → 409 category_in_use; retired gates
# never block (they are reassigned away first — see the repo).

CATEGORY_NAME_MAX = 80


def _validate_category_name(name: str) -> str:
    """Category name policy (same shape as ``_validate_gate_name``): trimmed,
    non-empty, ≤80 chars, no control/invisible chars; spaces allowed."""
    name = name.strip()
    if not name:
        raise ValueError("nombre vacío")
    if any(not ch.isprintable() for ch in name):
        raise ValueError("el nombre no puede contener caracteres invisibles")
    if len(name) > CATEGORY_NAME_MAX:
        raise ValueError("nombre demasiado largo")
    return name


class CreateCategoryRequest(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        return _validate_category_name(v)


class UpdateCategoryRequest(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        return _validate_category_name(v)


class CategoryOut(BaseModel):
    id: int
    name: str
    created_at: datetime


class CategoryListResponse(BaseModel):
    items: list[CategoryOut]
    total: int


def _category_to_out(category: GateCategory) -> CategoryOut:
    return CategoryOut(
        id=category.id, name=category.name, created_at=category.created_at
    )


@router.get("/gate-categories", response_model=CategoryListResponse)
async def list_gate_categories(
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> CategoryListResponse:
    """List every category, ordered by name."""
    categories = await gate_categories_repo.list_all(session)
    return CategoryListResponse(
        items=[_category_to_out(c) for c in categories], total=len(categories)
    )


@router.post("/gate-categories", response_model=CategoryOut, status_code=201)
async def create_gate_category(
    body: CreateCategoryRequest,
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> CategoryOut:
    """Add a category; duplicate name → 409 category_exists."""
    if await gate_categories_repo.get_by_name(session, body.name) is not None:
        raise category_exists()
    try:
        category = await gate_categories_repo.create(session, name=body.name)
        await session.commit()
    except IntegrityError as exc:
        # TOCTOU (2.1 review lesson): a concurrent insert of the same name
        # only trips uq_gate_categories_name at commit — same contract.
        raise category_exists() from exc
    return _category_to_out(category)


@router.patch("/gate-categories/{category_id}", response_model=CategoryOut)
async def update_gate_category(
    category_id: int,
    body: UpdateCategoryRequest,
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> CategoryOut:
    """Rename a category (gates keep pointing at it — nothing else moves)."""
    category = await _require_category(session, category_id)
    duplicate = await gate_categories_repo.get_by_name(session, body.name)
    if duplicate is not None and duplicate.id != category.id:
        raise category_exists()
    category.name = body.name
    try:
        await session.commit()
    except IntegrityError as exc:
        raise category_exists() from exc
    return _category_to_out(category)


@router.delete("/gate-categories/{category_id}", status_code=204)
async def delete_gate_category(
    category_id: int,
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a category; ACTIVE gates still assigned → 409 category_in_use.

    Retired gates don't block: they are reassigned to another category first
    (rows kept, 2.1 design). When the catalog has no other category to take
    them, the RESTRICT FK would fire — surfaced as the same 409.
    """
    category = await _require_category(session, category_id)
    if await gate_categories_repo.has_gates(session, category.id):
        raise category_in_use()
    detached = await gate_categories_repo.reassign_retired_gates(
        session, category.id
    )
    if not detached:  # retired gates exist and no other category can take them
        raise category_in_use()
    try:
        await gate_categories_repo.delete(session, category)
        await session.commit()
    except IntegrityError as exc:
        # Race: a gate was (re)assigned to this category after the check.
        raise category_in_use() from exc
