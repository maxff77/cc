"""Cookies router (cookie-vault feature, Phase 1): a tenant stores, lists and
deletes the per-account cookies it will (Phase 2) send before each line on a
cookie-mode gate. Phase 1 is ONLY the vault — no send/rotation/capture changes.

🔒 Security contract (mirrors the captured-CC precedent):
- ``tenant_id`` comes ONLY from the session (``user.tenant_id``) — never from
  the body or path.
- The stored credential is SENSITIVE: kept PLAINTEXT in Postgres (CC precedent,
  encryption deferred to Phase 2) but NEVER echoed to a client. Every response
  carries only the MASKED form (``CookieOut`` has no ``value`` field).
- The value is NEVER logged — not on the happy path, not in the IntegrityError
  dedup mapping, not in any 500. ``IntegrityError`` is caught narrowly and the
  re-fetch keys on the pre-computed ``value_hash``, never on the value.
- Value validation (empty / oversized / unprintable) is raised as
  ``invalid_cookie`` (400) INSIDE the handler — NEVER via a pydantic field
  validator on the value field — so the rejected value can't surface in a
  default 422 body or an access log (no ``RequestValidationError`` handler
  exists in ``main.py``).
- Canonicalization is ``value.strip()`` applied ONCE, before both the
  empty/length check and persistence, so the dedup index keys on the same bytes
  the validator saw.
- Dedup is DB-enforced by ``uq_gate_cookies_tenant_gate_hash`` over
  ``sha256(canonical)``: store-first / catch-``IntegrityError`` is the only
  arbiter (never SELECT-then-INSERT). On a unique violation: rollback FIRST,
  THEN re-fetch the existing row in a clean transaction and return it 200.
- POST resolves/authorizes the gate FIRST (unknown / foreign / retired /
  oversized → identical 404 ``gate_not_found``), THEN evaluates cookie-mode
  (→ 409 only for a gate this tenant can already see). GET and DELETE are
  tenant-scoped by ownership and do NOT re-gate on cookie-mode — a client can
  always list and delete cookies it owns even after the category flag flips off
  or the gate retires (no orphaned, undeletable credentials).
- Read endpoints carry ``Cache-Control: no-store``.

The router owns the transaction (commit/rollback); the repo stays flush-only.
"""

import hashlib
from datetime import datetime

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.base import get_session
from app.db.models import GateCookie, User
from app.db.repos import gate_categories as gate_categories_repo
from app.db.repos import gate_cookies as gate_cookies_repo
from app.db.repos import gates as gates_repo
from app.errors import (
    cookie_conflict_retry,
    cookie_delete_failed,
    cookie_limit_reached,
    cookie_not_found,
    gate_not_cookie_mode,
    gate_not_found,
    invalid_cookie,
)

router = APIRouter(prefix="/api/cookies", tags=["cookies"])

_PG_INT_MAX = 2**31 - 1  # ids are int4; larger binds overflow asyncpg

# A canonical value over this many chars is rejected with 400 ``invalid_cookie``
# (the app guard). The DB dedup key is the sha256 hash, NOT this length — a real
# cookie can exceed the ~2704-byte btree row limit, so the hash is the source of
# truth; this guard just keeps absurd payloads out.
_VALUE_MAX = 2600

# Per-(tenant, gate) cookie cap (Ask-First fork in the spec, resolved to 50).
_COOKIE_CAP = 50

# Newest-first list bound (snapshot-style cap; the cap is well above the
# per-gate store limit so a client always sees all of its own cookies).
_LIST_LIMIT = 100


# --- Schemas (inline, codebase convention) ---------------------------------


class CreateCookieRequest(BaseModel):
    # gate_id is NOT a tenant scope (tenant comes from the session); it is the
    # gate the client files this cookie under. ``value`` is validated in the
    # HANDLER, never here — a pydantic validator would leak the rejected value
    # into a default 422 body.
    gate_id: int
    value: str
    label: str | None = None


class CookieOut(BaseModel):
    """Client-visible cookie — deliberately WITHOUT the raw ``value``.

    ``masked_value`` is the only window onto the credential; the plaintext never
    leaves the database.
    """

    id: int
    label: str | None
    masked_value: str
    status: str
    created_at: datetime


class CookieListResponse(BaseModel):
    """List envelope — matches every other list endpoint ({items, total})."""

    items: list[CookieOut]
    total: int


def _mask(value: str) -> str:
    """Length-safe mask: reveal nothing for short values, a fixed prefix/suffix
    otherwise — never the full secret, and the dot count is fixed so the length
    of a short value never leaks.

    ``len ≤ 8`` → fixed ``••••`` (no characters, no length leak). Else the first
    two and last two characters with a fixed four-dot body. Never raises.
    """
    if len(value) <= 8:
        return "••••"
    return f"{value[:2]}••••{value[-2:]}"


def _cookie_to_out(cookie: GateCookie) -> CookieOut:
    """GateCookie → CookieOut. The raw ``value`` is masked here and NEVER
    serialized as-is — the single mapper guarantees no endpoint leaks it."""
    return CookieOut(
        id=cookie.id,
        label=cookie.label,
        masked_value=_mask(cookie.value),
        status=cookie.status,
        created_at=cookie.created_at,
    )


async def _require_cookie_mode_gate(session: AsyncSession, gate_id: int) -> None:
    """POST guard: resolve/authorize the gate FIRST (identical 404 for
    unknown / foreign-to-the-catalog / retired / oversized), THEN require
    cookie mode (→ 409 only for a gate the tenant can already see).

    The gate catalog is GLOBAL (every tenant sees the same active gates), so
    there is no per-tenant gate scoping here — the tenant scope lives on the
    cookie rows themselves. The id is never logged.
    """
    if not 0 < gate_id <= _PG_INT_MAX:
        raise gate_not_found()
    gate = await gates_repo.get_by_id(session, gate_id)
    if gate is None or gate.deleted_at is not None:
        raise gate_not_found()
    # cookie_mode lives on the gate's category. Loaded explicitly — the
    # category relation is not eager-loaded here and an async lazy-load would
    # raise MissingGreenlet. A vanished category (race) is treated as not
    # cookie-mode.
    category = await gate_categories_repo.get_by_id(session, gate.category_id)
    if category is None or not category.cookie_mode:
        raise gate_not_cookie_mode()


# --- Routes ------------------------------------------------------------------


@router.post("", response_model=CookieOut, status_code=201)
async def store_cookie(
    body: CreateCookieRequest,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CookieOut:
    """Store a cookie for a cookie-mode gate (idempotent per canonical value).

    Order is load-bearing: canonicalize+validate the value in-handler → resolve
    the gate (404 before cookie-mode) → cookie-mode check (409) → STORE-FIRST
    insert → cap check on the flushed count (409, only a genuinely-new DISTINCT
    value can hit it) → commit. A unique violation means the same canonical value
    already exists for this ``(tenant, gate)``: roll back, re-fetch and return the
    existing row 200 (same id) — never a 500, value never logged. Because the cap
    is checked AFTER the store-first insert, a duplicate (which raises on flush
    before the cap) dedups to 200 even when the tenant is AT the cap. A fresh
    insert keeps the route's 201.
    """
    tenant_id = user.tenant_id
    # Canonicalize ONCE — the same bytes feed validation AND the hash, so a
    # pasted trailing newline dedups instead of duplicating.
    canonical = body.value.strip()
    # In-handler validation (NOT a pydantic validator): the rejected value
    # never reaches a default 422 body or an access log.
    if not canonical or len(canonical) > _VALUE_MAX:
        raise invalid_cookie()
    if any(not ch.isprintable() for ch in canonical):
        raise invalid_cookie()

    # Resolve/authorize the gate (404) BEFORE the cookie-mode check (409).
    await _require_cookie_mode_gate(session, body.gate_id)

    value_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    try:
        # Store-first (the dedup arbiter is the DB unique index, never a
        # SELECT-then-INSERT): a duplicate canonical value raises IntegrityError
        # on flush below, BEFORE the cap is consulted.
        cookie = await gate_cookies_repo.create(
            session,
            tenant_id=tenant_id,
            gate_id=body.gate_id,
            value=canonical,
            value_hash=value_hash,
            label=body.label,
        )
        # Cap AFTER the store-first insert: the flushed row is counted, so the
        # cap gates only a genuinely-new DISTINCT value. An idempotent re-POST
        # never reaches here (its flush dedups to 200 below), so it is exempt
        # even AT the cap — matching the frozen matrix's "Nth+1 distinct value".
        if (
            await gate_cookies_repo.count_for(session, tenant_id, body.gate_id)
            > _COOKIE_CAP
        ):
            await session.rollback()
            raise cookie_limit_reached()
        await session.commit()
    except IntegrityError:
        # The canonical value already exists for this (tenant, gate). The txn is
        # aborted by the violation — roll back FIRST, then re-fetch in a clean
        # transaction and return the existing row 200 (same id). The value is
        # NEVER interpolated into any message; the re-fetch keys on the hash.
        await session.rollback()
        existing = await gate_cookies_repo.get_by_hash(
            session, tenant_id, body.gate_id, value_hash
        )
        if existing is None:
            # The conflicting row vanished between the violation and the
            # re-fetch (a concurrent delete) — surface a mapped, retryable
            # conflict (NOT a bare re-raise → unmapped 500), keeping the
            # {code,message} contract; the value is never logged.
            raise cookie_conflict_retry()
        # Idempotent re-POST: override the route's 201 default to 200 (the
        # frozen contract: a duplicate value returns the existing row, 200).
        response.status_code = 200
        return _cookie_to_out(existing)
    return _cookie_to_out(cookie)


@router.get("", response_model=CookieListResponse)
async def list_cookies(
    gate_id: int,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CookieListResponse:
    """List the tenant's cookies for a gate, newest first, masked.

    Tenant-scoped and NOT re-gated on cookie-mode: a client always sees the
    cookies it owns, even after the gate leaves cookie-mode or retires. A
    foreign/unknown/oversized ``gate_id`` simply returns an empty list (the
    tenant scope makes the lookup miss) — identical to "no cookies", so no
    existence leaks. ``Cache-Control: no-store`` — the body carries (masked)
    credential metadata.
    """
    response.headers["Cache-Control"] = "no-store"
    if not 0 < gate_id <= _PG_INT_MAX:
        return CookieListResponse(items=[], total=0)
    cookies = await gate_cookies_repo.list_by_tenant_gate(
        session, user.tenant_id, gate_id, limit=_LIST_LIMIT
    )
    items = [_cookie_to_out(c) for c in cookies]
    return CookieListResponse(items=items, total=len(items))


@router.delete("/{cookie_id}", status_code=204)
async def delete_cookie(
    cookie_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Hard-delete one of the tenant's cookies.

    Tenant-scoped and NOT re-gated on cookie-mode — works even after the gate
    leaves cookie-mode or retires (no orphaned, undeletable credentials).
    Unknown / foreign-tenant / oversized id all 404 IDENTICALLY (the repo's
    tenant predicate makes a foreign/unknown id a clean no-op) — existence is
    never leaked and the id is never logged.
    """
    if not 0 < cookie_id <= _PG_INT_MAX:
        raise cookie_not_found()
    try:
        deleted = await gate_cookies_repo.delete_by_id(
            session, user.tenant_id, cookie_id
        )
        if not deleted:
            raise cookie_not_found()
        await session.commit()
    except IntegrityError:
        # Defense-in-depth: ``batch_lines.failed_cookie_id`` is ON DELETE SET
        # NULL, so a referenced cookie deletes cleanly — but never let a bare
        # IntegrityError surface as an unmapped 500 (the original "error
        # inesperado"). Roll back the aborted txn and map it. The id is never
        # logged.
        await session.rollback()
        # ``from None`` — translate the DB error into the domain error without
        # chaining the raw IntegrityError (value-free; nothing from the cookie
        # leaks into a traceback).
        raise cookie_delete_failed() from None
