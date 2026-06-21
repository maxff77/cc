"""Admin router: user management (Story 1.3).

`/api/admin/users` — list/create/delete clients (and, for the owner, admins);
`/api/admin/tenants/{id}/sessions[/{session_id}]` — the read-only cross-tenant
support view (Story 3.6), every read audit-logged.

Authorization is enforced SERVER-SIDE here (the security boundary — the UI only
mirrors it). The actor's role/identity comes ONLY from ``require_role`` /
``get_current_user`` (the session), never from the request body. These queries
are GLOBAL/cross-tenant by design (an admin manages all clients) — see the
``db.repos.users`` module note.
"""

import logging
import math
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from pydantic import AwareDatetime, BaseModel, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.api.sessions import (
    SessionCcRow,
    SessionDetailOut,
    SessionOut,
    SessionResponseRow,
    session_to_out,
)
from app.core import send_worker
from app.core.broadcaster import broadcaster
from app.core.redact import redact_reply_text
from app.core.scheduler import scheduler
from app.core.telegram import gateway
from app.db.base import get_session
from app.db.models import Gate, GateCategory, Plan, User
from app.db.repos import audit as audit_repo
from app.db.repos import capture_sessions as capture_sessions_repo
from app.db.repos import gate_categories as gate_categories_repo
from app.db.repos import gates as gates_repo
from app.db.repos import plans as plans_repo
from app.db.repos import responses as responses_repo
from app.db.repos import tenants as tenants_repo
from app.db.repos import users as users_repo
from app.errors import (
    category_exists,
    category_in_use,
    category_not_found,
    forbidden,
    gate_exists,
    gate_not_found,
    invalid_admission_cap,
    invalid_contact,
    invalid_credits,
    invalid_gate,
    invalid_live_channel,
    invalid_plan,
    invalid_plan_days,
    invalid_renewal,
    invalid_send_interval,
    plan_not_found,
    renewal_would_shorten,
    session_not_found,
    telegram_unauthorized,
    tenant_not_found,
    user_not_found,
)
from app.services import admission as admission_service
from app.services import auth as auth_service
from app.services import live_forward as live_forward_service
from app.services import pacing as pacing_service
from app.services import plans as plans_service
from app.services import users as users_service

logger = logging.getLogger(__name__)

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
# Telegram username shape (5–32 chars, letters/digits/underscore). Stored
# canonical: no leading '@', no t.me prefix. The frontend re-adds '@' + link.
_CONTACT_RE = re.compile(r"[A-Za-z0-9_]{5,32}")
# A pasted Telegram link prefix (any case): optional scheme, optional www., the
# t.me/telegram.me host, and the optional /s/ "share" segment. Stripped so an
# operator can paste a copied URL, not only a bare handle.
_CONTACT_URL_PREFIX = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:t|telegram)\.me/(?:s/)?", re.IGNORECASE
)
# Upper bound on plan length; guards datetime/timedelta overflow on a fat-finger
# value. ~100 years is far beyond any real plan. Lower bound stays in the route
# (so a missing/<=0 value surfaces the invalid_plan_days code, not a 422).
PLAN_DAYS_MAX = 36500


def _normalize_contact(value: str | None) -> str | None:
    """Single source of truth for the Telegram-handle format (create + edit).

    Lenient on input (a pasted '@handle' or 't.me/handle' link is accepted),
    strict on storage: returns the canonical handle (no '@', no prefix) or
    ``None`` when empty. A malformed value raises ``invalid_contact``.
    """
    if value is None:
        return None
    v = _CONTACT_URL_PREFIX.sub("", value.strip())
    v = v.lstrip("@").strip()
    # Drop any trailing path/query/fragment left after the handle
    # (e.g. a pasted "t.me/user?start=x" → "user").
    v = re.split(r"[/?#]", v, maxsplit=1)[0].strip()
    if v == "":
        return None
    if not _CONTACT_RE.fullmatch(v):
        raise invalid_contact()
    return v


def _validate_plan_days(days: int | None) -> int:
    """Single copy of the plan-days bounds policy (creation AND renewal)."""
    if days is None or days <= 0 or days > PLAN_DAYS_MAX:
        raise invalid_plan_days()
    return days


class CreateUserRequest(BaseModel):
    email: str
    password: str
    role: str = "client"
    # Plan modes for a client (XOR, resolved in the route): a catalog ``plan_id``
    # (plan-catalog feature — sets the link AND derives expires_at from the plan's
    # duration_days) OR the legacy ``plan_days`` (no plan link). When both are
    # given the catalog plan wins (services.users.create_account precedence).
    plan_id: int | None = None
    plan_days: int | None = None
    # Raw handle; normalized/validated in the route so a bad value surfaces the
    # invalid_contact code (400), not a pydantic 422.
    contact: str | None = None

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


class SetContactRequest(BaseModel):
    # Raw handle (or null/empty to clear); normalized in the route.
    contact: str | None = None


class RenewPlanRequest(BaseModel):
    # Exactly one mode per request (FR4: "add days or set a new expiration
    # date", plus the plan-catalog ``plan_id``); the route enforces the XOR and
    # the bounds. AwareDatetime rejects a naive datetime at the boundary —
    # ``expires_at`` is timestamptz and naive comparisons raise TypeError (1.4
    # lesson). Assigning a ``plan_id`` updates ``user.plan_id`` AND extends
    # expires_at by the plan's duration_days, anchored on max(now, current).
    plan_id: int | None = None
    plan_days: int | None = None
    expires_at: AwareDatetime | None = None


class UserOut(BaseModel):
    id: int
    email: str
    role: str
    tenant_id: int
    expires_at: datetime | None
    is_blocked: bool
    contact: str | None
    # The tenant's credit balance (credits feature) — shown in the admin users
    # table and updated by recharge / plan renewal. Lives on the tenant, so it
    # is passed in rather than read off the ``User`` row.
    credit_balance: int = 0


class UserListResponse(BaseModel):
    items: list[UserOut]


def _to_out(user: User, *, credit_balance: int = 0) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        role=user.role,
        tenant_id=user.tenant_id,
        expires_at=user.expires_at,
        is_blocked=user.is_blocked,
        contact=user.contact,
        credit_balance=credit_balance,
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
    # Credit balances in one query (credits feature) — the table shows each
    # client's balance; a missing tenant defaults to 0.
    balances = await tenants_repo.get_credit_balances(
        session, [u.tenant_id for u in users]
    )
    return UserListResponse(
        items=[
            _to_out(u, credit_balance=balances.get(u.tenant_id, 0)) for u in users
        ]
    )


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
    - 'client' carries a plan: either a catalog ``plan_id`` (plan-catalog
      feature — expires_at derived from the plan's duration_days; unknown or
      inactive plan → invalid_plan in the service) OR a positive ``plan_days``
      (legacy; else invalid_plan_days). 'admin' ignores both — no plan.
    """
    if body.role not in ("client", "admin"):
        raise forbidden()

    if body.role == "admin" and actor.role != "owner":
        raise forbidden()

    # A client must carry a plan via exactly one mode. ``plan_id`` (catalog)
    # takes precedence and is validated in the service (exists + active); the
    # legacy ``plan_days`` is bounds-checked here. Admins ignore both.
    plan_id: int | None = None
    plan_days: int | None = None
    if body.role == "client":
        if body.plan_id is not None:
            # Bounds-check before the DB lookup: an out-of-int4 id overflows the
            # asyncpg bind and raises a raw DataError → 500 (2.1 review lesson).
            # Reject it as invalid_plan, matching the unknown/inactive contract.
            if not 0 < body.plan_id <= _PG_INT_MAX:
                raise invalid_plan()
            plan_id = body.plan_id  # service validates exists + active
        else:
            plan_days = _validate_plan_days(body.plan_days)

    user = await users_service.create_account(
        session,
        email=body.email,
        password=body.password,
        role=body.role,
        plan_id=plan_id,
        plan_days=plan_days,
        # Contact is a client-only field (renewal outreach); admins carry none
        # and have no edit path, so never store one for them.
        contact=_normalize_contact(body.contact) if body.role == "client" else None,
    )
    await session.commit()
    balance = await tenants_repo.get_credit_balance(session, user.tenant_id)
    return _to_out(user, credit_balance=balance)


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
    """Renew a client's plan: assign a catalog plan XOR add days XOR set a
    future date (AC1/AC2 + plan-catalog feature).

    Validation order: target exists & is a client → exactly one mode provided →
    mode-specific bounds. Login re-reads the new ``expires_at`` so a renewed
    expired client logs in normally (AC2) — no expiry code changes here.

    Plan-catalog mode (``plan_id``): the plan must exist AND be active (else
    invalid_plan); it links ``target.plan_id`` and extends expires_at by the
    plan's duration_days anchored on ``max(now, current)`` (renewing an active
    plan stacks days; renewing an expired one grants days from today).
    """
    target = await _require_client_target(session, user_id)

    # Exactly one renewal mode. ``plan_id`` (catalog) is mutually exclusive with
    # the legacy add-days / set-date modes.
    modes = sum(
        x is not None for x in (body.plan_id, body.plan_days, body.expires_at)
    )
    if modes != 1:
        raise invalid_renewal()

    if body.plan_id is not None:
        # Bounds-check before the DB lookup (out-of-int4 ids overflow the
        # asyncpg bind → raw DataError → 500). Assigning an unknown/inactive
        # plan is invalid_plan (the spec I/O matrix's "unknown/inactive plan →
        # invalid_plan" contract — same surface as create-user), not the bare
        # plan_not_found get_plan would raise.
        if not 0 < body.plan_id <= _PG_INT_MAX:
            raise invalid_plan()
        plan = await plans_repo.get_by_id(session, body.plan_id)
        if plan is None or not plan.is_active:
            raise invalid_plan()
        target.plan_id = plan.id
        new_expiry = plans_service.compute_renewed_expiry_from_duration(
            target.expires_at, plan.duration_days
        )
        # Credit top-up on renewal (credits feature): a catalog-plan renewal
        # ADDS the plan's credits to the tenant's balance (the package is
        # granted again). The legacy add-days / set-date renewal modes below
        # never touch credits.
        if plan.credits:
            await tenants_repo.add_credits(
                session, target.tenant_id, plan.credits
            )
    elif body.plan_days is not None:
        _validate_plan_days(body.plan_days)
        new_expiry = plans_service.compute_renewed_expiry(
            target.expires_at, body.plan_days
        )
    else:
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

    user = await plans_service.renew_plan(session, target, new_expiry)
    await session.commit()
    balance = await tenants_repo.get_credit_balance(session, user.tenant_id)
    return _to_out(user, credit_balance=balance)


async def _set_blocked(
    session: AsyncSession, user_id: int, *, blocked: bool
) -> UserOut:
    """Shared body of the block/unblock routes."""
    target = await _require_client_target(session, user_id)
    user = await plans_service.set_blocked(session, target, blocked=blocked)
    await session.commit()
    balance = await tenants_repo.get_credit_balance(session, user.tenant_id)
    return _to_out(user, credit_balance=balance)


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


@router.post("/users/{user_id}/contact", response_model=UserOut)
async def set_contact(
    user_id: int,
    body: SetContactRequest,
    actor: User = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    """Set (or clear) a client's Telegram contact for renewal outreach.

    Same shape as the other lifecycle actions: client target → normalize →
    persist → commit. An empty/null body clears the contact; a malformed handle
    raises ``invalid_contact`` (400).
    """
    target = await _require_client_target(session, user_id)
    user = await users_service.set_contact(
        session, target, _normalize_contact(body.contact)
    )
    await session.commit()
    balance = await tenants_repo.get_credit_balance(session, user.tenant_id)
    return _to_out(user, credit_balance=balance)


# --- Credit recharge (credits feature) ------------------------------------
#
# Owner-only: set a client's credit balance directly. Independent of the plan
# (which TOPS UP on renewal) — the owner can add/correct credits any time. An
# absolute set (the UI pre-fills the current balance), not a delta. Emits the
# same ``credits.updated`` WS event the capture charge does, so a connected
# cockpit reflects the new balance live.


class RechargeCreditsRequest(BaseModel):
    # The new absolute balance. Bounds-checked in the route (>=0, int4 ceiling)
    # so a fat-finger value surfaces invalid_credits, not a raw 422/500.
    credit_balance: int


@router.post("/users/{user_id}/credits", response_model=UserOut)
async def recharge_credits(
    user_id: int,
    body: RechargeCreditsRequest,
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    """Set a client's credit balance (credits feature) — owner-only recharge.

    Absolute set (the plan-grant path tops up on renewal instead). Target must
    be a client; out-of-range value → 400 invalid_credits.
    """
    if not 0 <= body.credit_balance <= _PG_INT_MAX:
        raise invalid_credits()
    target = await _require_client_target(session, user_id)
    new_balance = await tenants_repo.set_credit_balance(
        session, target.tenant_id, body.credit_balance
    )
    await session.commit()
    # Live cockpit update: the client may be connected — push the new balance
    # (the reducer assigns it). ``new_balance`` is None only if the tenant row
    # vanished mid-request (the client target lock makes that practically
    # impossible); fall back to the requested value.
    resolved = new_balance if new_balance is not None else body.credit_balance
    await broadcaster.emit(
        target.tenant_id, "credits.updated", {"balance": resolved}
    )
    return _to_out(target, credit_balance=resolved)


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
GATE_DISPLAY_VALUE_MAX = 80
_PG_INT_MAX = 2**31 - 1  # gates.id is int4; larger ids overflow the bind


def _validate_gate_value(value: str) -> str:
    """Single copy of the gate-value policy: trimmed, internal space-runs
    collapsed, non-empty, ≤20 chars. A single inner ASCII space IS allowed
    (e.g. ``/xx x`` — a space-separated checker command); tabs, newlines and
    other invisible/non-printable chars are rejected. ``str.isprintable()``
    keeps the plain ASCII space (0x20) but flags every other separator/control
    char. Otherwise verbatim — the leading dot is data, not a format
    requirement."""
    value = value.strip()
    if not value:
        raise ValueError("gateway vacío")
    if any(not ch.isprintable() for ch in value):
        raise ValueError(
            "el gateway no puede contener tabulaciones, saltos de línea ni caracteres invisibles"
        )
    # Collapse internal ASCII-space runs to one. A stored double space would
    # desync apply_gate's ``startswith(gate_value + " ")`` dedup and silently
    # double-prefix re-pasted lines — the send-corruption class this repo guards.
    value = re.sub(r" {2,}", " ", value)
    if len(value) > GATE_VALUE_MAX:
        raise ValueError("gateway demasiado largo")
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


def _validate_gate_display_value(display_value: str) -> str:
    """Policy for the gate's "Comando visible": trimmed, non-empty, ≤80 chars. Spaces ARE
    allowed (owner-authored display string); only control/invisible chars are
    rejected. Same idiom as ``_validate_gate_name`` — NOT the real command, so
    no leading-dot or apply_gate space-collapse concerns apply."""
    display_value = display_value.strip()
    if not display_value:
        raise ValueError("comando visible vacío")
    if any(not ch.isprintable() for ch in display_value):
        raise ValueError("el comando visible no puede contener caracteres invisibles")
    if len(display_value) > GATE_DISPLAY_VALUE_MAX:
        raise ValueError("comando visible demasiado largo")
    return display_value


class CreateGateRequest(BaseModel):
    value: str
    name: str
    display_value: str
    category_id: int
    # Credits charged per captured ✅ for this gate (credits feature). 0 ⇒ free.
    # Bounds-checked in the route (invalid_gate), not here, to surface the
    # {code, message} contract instead of a pydantic 422.
    credit_cost: int = 0

    @field_validator("value")
    @classmethod
    def _valid_value(cls, v: str) -> str:
        return _validate_gate_value(v)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        return _validate_gate_name(v)

    @field_validator("display_value")
    @classmethod
    def _valid_display_value(cls, v: str) -> str:
        return _validate_gate_display_value(v)


class UpdateGateRequest(BaseModel):
    value: str
    name: str
    display_value: str
    category_id: int
    credit_cost: int = 0

    @field_validator("value")
    @classmethod
    def _valid_value(cls, v: str) -> str:
        return _validate_gate_value(v)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        return _validate_gate_name(v)

    @field_validator("display_value")
    @classmethod
    def _valid_display_value(cls, v: str) -> str:
        return _validate_gate_display_value(v)


def _validate_gate_credit_cost(credit_cost: int) -> None:
    """Gate credit-cost bounds (credits feature): 0 (free) .. int4 ceiling.

    Surfaced as invalid_gate (400) — same route-level idiom as
    ``_validate_plan_fields``."""
    if not 0 <= credit_cost <= _PG_INT_MAX:
        raise invalid_gate("El costo en créditos no puede ser negativo.")


class GateOut(BaseModel):
    """Owner-facing gate shape — carries the real ``value`` (owner-only). The
    public catalog router uses ``PublicGateOut`` (no ``value``) instead."""

    id: int
    value: str
    name: str
    display_value: str
    credit_cost: int
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
        display_value=gate.display_value,
        credit_cost=gate.credit_cost,
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
    _validate_gate_credit_cost(body.credit_cost)
    category = await _require_category(session, body.category_id)
    if await gates_repo.get_active_by_value(session, body.value) is not None:
        raise gate_exists()
    try:
        gate = await gates_repo.create(
            session,
            value=body.value,
            name=body.name,
            display_value=body.display_value,
            credit_cost=body.credit_cost,
            category_id=category.id,
        )
        await session.commit()
    except IntegrityError as exc:
        # The pre-check above is racy: a concurrent insert of the same value
        # only trips uq_gates_value_active at flush/commit — but a concurrent
        # category delete trips the FK instead (2-2 deferred fix): map each
        # constraint to its own error instead of a misleading "duplicate".
        if "fk_gates_category_id" in str(exc.orig):
            raise category_not_found() from exc
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
    _validate_gate_credit_cost(body.credit_cost)
    gate = await _require_active_gate(session, gate_id)
    category = await _require_category(session, body.category_id)
    duplicate = await gates_repo.get_active_by_value(session, body.value)
    if duplicate is not None and duplicate.id != gate.id:
        raise gate_exists()
    gate.value = body.value
    gate.name = body.name
    gate.display_value = body.display_value
    gate.credit_cost = body.credit_cost
    gate.category_id = category.id
    try:
        await session.commit()
    except IntegrityError as exc:
        # Duplicate check is racy (the duplicate row isn't locked); a
        # concurrent create/edit of the same value trips the index at commit.
        # A concurrent category delete trips the FK instead (2-2 deferred fix).
        if "fk_gates_category_id" in str(exc.orig):
            raise category_not_found() from exc
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


# --- Admission control (Story 4.2) -------------------------------------------
#
# Owner-only knob: the cap on concurrent active senders. Lives in
# ``system_settings`` (hot-configurable, durable) — 0 disables admission
# control entirely (pure Epic 2 adaptive-interval semantics).


class AdmissionOut(BaseModel):
    max_active_senders: int  # 0 = disabled


class UpdateAdmissionRequest(BaseModel):
    max_active_senders: int


@router.get("/admission", response_model=AdmissionOut)
async def get_admission(
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> AdmissionOut:
    """Current admission cap (0 = disabled)."""
    return AdmissionOut(max_active_senders=await admission_service.get_cap(session))


@router.put("/admission", response_model=AdmissionOut)
async def update_admission(
    body: UpdateAdmissionRequest,
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> AdmissionOut:
    """Set the admission cap; 0 disables it.

    Bounds checked in the route (invalid_plan_days idiom — the error code
    surfaces instead of a raw 422). Raising or disabling the cap must promote
    waiting batches NOW, not within the worker's next idle second — hence the
    ``wake()`` after commit. Lowering it never expels anyone: active senders
    keep their slot; only new admissions wait (recorded decision).
    """
    if not 0 <= body.max_active_senders <= admission_service.CAP_MAX:
        raise invalid_admission_cap()
    await admission_service.set_cap(session, body.max_active_senders)
    await session.commit()
    send_worker.wake()
    return AdmissionOut(max_active_senders=body.max_active_senders)


# --- Send interval (configurable pacing) -------------------------------------
#
# Owner-only knob: the constant interval between sends on the shared account
# (the scheduler floor ``G``). Lives in ``system_settings`` (hot-configurable,
# durable). Bounded 2–30s server-side — lowering it raises ban risk, so a
# client/admin can never touch it.


class IntervalOut(BaseModel):
    interval_seconds: float


class UpdateIntervalRequest(BaseModel):
    interval_seconds: float


@router.get("/interval", response_model=IntervalOut)
async def get_interval(
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> IntervalOut:
    """Current send interval in seconds (env default when unset)."""
    return IntervalOut(interval_seconds=await pacing_service.get_interval(session))


@router.put("/interval", response_model=IntervalOut)
async def update_interval(
    body: UpdateIntervalRequest,
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> IntervalOut:
    """Set the constant send interval (seconds), bounded 2–30s.

    Bounds checked in the route (invalid_admission_cap idiom — a clean error
    code, not a raw 422). Applied live to the scheduler floor after commit;
    NO ``send_worker.wake()`` on purpose — pacing is wake-immune, so a control
    never makes the shared account send faster mid-sleep. The FloodWait
    governor keeps self-tuning the live pace UP from this new floor.
    """
    # Reject NaN/±Inf explicitly — a chained ``MIN <= x <= MAX`` happens to
    # drop them (NaN compares False), but that is correctness-by-accident on a
    # ban-safety knob; ``isfinite`` makes the intent refactor-proof.
    if not math.isfinite(body.interval_seconds) or not (
        pacing_service.INTERVAL_MIN
        <= body.interval_seconds
        <= pacing_service.INTERVAL_MAX
    ):
        raise invalid_send_interval()
    await pacing_service.set_interval(session, body.interval_seconds)
    await session.commit()
    scheduler.set_floor(body.interval_seconds)
    return IntervalOut(interval_seconds=body.interval_seconds)


# --- Live-forward channel (Amazon lives → Telegram) --------------------------
#
# Owner-only knob: the GLOBAL Telegram channel/group where every Amazon "live"
# (approved card) is forwarded verbatim. Lives in ``system_settings``. Empty =
# forwarding disabled. The id/@username is validated against telegram on save
# (``resolve_one``, like send targets) and stored as the resolved marked id.


class LiveChannelOut(BaseModel):
    live_forward_channel: str  # "" = disabled


class UpdateLiveChannelRequest(BaseModel):
    live_forward_channel: str


@router.get("/live-channel", response_model=LiveChannelOut)
async def get_live_channel(
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> LiveChannelOut:
    """Current live-forward channel id ("" = disabled)."""
    return LiveChannelOut(
        live_forward_channel=await live_forward_service.get_channel(session)
    )


@router.put("/live-channel", response_model=LiveChannelOut)
async def update_live_channel(
    body: UpdateLiveChannelRequest,
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> LiveChannelOut:
    """Set the live-forward channel; empty string disables forwarding.

    A non-empty value is resolved against telegram first (same validation as
    send targets); an unresolvable id/@username raises ``invalid_live_channel``
    instead of silently storing a dead destination. The RESOLVED marked id is
    persisted so the forward send doesn't need to re-resolve a bare username.
    """
    raw = body.live_forward_channel.strip()
    if not raw:
        await live_forward_service.set_channel(session, "")
        await session.commit()
        return LiveChannelOut(live_forward_channel="")
    # Distinguish a transient session outage from a bad channel (mirror
    # create_target): a down/unauthorized gateway makes resolve_one return None
    # for ANY id, so guard first and surface 503 "retry later" instead of the
    # misleading invalid_live_channel.
    if not gateway.authorized:
        raise telegram_unauthorized()
    resolved = await gateway.resolve_one(live_forward_service.as_identifier(raw))
    if resolved is None:
        raise invalid_live_channel()
    await live_forward_service.set_channel(session, str(resolved))
    await session.commit()
    return LiveChannelOut(live_forward_channel=str(resolved))


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
    # Special-mode feature: gates in this category capture in special mode
    # (Approveds-count validity + stats stripping). Defaults off.
    special_mode: bool = False
    # Cookie-vault feature: gates in this category accept per-account cookies
    # (the cockpit shows the cookie manager). Defaults off.
    cookie_mode: bool = False

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        return _validate_category_name(v)


class UpdateCategoryRequest(BaseModel):
    name: str
    # Optional: ``None`` leaves the flag untouched so a plain rename never
    # resets it; the owner's toggle sends an explicit boolean.
    special_mode: bool | None = None
    # Optional: ``None`` leaves cookie mode untouched (cookie-vault feature),
    # same rename-safe semantics as ``special_mode``.
    cookie_mode: bool | None = None

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        return _validate_category_name(v)


class CategoryOut(BaseModel):
    id: int
    name: str
    special_mode: bool
    cookie_mode: bool
    created_at: datetime


class CategoryListResponse(BaseModel):
    items: list[CategoryOut]
    total: int


def _category_to_out(category: GateCategory) -> CategoryOut:
    return CategoryOut(
        id=category.id,
        name=category.name,
        special_mode=category.special_mode,
        cookie_mode=category.cookie_mode,
        created_at=category.created_at,
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
        category = await gate_categories_repo.create(
            session, name=body.name, special_mode=body.special_mode
        )
        # cookie_mode (cookie-vault feature) is set on the flushed ORM row
        # before commit — the ``gate_categories_repo.create`` signature stays
        # untouched (out of this stage's scope), same effect as the column
        # default plus this explicit value.
        category.cookie_mode = body.cookie_mode
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
    """Rename a category and/or toggle its special mode / cookie mode (gates
    keep pointing at it — nothing else moves). ``special_mode``/``cookie_mode``
    omitted ⇒ left untouched."""
    category = await _require_category(session, category_id)
    duplicate = await gate_categories_repo.get_by_name(session, body.name)
    if duplicate is not None and duplicate.id != category.id:
        raise category_exists()
    category.name = body.name
    if body.special_mode is not None:
        category.special_mode = body.special_mode
    if body.cookie_mode is not None:
        category.cookie_mode = body.cookie_mode
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


# --- Pricing-plan catalog CRUD (plan-catalog feature) ------------------------
#
# Owner-only curation of the GLOBAL pricing-plan catalog (no tenant scoping —
# see the ``db.repos.plans`` module note). Plans hold price + duration +
# per-tenant antispam cooldown + a max-lines-per-batch cap. The catalog ships
# EMPTY (nothing seeded). Delete is RESTRICTed while ≥1 client references the
# plan (409 plan_in_use) — retire via ``is_active=false`` instead, so historical
# assignments never dangle. Money/seconds are exact ``Decimal`` (Numeric
# columns); the route validates field bounds BEFORE the service runs
# (antispam/duration/max-lines >= 1, price >= 0) so a bad value surfaces the
# invalid_plan code, not a raw 422.

PLAN_NAME_MAX = 80
PLAN_DURATION_MAX = PLAN_DAYS_MAX  # same ~100-year ceiling as plan renewal
# Numeric(6,2) holds up to 9999.99; the antispam cooldown can only SLOW a tenant
# below the account-wide floor, so a generous upper bound is harmless.
PLAN_ANTISPAM_MAX = 9999
PLAN_MAX_LINES_MAX = _PG_INT_MAX
PLAN_PRICE_MAX = Decimal("99999999.99")  # Numeric(10,2) column ceiling


def _validate_plan_name(name: str) -> str:
    """Plan name policy (same shape as ``_validate_gate_name``): trimmed,
    non-empty, ≤80 chars, no control/invisible chars; spaces allowed."""
    name = name.strip()
    if not name:
        raise ValueError("nombre vacío")
    if any(not ch.isprintable() for ch in name):
        raise ValueError("el nombre no puede contener caracteres invisibles")
    if len(name) > PLAN_NAME_MAX:
        raise ValueError("nombre demasiado largo")
    return name


class CreatePlanRequest(BaseModel):
    name: str
    # Money is exact (Decimal); pydantic v2 coerces JSON numbers to Decimal.
    price_usd: Decimal
    duration_days: int
    antispam_seconds: Decimal
    max_lines_per_batch: int
    # Credits granted to the tenant on assign/renew (credits feature). 0 ⇒ a
    # time-only plan. Bounds-checked in _validate_plan_fields.
    credits: int = 0
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        return _validate_plan_name(v)


class UpdatePlanRequest(BaseModel):
    # Partial edit: every field optional; only the provided ones are written.
    # ``None`` means "leave unchanged" — so a deactivate is ``is_active: false``.
    name: str | None = None
    price_usd: Decimal | None = None
    duration_days: int | None = None
    antispam_seconds: Decimal | None = None
    max_lines_per_batch: int | None = None
    credits: int | None = None
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str | None) -> str | None:
        return None if v is None else _validate_plan_name(v)


class PlanOut(BaseModel):
    id: int
    name: str
    price_usd: Decimal
    duration_days: int
    antispam_seconds: Decimal
    max_lines_per_batch: int
    credits: int
    is_active: bool
    # The gift-key default ("basic") tier — at most one plan true (gift-keys
    # feature). The UI shows which plan keys grant; set via /plans/{id}/default.
    is_default: bool
    created_at: datetime


class PlanListResponse(BaseModel):
    items: list[PlanOut]
    total: int


def _plan_to_out(plan: Plan) -> PlanOut:
    return PlanOut(
        id=plan.id,
        name=plan.name,
        price_usd=plan.price_usd,
        duration_days=plan.duration_days,
        antispam_seconds=plan.antispam_seconds,
        max_lines_per_batch=plan.max_lines_per_batch,
        credits=plan.credits,
        is_active=plan.is_active,
        is_default=plan.is_default,
        created_at=plan.created_at,
    )


def _validate_plan_fields(
    *,
    price_usd: Decimal,
    duration_days: int,
    antispam_seconds: Decimal,
    max_lines_per_batch: int,
    credits: int,
) -> None:
    """Field-bound policy (creation AND edit), surfaced as invalid_plan (400).

    Bounds: ``antispam_seconds >= 1``, ``duration_days >= 1``,
    ``max_lines_per_batch >= 1``, ``price_usd >= 0``, ``credits >= 0`` — plus
    the column ceilings so a fat-finger value can't overflow Numeric/int4. The
    message is field-specific Spanish copy (the invalid_plan contract accepts
    an override)."""
    if not Decimal(1) <= antispam_seconds <= PLAN_ANTISPAM_MAX:
        raise invalid_plan("El antispam debe ser de al menos 1 segundo.")
    if not 1 <= duration_days <= PLAN_DURATION_MAX:
        raise invalid_plan("La duración debe ser de al menos 1 día.")
    if not 1 <= max_lines_per_batch <= PLAN_MAX_LINES_MAX:
        raise invalid_plan("El máximo de líneas por lote debe ser al menos 1.")
    if not Decimal(0) <= price_usd <= PLAN_PRICE_MAX:
        raise invalid_plan("El precio no puede ser negativo.")
    if not 0 <= credits <= _PG_INT_MAX:
        raise invalid_plan("Los créditos del plan no pueden ser negativos.")


@router.get("/plans", response_model=PlanListResponse)
async def list_plans(
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> PlanListResponse:
    """List the full catalog (active AND retired), ordered by id."""
    plans = await plans_service.list_plans(session)
    return PlanListResponse(
        items=[_plan_to_out(p) for p in plans], total=len(plans)
    )


@router.get("/plans/active", response_model=PlanListResponse)
async def list_active_plans(
    actor: User = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_session),
) -> PlanListResponse:
    """Active plans only — the read needed to populate the client create/renew
    selector. Plan CRUD stays owner-only (``/plans`` GET/POST/PATCH/DELETE);
    this narrow read is admin-accessible so a non-owner admin (who CAN create
    and renew clients) still has the sellable tiers to choose from — otherwise
    the selector 403s and the documented admin function breaks. No retired
    plans leak: only ``is_active`` rows, no full-catalog disclosure."""
    plans = await plans_service.list_plans(session, active_only=True)
    return PlanListResponse(
        items=[_plan_to_out(p) for p in plans], total=len(plans)
    )


@router.post("/plans", response_model=PlanOut, status_code=201)
async def create_plan(
    body: CreatePlanRequest,
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> PlanOut:
    """Add a plan; bad field → 400 invalid_plan, duplicate name → 409
    plan_name_taken."""
    _validate_plan_fields(
        price_usd=body.price_usd,
        duration_days=body.duration_days,
        antispam_seconds=body.antispam_seconds,
        max_lines_per_batch=body.max_lines_per_batch,
        credits=body.credits,
    )
    plan = await plans_service.create_plan(
        session,
        name=body.name,
        price_usd=body.price_usd,
        duration_days=body.duration_days,
        antispam_seconds=body.antispam_seconds,
        max_lines_per_batch=body.max_lines_per_batch,
        credits=body.credits,
        is_active=body.is_active,
    )
    await session.commit()
    return _plan_to_out(plan)


@router.patch("/plans/{plan_id}", response_model=PlanOut)
async def update_plan(
    plan_id: int,
    body: UpdatePlanRequest,
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> PlanOut:
    """Edit a plan (partial). Unknown → 404 plan_not_found, duplicate name →
    409 plan_name_taken, bad field → 400 invalid_plan.

    Only fields present in the body are written. The post-edit values (current
    row merged with the patch) are bounds-checked so an edit can't drop a field
    below its floor. Existing clients keep their already-derived ``expires_at``;
    editing the plan changes only future assignments/renewals + the live
    antispam cooldown — never retroactively (recorded scope).
    """
    if not 0 < plan_id <= _PG_INT_MAX:
        raise plan_not_found()
    # Merge patch over the current row to bounds-check the resulting plan.
    current = await plans_service.get_plan(session, plan_id)
    fields = body.model_dump(exclude_none=True)
    _validate_plan_fields(
        price_usd=fields.get("price_usd", current.price_usd),
        duration_days=fields.get("duration_days", current.duration_days),
        antispam_seconds=fields.get("antispam_seconds", current.antispam_seconds),
        max_lines_per_batch=fields.get(
            "max_lines_per_batch", current.max_lines_per_batch
        ),
        credits=fields.get("credits", current.credits),
    )
    if not fields:  # nothing to change — return the current row unchanged
        return _plan_to_out(current)
    plan = await plans_service.update_plan(session, plan_id, **fields)
    await session.commit()
    return _plan_to_out(plan)


@router.delete("/plans/{plan_id}", status_code=204)
async def delete_plan(
    plan_id: int,
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a plan. Unknown → 404 plan_not_found; referenced by ≥1 client →
    409 plan_in_use (retire via is_active=false instead)."""
    if not 0 < plan_id <= _PG_INT_MAX:
        raise plan_not_found()
    await plans_service.delete_plan(session, plan_id)
    await session.commit()


@router.post("/plans/{plan_id}/default", response_model=PlanOut)
async def set_plan_default(
    plan_id: int,
    actor: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> PlanOut:
    """Flag a plan as the DEFAULT ("basic") tier gift keys grant to a plan-less
    claimer (gift-keys feature). At most one default — flagging one clears the
    prior. Owner-only: admins mint keys but never choose the tier."""
    if not 0 < plan_id <= _PG_INT_MAX:
        raise plan_not_found()
    plan = await plans_service.set_default_plan(session, plan_id)
    await session.commit()
    return _plan_to_out(plan)


# --- Cross-tenant support view (Story 3.6) ----------------------------------
#
# THE ONLY place in the system where a handler passes the repos a ``tenant_id``
# that does NOT come from ``user.tenant_id`` but from the PATH — the
# intentional cross of architecture ("Owner/admin cross-tenant access goes
# through explicit ``for_tenant(id)`` support paths, audit-logged"). The repos'
# ``list_for_tenant``/``get_for_tenant`` ARE those support paths, reused with
# the path's tenant. Every read writes an ``audit_log`` row and COMMITS it
# BEFORE serving data (fail-closed: no record, no data). Read-only is
# structural: these two GET are the only verbs under ``/api/admin/tenants/...``
# — no rename, no continue, no delete, no export (FastAPI answers 405 to
# anything else).


class SupportSessionsResponse(BaseModel):
    tenant_id: int
    email: str
    items: list[SessionOut]
    total: int


async def _reject_cross_site(request: Request) -> None:
    """Refuse foreign-origin requests on the audited support GETs.

    These are GETs that WRITE (the audit row is the condition of service) and
    the session cookie is SameSite=Lax, which still rides cross-site TOP-LEVEL
    navigations: a third party could make an admin's browser mint an audit row
    ("this admin viewed this client") — and dump the client's data in their
    tab — just by navigating them here. Every fetch() from the SPA sends
    ``Sec-Fetch-Site: same-origin``; browsers send ``cross-site`` on
    attacker-initiated navigations. Header absent (non-browser clients, tests)
    → allowed: those carry no ambient cookie to forge with.
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None and site != "same-origin":
        raise forbidden()


async def _require_client_tenant(session: AsyncSession, tenant_id: int) -> User:
    """Resolve the target CLIENT's user by tenant or raise 404.

    Unknown tenant, a tenant whose user is NOT a client (probing the owner's
    or an admin's tenant) and an out-of-int4 id all answer the IDENTICAL
    ``tenant_not_found`` — existence is never leaked. The returned user's
    ``email`` feeds the support header ("Sesiones de {email}").
    """
    if not 0 < tenant_id <= _PG_INT_MAX:
        raise tenant_not_found()
    target = await users_repo.get_user_by_tenant(session, tenant_id)
    if target is None or target.role != "client":
        raise tenant_not_found()
    return target


@router.get(
    "/tenants/{tenant_id}/sessions",
    response_model=SupportSessionsResponse,
    dependencies=[Depends(_reject_cross_site)],
)
async def list_tenant_sessions(
    tenant_id: int,
    actor: User = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_session),
) -> SupportSessionsResponse:
    """The target client's sessions, newest first (AC 1) — audited (AC 2).

    A GET that writes is deliberate: the ``audit_log`` row is the condition
    of service ("is audit-logged"), committed before the data leaves.
    """
    target = await _require_client_tenant(session, tenant_id)
    sessions = await capture_sessions_repo.list_for_tenant(
        session, target.tenant_id
    )
    await audit_repo.record(
        session,
        actor_user_id=actor.id,
        tenant_id=target.tenant_id,
        action="support_sessions_list",
    )
    await session.commit()
    logger.info(
        "event=support_view action=sessions_list actor=%s role=%s tenant=%s total=%s",
        actor.id,
        actor.role,
        target.tenant_id,
        len(sessions),
    )
    return SupportSessionsResponse(
        tenant_id=target.tenant_id,
        email=target.email,
        items=[session_to_out(s) for s in sessions],
        total=len(sessions),
    )


@router.get(
    "/tenants/{tenant_id}/sessions/{session_id}",
    response_model=SessionDetailOut,
    dependencies=[Depends(_reject_cross_site)],
)
async def get_tenant_session_detail(
    tenant_id: int,
    session_id: int,
    actor: User = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_session),
) -> SessionDetailOut:
    """One session with the COMPLETE Completa/Filtrada data (AC 1) — the 3.3
    detail shape VERBATIM, served read-only cross-tenant and audited (AC 2).

    Line-by-line mirror of ``get_session_detail`` (``limit=None`` = full
    ascending data) with the path's tenant instead of the actor's. Unknown
    session id, another tenant's session and out-of-int4 id 404 identical
    (``session_not_found`` trio, intact). A GET that writes is deliberate —
    see ``list_tenant_sessions``.
    """
    target = await _require_client_tenant(session, tenant_id)
    if not 0 < session_id <= _PG_INT_MAX:
        raise session_not_found()
    target_session = await capture_sessions_repo.get_for_tenant(
        session, target.tenant_id, session_id
    )
    if target_session is None:
        raise session_not_found()
    responses = await responses_repo.list_full(session, target_session.id, None)
    cc = await responses_repo.list_cc(session, target_session.id, None)
    await audit_repo.record(
        session,
        actor_user_id=actor.id,
        tenant_id=target.tenant_id,
        action="support_session_detail",
        capture_session_id=target_session.id,
    )
    await session.commit()
    logger.info(
        "event=support_view action=session_detail actor=%s role=%s tenant=%s "
        "session=%s",
        actor.id,
        actor.role,
        target.tenant_id,
        target_session.id,
    )
    return SessionDetailOut(
        **session_to_out(target_session).model_dump(),
        responses=[
            SessionResponseRow(
                id=row.id,
                message_id=row.message_id,
                status=row.status,
                text=redact_reply_text(row.text),
                created_at=row.created_at,
            )
            for row in responses
        ],
        cc=[SessionCcRow(id=row.id, text=row.text) for row in cc],
        responses_total=len(responses),
        responses_ok_total=await responses_repo.full_count(
            session, target_session.id, status=responses_repo.STATUS_OK
        ),
        cc_total=len(cc),
    )
