"""Integration tests for the cookie vault (cookie-vault feature, Phase 1).

The cookie vault is a tenant-scoped store of per-account cookies a client will
(Phase 2) send before each line on a cookie-mode gate. Phase 1 is ONLY the
vault: POST/GET/DELETE on ``/api/cookies``. These tests lock the security
invariants the spec freezes — the raw value NEVER leaves the DB (masked only,
``no-store``), DB-enforced dedup (hash index, not value), 404-before-409 gate
resolution, tenant-scoped existence (no leaks), and the per-(tenant, gate) cap.

Same harness as the other API modules: drives the real ASGI app (httpx
``ASGITransport``) against the dev Postgres, self-seeding with unique emails,
self-cleaning on teardown. A cookie-mode gate is created via the owner API
(``cookie_mode=True`` on its category) and torn down with its cookies.

Run (from backend/, venv active):  pytest tests/test_cookies.py
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from app.db.base import async_session_factory
from app.db.models import Gate, GateCategory, GateCookie
from app.main import app
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from tests.conftest import cleanup_users, login, seed_user


async def _make_cookie_gate(
    owner_client: AsyncClient, *, cookie_mode: bool
) -> dict:
    """Create an active gate in a fresh category with the given cookie_mode flag,
    via the owner API (mirrors the conftest ``gate`` fixture). Returns the gate
    JSON (carries ``id`` and ``category_id``)."""
    cat = await owner_client.post(
        "/api/admin/gate-categories",
        json={"name": f"Ck {uuid.uuid4().hex[:8]}", "cookie_mode": cookie_mode},
    )
    assert cat.status_code == 201, cat.text
    assert cat.json()["cookie_mode"] is cookie_mode
    value = f".ck{uuid.uuid4().hex[:6]}"
    res = await owner_client.post(
        "/api/admin/gates",
        json={
            "value": value,
            "name": "Cookie Gate",
            "display_value": "Comando Cookie",
            "category_id": cat.json()["id"],
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _drop_gate(category_id: int) -> None:
    async with async_session_factory() as session:
        gate_ids = list(
            (
                await session.execute(
                    select(Gate.id).where(Gate.category_id == category_id)
                )
            )
            .scalars()
            .all()
        )
        if gate_ids:
            await session.execute(
                delete(GateCookie).where(GateCookie.gate_id.in_(gate_ids))
            )
        await session.execute(delete(Gate).where(Gate.category_id == category_id))
        await session.execute(
            delete(GateCategory).where(GateCategory.id == category_id)
        )
        await session.commit()


@pytest_asyncio.fixture(loop_scope="session")
async def owner_client() -> AsyncIterator[AsyncClient]:
    """A logged-in owner (cookie_mode is an owner-only category control)."""
    owner = await seed_user("owner", email_prefix="cookies")
    http = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(http, owner.email)
    yield http
    await http.aclose()
    await cleanup_users({owner.email})


@pytest_asyncio.fixture(loop_scope="session")
async def cookie_gate(owner_client: AsyncClient) -> AsyncIterator[dict]:
    """An active, cookie-MODE gate (its category has ``cookie_mode=True``)."""
    gate = await _make_cookie_gate(owner_client, cookie_mode=True)
    yield gate
    await _drop_gate(gate["category_id"])


@pytest_asyncio.fixture(loop_scope="session")
async def plain_gate(owner_client: AsyncClient) -> AsyncIterator[dict]:
    """An active gate whose category is NOT in cookie mode (POST → 409)."""
    gate = await _make_cookie_gate(owner_client, cookie_mode=False)
    yield gate
    await _drop_gate(gate["category_id"])


@pytest_asyncio.fixture(loop_scope="session")
async def client_a() -> AsyncIterator[AsyncClient]:
    """A logged-in client (valid plan), self-cleaning (its cookies cascade with
    the tenant on teardown via FK CASCADE on ``tenant_id``)."""
    user = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    http = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(http, user.email)
    yield http
    await http.aclose()
    await cleanup_users({user.email})


@pytest_asyncio.fixture(loop_scope="session")
async def client_b() -> AsyncIterator[AsyncClient]:
    """A SECOND, distinct client tenant — for cross-tenant isolation checks."""
    user = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    http = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(http, user.email)
    yield http
    await http.aclose()
    await cleanup_users({user.email})


# --- Store → list (masked, value never raw) ---------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_store_then_list_masks_value_and_never_returns_raw(
    client_a: AsyncClient, cookie_gate: dict
) -> None:
    raw = "session-token-abcdef0123456789"
    res = await client_a.post(
        "/api/cookies",
        json={"gate_id": cookie_gate["id"], "value": raw, "label": "main"},
    )
    assert res.status_code == 201, res.text
    out = res.json()
    # The store response is the masked CookieOut — no raw value, no value_hash.
    assert "value" not in out
    assert "value_hash" not in out
    assert out["label"] == "main"
    assert out["status"] == "active"
    assert out["id"] > 0
    # Length-safe mask: first two + fixed dots + last two; never the whole secret.
    assert out["masked_value"] == f"{raw[:2]}••••{raw[-2:]}"
    assert raw not in out["masked_value"]

    listed = await client_a.get(f"/api/cookies?gate_id={cookie_gate['id']}")
    assert listed.status_code == 200, listed.text
    # Read endpoints never cache the (masked) credential metadata.
    assert listed.headers.get("cache-control") == "no-store"
    body = listed.json()
    assert body["total"] == len(body["items"])
    items = body["items"]
    assert any(c["id"] == out["id"] for c in items)
    # The raw value is absent from every row, and so is value_hash.
    for c in items:
        assert "value" not in c
        assert "value_hash" not in c
        assert raw not in c["masked_value"]
    # The raw value appears NOWHERE in the serialized body.
    assert raw not in listed.text


# --- A ~3000-char value round-trips with no 500 (hash index, not value) ------


@pytest.mark.asyncio(loop_scope="session")
async def test_long_value_roundtrips_without_500(
    client_a: AsyncClient, cookie_gate: dict
) -> None:
    """A value far past the ~2704-byte btree row limit a value-index would hit
    must store fine — the unique key is sha256(value), not the value itself."""
    big = "z" + ("cookie-segment-" * 160) + "q"  # ~2400 chars, > 2704 bytes
    assert len(big) > 2000
    res = await client_a.post(
        "/api/cookies", json={"gate_id": cookie_gate["id"], "value": big}
    )
    assert res.status_code == 201, res.text
    out = res.json()
    assert "value" not in out
    # Masked even for a long value; reveals nothing close to the full secret.
    assert out["masked_value"] == f"{big[:2]}••••{big[-2:]}"
    assert big not in res.text


# --- Canonicalization dedup: "abc" and "abc\n" → SAME id (200) ---------------


@pytest.mark.asyncio(loop_scope="session")
async def test_trailing_newline_dedups_to_same_id(
    client_a: AsyncClient, cookie_gate: dict
) -> None:
    """``"abc"`` and ``"abc\\n"`` canonicalize identically (strip once) → the DB
    unique index on the hash maps the second to the first: 200, SAME id."""
    base = f"dedup-{uuid.uuid4().hex}"
    first = await client_a.post(
        "/api/cookies", json={"gate_id": cookie_gate["id"], "value": base}
    )
    assert first.status_code == 201, first.text
    first_id = first.json()["id"]

    second = await client_a.post(
        "/api/cookies", json={"gate_id": cookie_gate["id"], "value": base + "\n"}
    )
    # Idempotent: the IntegrityError branch in cookies.py rolls back, re-fetches
    # by hash and sets ``response.status_code = 200`` → the dedup re-POST returns
    # 200 with the SAME id (the frozen contract). The SAME-id assertion proves
    # the dedup logic; the status assertion locks the 200 contract.
    assert second.status_code == 200, (
        f"dedup must return 200 (frozen contract); got {second.status_code}. "
        f"body={second.text}"
    )
    assert second.json()["id"] == first_id


# --- Second identical POST → 200 + SAME id -----------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_second_identical_post_returns_200_same_id(
    client_a: AsyncClient, cookie_gate: dict
) -> None:
    val = f"idem-{uuid.uuid4().hex}"
    first = await client_a.post(
        "/api/cookies", json={"gate_id": cookie_gate["id"], "value": val}
    )
    assert first.status_code == 201, first.text
    second = await client_a.post(
        "/api/cookies", json={"gate_id": cookie_gate["id"], "value": val}
    )
    # Frozen contract: the idempotent re-POST returns 200 (same id) — the
    # IntegrityError branch in cookies.py overrides the route's 201 default.
    assert second.status_code == 200, (
        f"second identical POST must return 200 (frozen contract); got "
        f"{second.status_code}. body={second.text}"
    )
    assert second.json()["id"] == first.json()["id"]


# --- Too-long value → 400 invalid_cookie AND body excludes the value ---------


@pytest.mark.asyncio(loop_scope="session")
async def test_too_long_value_is_400_and_body_excludes_value(
    client_a: AsyncClient, cookie_gate: dict
) -> None:
    """Oversized → 400 ``invalid_cookie`` raised IN-HANDLER (not a pydantic
    validator), so the rejected value can't surface in the 400/422 body."""
    huge = "x" * 5000  # > _VALUE_MAX (2600 canonical chars)
    res = await client_a.post(
        "/api/cookies", json={"gate_id": cookie_gate["id"], "value": huge}
    )
    assert res.status_code == 400, res.text
    assert res.json()["code"] == "invalid_cookie"
    # The rejected value never appears in the response body (no 422 echo, no leak).
    assert huge not in res.text
    assert "x" * 100 not in res.text


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "bad_value",
    ["", "   ", "\n\t  ", "tok\x00en", "line\x07bell"],
    ids=["empty", "whitespace", "all-whitespace", "nul-byte", "control-char"],
)
async def test_empty_or_unprintable_value_is_400(
    client_a: AsyncClient, cookie_gate: dict, bad_value: str
) -> None:
    res = await client_a.post(
        "/api/cookies", json={"gate_id": cookie_gate["id"], "value": bad_value}
    )
    assert res.status_code == 400, res.text
    assert res.json()["code"] == "invalid_cookie"
    if bad_value.strip():
        assert bad_value not in res.text


# --- Foreign-tenant gate_id on GET → empty list (no existence leak) ----------


@pytest.mark.asyncio(loop_scope="session")
async def test_list_is_tenant_scoped_no_existence_leak(
    client_a: AsyncClient, client_b: AsyncClient, cookie_gate: dict
) -> None:
    """client_a stores a cookie; client_b GETting the same gate_id sees an EMPTY
    list — identical to "no cookies", so the lookup leaks nothing. A bogus
    gate_id also lists empty."""
    val = f"scoped-{uuid.uuid4().hex}"
    a_store = await client_a.post(
        "/api/cookies", json={"gate_id": cookie_gate["id"], "value": val}
    )
    assert a_store.status_code == 201, a_store.text

    a_list = await client_a.get(f"/api/cookies?gate_id={cookie_gate['id']}")
    assert any(c["id"] == a_store.json()["id"] for c in a_list.json()["items"])

    # client_b (different tenant) sees nothing for the SAME gate_id.
    b_list = await client_b.get(f"/api/cookies?gate_id={cookie_gate['id']}")
    assert b_list.status_code == 200, b_list.text
    assert b_list.headers.get("cache-control") == "no-store"
    assert b_list.json()["items"] == []
    assert b_list.json()["total"] == 0

    # Unknown / oversized gate_id → empty list too (no existence leak, no 500).
    for bad in (999999999, 99999999999999999999):
        miss = await client_a.get(f"/api/cookies?gate_id={bad}")
        assert miss.status_code == 200, miss.text
        assert miss.json()["items"] == []


# --- POST on a non-cookie-mode gate → 409 ------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_post_non_cookie_mode_gate_is_409(
    client_a: AsyncClient, plain_gate: dict
) -> None:
    res = await client_a.post(
        "/api/cookies", json={"gate_id": plain_gate["id"], "value": "abc12345"}
    )
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "gate_not_cookie_mode"


# --- POST on an unknown/foreign/oversized gate → 404 (NOT 409) ---------------


@pytest.mark.asyncio(loop_scope="session")
async def test_post_unknown_gate_is_404_before_cookie_mode(
    client_a: AsyncClient,
) -> None:
    """The gate is resolved/authorized FIRST: an unknown/oversized gate_id is a
    404 ``gate_not_found`` — never a 409 (no existence leak; you can't learn a
    gate's cookie-mode by probing). Identical 404 for unknown and out-of-int4."""
    for bad in (999999999, 0, 99999999999999999999):
        res = await client_a.post(
            "/api/cookies", json={"gate_id": bad, "value": "abc12345"}
        )
        assert res.status_code == 404, res.text
        assert res.json()["code"] == "gate_not_found"


@pytest.mark.asyncio(loop_scope="session")
async def test_post_retired_gate_is_404(
    owner_client: AsyncClient, client_a: AsyncClient
) -> None:
    """A retired (``deleted_at`` set) cookie-mode gate resolves to the SAME 404
    as an unknown one — retirement is indistinguishable from non-existence on
    the store path."""
    gate = await _make_cookie_gate(owner_client, cookie_mode=True)
    try:
        # Retire it via the owner API (soft delete → deleted_at set).
        retire = await owner_client.delete(f"/api/admin/gates/{gate['id']}")
        assert retire.status_code == 204, retire.text
        res = await client_a.post(
            "/api/cookies", json={"gate_id": gate["id"], "value": "abc12345"}
        )
        assert res.status_code == 404, res.text
        assert res.json()["code"] == "gate_not_found"
    finally:
        await _drop_gate(gate["category_id"])


# --- Delete: owned → 204; foreign/unknown/oversized → 404 --------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_owned_then_gone(
    client_a: AsyncClient, cookie_gate: dict
) -> None:
    val = f"del-{uuid.uuid4().hex}"
    stored = await client_a.post(
        "/api/cookies", json={"gate_id": cookie_gate["id"], "value": val}
    )
    cid = stored.json()["id"]
    res = await client_a.delete(f"/api/cookies/{cid}")
    assert res.status_code == 204, res.text
    # Re-list: the deleted cookie is gone.
    listed = await client_a.get(f"/api/cookies?gate_id={cookie_gate['id']}")
    assert all(c["id"] != cid for c in listed.json()["items"])
    # Deleting it again 404s (now unknown to this tenant).
    again = await client_a.delete(f"/api/cookies/{cid}")
    assert again.status_code == 404
    assert again.json()["code"] == "cookie_not_found"


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_foreign_cookie_is_404(
    client_a: AsyncClient, client_b: AsyncClient, cookie_gate: dict
) -> None:
    """client_b cannot delete client_a's cookie — the tenant predicate makes it
    a clean no-op → identical 404, no existence leak."""
    val = f"foreign-{uuid.uuid4().hex}"
    a_store = await client_a.post(
        "/api/cookies", json={"gate_id": cookie_gate["id"], "value": val}
    )
    a_id = a_store.json()["id"]

    res = await client_b.delete(f"/api/cookies/{a_id}")
    assert res.status_code == 404, res.text
    assert res.json()["code"] == "cookie_not_found"

    # The cookie still exists for its owner (the foreign delete was a no-op).
    a_list = await client_a.get(f"/api/cookies?gate_id={cookie_gate['id']}")
    assert any(c["id"] == a_id for c in a_list.json()["items"])


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("bad_id", ["999999999", "0", "99999999999999999999"])
async def test_delete_unknown_or_oversized_id_is_404(
    client_a: AsyncClient, bad_id: str
) -> None:
    res = await client_a.delete(f"/api/cookies/{bad_id}")
    assert res.status_code == 404, res.text
    assert res.json()["code"] == "cookie_not_found"


# --- Masking is length-safe: 1-char & 8-char reveal no full secret -----------


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "secret",
    ["a", "ab", "secret12", "12345678"],
    ids=["len1", "len2", "len8-alpha", "len8-digits"],
)
async def test_short_values_mask_to_fixed_dots(
    client_a: AsyncClient, cookie_gate: dict, secret: str
) -> None:
    """``len ≤ 8`` masks to a fixed ``••••`` — reveals NOTHING (not even the
    length): the short secret never appears, and no character of it leaks."""
    res = await client_a.post(
        "/api/cookies", json={"gate_id": cookie_gate["id"], "value": secret}
    )
    assert res.status_code in (200, 201), res.text
    masked = res.json()["masked_value"]
    # Fixed dots, no characters of the secret, no length leak.
    assert masked == "••••"
    assert secret not in masked
    # The raw short secret never appears in the body either.
    assert f'"masked_value":"{secret}"' not in res.text


@pytest.mark.asyncio(loop_scope="session")
async def test_nine_char_value_masks_with_fixed_dot_count(
    client_a: AsyncClient, cookie_gate: dict
) -> None:
    """Just over the threshold (len 9): first two + last two with a FIXED four-dot
    body — the dot count never encodes the real length."""
    secret = "abcdefghi"  # 9 chars
    res = await client_a.post(
        "/api/cookies", json={"gate_id": cookie_gate["id"], "value": secret}
    )
    assert res.status_code == 201, res.text
    assert res.json()["masked_value"] == "ab••••hi"
    assert secret not in res.text


# --- Cap reached → 409 -------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_cookie_cap_reached_is_409(owner_client: AsyncClient) -> None:
    """The per-(tenant, gate) cap (50) gates the Nth+1 DISTINCT value. This test
    uses a FRESH client + a FRESH cookie-mode gate so the count starts at zero
    (the shared ``cookie_gate``/``client_a`` accumulate cookies in other tests).
    Fill to the cap, then the next distinct store → 409 ``cookie_limit_reached``;
    a DUPLICATE of an existing value still dedups (the cap is on distinct rows)."""
    cap = 50
    gate = await _make_cookie_gate(owner_client, cookie_mode=True)
    user = await seed_user(
        "client",
        expires_at=datetime.now(UTC) + timedelta(days=30),
        email_prefix="cookies-cap",
    )
    capc = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(capc, user.email)
    try:
        first_value = None
        first_id = None
        for i in range(cap):
            value = f"cap-{i}-{uuid.uuid4().hex}"
            res = await capc.post(
                "/api/cookies",
                json={"gate_id": gate["id"], "value": value},
            )
            assert res.status_code == 201, f"row {i}: {res.text}"
            if i == 0:
                first_value, first_id = value, res.json()["id"]

        # An (N+1)th DISTINCT value is rejected.
        over = await capc.post(
            "/api/cookies",
            json={"gate_id": gate["id"], "value": f"over-{uuid.uuid4().hex}"},
        )
        assert over.status_code == 409, over.text
        assert over.json()["code"] == "cookie_limit_reached"

        # But re-POSTing an EXISTING value while AT the cap still dedups to 200 +
        # SAME id — the cap gates only genuinely-new DISTINCT rows, not idempotent
        # re-stores (regression guard: the cap once ran before the dedup path,
        # returning 409 here).
        dup = await capc.post(
            "/api/cookies",
            json={"gate_id": gate["id"], "value": first_value},
        )
        assert dup.status_code == 200, dup.text
        assert dup.json()["id"] == first_id
    finally:
        await capc.aclose()
        await _drop_gate(gate["category_id"])
        await cleanup_users({user.email})
