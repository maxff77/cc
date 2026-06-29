"""Public (no-auth) router for the sales landing (public-landing-page feature).

`/api/public/*` is the ONLY router that needs the DB but is deliberately
NOT auth-gated — no ``Depends(get_current_user)`` anywhere. It feeds the public
landing's live pricing + gates sections to first-time, logged-out visitors.

MARKETING-SAFE SHAPE (hard boundary): every response here is reachable without a
session, so it exposes only conversion copy:

- ``/gates`` -> category ``name`` + gate ``name`` ONLY. NEVER the real ``value``
  (the engine command), ``display_value``, or ``credit_cost``.
- ``/plans`` -> ``name, price_usd, duration_days, max_lines_per_batch, credits,
  credits_unlimited, is_default`` for ACTIVE plans only. NEVER ``antispam_seconds``
  (internal pacing) or any per-tenant data.

Unlimited credits is a DISPLAY convention, no migration: a plan whose ``credits``
reaches the threshold below renders an ∞ glyph instead of a number. The engine
still decrements; the grant is just effectively inexhaustible.
"""

from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import Plan
from app.db.repos import gates as gates_repo
from app.db.repos import plans as plans_repo
from app.services import support_contacts as support_contacts_service

# A plan with at least this many credits is shown as "unlimited" (∞). This is a
# pure display convention — no DB column, no engine change (the owner sets the
# premium tier's ``credits`` at/above this from /admin/plans).
UNLIMITED_CREDITS_THRESHOLD = 99_999

router = APIRouter(prefix="/api/public", tags=["public"])


class PublicGateCategoryOut(BaseModel):
    """A category and the (active) gate names under it — names ONLY."""

    name: str
    gates: list[str]


class PublicGatesResponse(BaseModel):
    categories: list[PublicGateCategoryOut]
    total: int


class PublicPlanOut(BaseModel):
    """Marketing-safe plan shape — NO ``antispam_seconds`` or per-tenant data."""

    name: str
    price_usd: Decimal
    duration_days: int
    max_lines_per_batch: int
    credits: int
    # Display convention: ``credits >= UNLIMITED_CREDITS_THRESHOLD`` -> ∞ on the
    # card instead of a number. No DB column.
    credits_unlimited: bool
    is_default: bool


class PublicPlansResponse(BaseModel):
    items: list[PublicPlanOut]
    total: int


class SupportContactOut(BaseModel):
    """One Telegram support handle (canonical: no '@', no t.me prefix)."""

    handle: str


class SupportContactsResponse(BaseModel):
    """Ordered support handles — index 0 is the primary contact. Shared shape
    for the public read here and the owner read/write in ``api/admin``."""

    contacts: list[SupportContactOut]


@router.get("/gates", response_model=PublicGatesResponse)
async def public_gates(
    session: AsyncSession = Depends(get_session),
) -> PublicGatesResponse:
    """Active gates grouped by category, names ONLY (no auth).

    Reuses ``gates_repo.list_active`` (eager-loads ``Gate.category``). The real
    ``value``/``display_value``/``credit_cost`` are deliberately never exposed —
    this endpoint is public.
    """
    gates = await gates_repo.list_active(session)
    grouped: dict[str, list[str]] = {}
    for gate in gates:
        grouped.setdefault(gate.category.name, []).append(gate.name)
    categories = [
        PublicGateCategoryOut(name=category_name, gates=sorted(names))
        for category_name, names in sorted(grouped.items())
    ]
    return PublicGatesResponse(categories=categories, total=len(gates))


def _plan_to_public_out(plan: Plan) -> PublicPlanOut:
    return PublicPlanOut(
        name=plan.name,
        price_usd=plan.price_usd,
        duration_days=plan.duration_days,
        max_lines_per_batch=plan.max_lines_per_batch,
        credits=plan.credits,
        credits_unlimited=plan.credits >= UNLIMITED_CREDITS_THRESHOLD,
        is_default=plan.is_default,
    )


@router.get("/plans", response_model=PublicPlansResponse)
async def public_plans(
    session: AsyncSession = Depends(get_session),
) -> PublicPlansResponse:
    """Active pricing plans, marketing-safe fields only (no auth).

    Reuses ``plans_repo.list_active`` (the active-only catalog read). NEVER
    exposes ``antispam_seconds`` or any per-tenant data.
    """
    plans = await plans_repo.list_active(session)
    return PublicPlansResponse(
        items=[_plan_to_public_out(p) for p in plans], total=len(plans)
    )


@router.get("/support-contacts", response_model=SupportContactsResponse)
async def public_support_contacts(
    session: AsyncSession = Depends(get_session),
) -> SupportContactsResponse:
    """The owner-managed Telegram support handles (no auth).

    Marketing-safe: these handles are already rendered in the logged-out
    ``/login`` HTML. Falls back to the pre-feature defaults when unset (the
    service owns that). Feeds the client ``useSupportContacts`` hook.
    """
    handles = await support_contacts_service.get_handles(session)
    return SupportContactsResponse(
        contacts=[SupportContactOut(handle=h) for h in handles]
    )
