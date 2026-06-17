"""Gift-key routers (gift-keys feature).

Admin/owner MINT keys (``/api/admin/keys``); a client CLAIMS one
(``/api/keys/claim``). A key carries only ``days`` + a snapshot of the
owner-designated default plan — admins never choose the tier (anti-abuse).
Claiming adds days (never credits) and assigns the basic plan only to a
plan-less client.

The claim route uses ``get_current_user_allow_expired`` so an expired /
just-registered client (exactly the people who redeem) can reach it despite the
``plan_expired`` gate; it still guards ``role == 'client'`` and keeps
``tenant_id`` from the session. Generate/list/revoke are admin-or-owner.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_allow_expired, require_role
from app.db.base import get_session
from app.db.models import GiftKey, User
from app.db.repos import gift_keys as gift_keys_repo
from app.errors import forbidden, invalid_key_days, key_not_found
from app.services import gift_keys as gift_keys_service

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/api/admin/keys", tags=["gift-keys"])
client_router = APIRouter(prefix="/api/keys", tags=["gift-keys"])

require_admin_or_owner = require_role("admin", "owner")

# ~100-year ceiling, same as plan renewal (admin.PLAN_DAYS_MAX); guards a
# fat-finger value from overflowing the datetime math at claim.
KEY_DAYS_MAX = 36500
_PG_INT_MAX = 2**31 - 1  # gift_keys.id is int4


# --- Schemas -------------------------------------------------------------


class GenerateKeyRequest(BaseModel):
    # The ONLY admin-chosen field — the tier is fixed to the default plan.
    days: int


class GiftKeyOut(BaseModel):
    id: int
    code: str
    days: int
    plan_id: int
    plan_name: str
    status: str
    created_by_email: str | None
    claimed_by_email: str | None
    created_at: datetime
    claimed_at: datetime | None


class GiftKeyListResponse(BaseModel):
    items: list[GiftKeyOut]


class ClaimKeyRequest(BaseModel):
    code: str


class ClaimKeyResult(BaseModel):
    # The cockpit / expired page refresh /me off this; expires_at confirms the
    # extension and plan_id reflects a freshly-assigned basic tier (or the kept
    # existing one).
    expires_at: datetime | None
    plan_id: int | None
    days_added: int


def _key_to_out(
    key: GiftKey,
    *,
    plan_name: str,
    created_by_email: str | None,
    claimed_by_email: str | None = None,
) -> GiftKeyOut:
    return GiftKeyOut(
        id=key.id,
        code=key.code,
        days=key.days,
        plan_id=key.plan_id,
        plan_name=plan_name,
        status=key.status,
        created_by_email=created_by_email,
        claimed_by_email=claimed_by_email,
        created_at=key.created_at,
        claimed_at=key.claimed_at,
    )


# --- Admin: mint / log / revoke ------------------------------------------


@admin_router.post("", response_model=GiftKeyOut, status_code=201)
async def generate_key(
    body: GenerateKeyRequest,
    actor: User = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_session),
) -> GiftKeyOut:
    """Mint a single-use key (days only; tier = the default plan).

    Bad days → 400 ``invalid_key_days``; no active default plan configured →
    409 ``no_default_plan``. The response carries the code so the admin can copy
    it immediately.
    """
    if not 1 <= body.days <= KEY_DAYS_MAX:
        raise invalid_key_days()
    key, plan = await gift_keys_service.generate(
        session, days=body.days, created_by_user_id=actor.id
    )
    await session.commit()
    return _key_to_out(key, plan_name=plan.name, created_by_email=actor.email)


@admin_router.get("", response_model=GiftKeyListResponse)
async def list_keys(
    actor: User = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_session),
) -> GiftKeyListResponse:
    """The keys log (newest first): who minted, who claimed, status — the
    owner's admin-abuse audit view."""
    rows = await gift_keys_repo.list_all(session)
    return GiftKeyListResponse(
        items=[
            _key_to_out(
                r.GiftKey,
                plan_name=r.plan_name,
                created_by_email=r.created_by_email,
                claimed_by_email=r.claimed_by_email,
            )
            for r in rows
        ]
    )


@admin_router.post("/{key_id}/revoke", status_code=204)
async def revoke_key(
    key_id: int,
    actor: User = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Revoke an UNCLAIMED key. Unknown → 404 ``key_not_found``; already
    claimed → 409 ``key_already_claimed`` (the days are already granted)."""
    if not 0 < key_id <= _PG_INT_MAX:
        raise key_not_found()
    await gift_keys_service.revoke(session, key_id, revoked_by_user_id=actor.id)
    await session.commit()


# --- Client: claim -------------------------------------------------------


@client_router.post("/claim", response_model=ClaimKeyResult)
async def claim_key(
    body: ClaimKeyRequest,
    user: User = Depends(get_current_user_allow_expired),
    session: AsyncSession = Depends(get_session),
) -> ClaimKeyResult:
    """Redeem a key for the logged-in client: +days, basic plan if plan-less,
    no credits. Works for an EXPIRED client (the dep bypasses the expiry gate).

    Unknown code → 404 ``key_not_found``; revoked → 409 ``key_revoked``; already
    claimed → 409 ``key_already_claimed``; non-client → 403 ``forbidden``.
    """
    if user.role != "client":
        raise forbidden()
    updated, days_added = await gift_keys_service.claim(
        session, user_id=user.id, code=body.code.strip()
    )
    await session.commit()
    return ClaimKeyResult(
        expires_at=updated.expires_at,
        plan_id=updated.plan_id,
        days_added=days_added,
    )
