"""Integration tests for the credential vault (``/api/credentials``).

Auth is the single shared API key (``X-Api-Key``); there is no session here. All
rows live under one dedicated vault tenant, so these tests share state — the
``vault`` fixture wipes the vault before AND after each test to stay isolated.

Locks the invariants:
- a missing/wrong key is 401; with no key configured the vault is 503.
- store → list/random echo the password back (owner choice); reads are no-store.
- email format is validated in-handler → bad address is 400 ``invalid_credential``.
- delete by id and delete by email both 404 IDENTICALLY on no match.

Run (from backend/, venv active):  pytest tests/test_credentials.py
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from app.config import settings
from app.main import app
from httpx import ASGITransport, AsyncClient

_PG_INT_MAX = 2**31 - 1
_TEST_KEY = "test-vault-key-abc123"


async def _wipe(http: AsyncClient) -> None:
    listed = await http.get("/api/credentials")
    for r in listed.json():
        await http.delete(f"/api/credentials/{r['id']}")


@pytest_asyncio.fixture(loop_scope="session")
async def vault() -> AsyncIterator[AsyncClient]:
    """A client carrying a valid X-Api-Key, with the vault wiped clean."""
    original = settings.credentials_api_key
    settings.credentials_api_key = _TEST_KEY
    http = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Api-Key": _TEST_KEY},
    )
    await _wipe(http)
    yield http
    await _wipe(http)
    await http.aclose()
    settings.credentials_api_key = original


# --- Auth -------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_or_wrong_key_is_401(vault: AsyncClient) -> None:
    no_key = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    res = await no_key.get("/api/credentials")
    assert res.status_code == 401, res.text
    assert res.json()["code"] == "invalid_api_key"

    res = await no_key.get(
        "/api/credentials", headers={"X-Api-Key": "wrong-key"}
    )
    assert res.status_code == 401, res.text
    await no_key.aclose()


@pytest.mark.asyncio(loop_scope="session")
async def test_unconfigured_key_is_503() -> None:
    original = settings.credentials_api_key
    settings.credentials_api_key = None
    http = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Api-Key": "anything"},
    )
    res = await http.get("/api/credentials")
    assert res.status_code == 503, res.text
    assert res.json()["code"] == "api_key_not_configured"
    await http.aclose()
    settings.credentials_api_key = original


# --- Store → list / random (password IS returned) ---------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_store_then_list_returns_password(vault: AsyncClient) -> None:
    res = await vault.post(
        "/api/credentials",
        json={"email": "saved@example.com", "password": "secreto123"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["email"] == "saved@example.com"
    assert body["used"] is False
    assert body["password"] == "secreto123"

    listed = await vault.get("/api/credentials")
    assert listed.status_code == 200, listed.text
    assert listed.headers["cache-control"] == "no-store"
    row = next(r for r in listed.json() if r["id"] == body["id"])
    assert row["password"] == "secreto123"


@pytest.mark.asyncio(loop_scope="session")
async def test_list_is_oldest_first(vault: AsyncClient) -> None:
    ids = []
    for i in range(3):
        res = await vault.post(
            "/api/credentials",
            json={"email": f"ord{i}@example.com", "password": f"pw{i}"},
        )
        ids.append(res.json()["id"])
    listed = await vault.get("/api/credentials")
    got = [r["id"] for r in listed.json()]
    assert got == sorted(got)  # ascending = oldest first
    assert got == ids  # exact insertion order


@pytest.mark.asyncio(loop_scope="session")
async def test_oldest_returns_oldest(vault: AsyncClient) -> None:
    ids = []
    for i in range(3):
        res = await vault.post(
            "/api/credentials",
            json={"email": f"old{i}@example.com", "password": f"pw{i}"},
        )
        assert res.status_code == 201, res.text
        ids.append(res.json()["id"])

    res = await vault.get("/api/credentials/oldest")
    assert res.status_code == 200, res.text
    assert res.headers["cache-control"] == "no-store"
    body = res.json()
    assert body["id"] == min(ids)  # the first inserted (lowest id)
    assert body["email"] == "old0@example.com"
    assert body["password"] == "pw0"

    # delete it → /oldest now returns the next one.
    await vault.delete(f"/api/credentials/{body['id']}")
    nxt = await vault.get("/api/credentials/oldest")
    assert nxt.json()["email"] == "old1@example.com"


@pytest.mark.asyncio(loop_scope="session")
async def test_oldest_empty_is_404(vault: AsyncClient) -> None:
    res = await vault.get("/api/credentials/oldest")
    assert res.status_code == 404, res.text
    assert res.json()["code"] == "credential_not_found"


# --- Email validation -------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("bad", ["sin-arroba", "a@b", "a@b.", "@b.com", "a b@c.com", ""])
async def test_bad_email_is_400_without_leaking(vault: AsyncClient, bad: str) -> None:
    res = await vault.post(
        "/api/credentials", json={"email": bad, "password": "secreto123"}
    )
    assert res.status_code == 400, res.text
    assert res.json()["code"] == "invalid_credential"
    assert "secreto123" not in res.text


# --- Delete by id -----------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_by_id(vault: AsyncClient) -> None:
    created = await vault.post(
        "/api/credentials",
        json={"email": "todelete@example.com", "password": "secreto123"},
    )
    cred_id = created.json()["id"]

    gone = await vault.delete(f"/api/credentials/{cred_id}")
    assert gone.status_code == 204, gone.text

    listed = await vault.get("/api/credentials")
    assert all(r["id"] != cred_id for r in listed.json())

    # second delete (now unknown) and an oversized id are the SAME 404.
    again = await vault.delete(f"/api/credentials/{cred_id}")
    assert again.status_code == 404
    oversized = await vault.delete(f"/api/credentials/{_PG_INT_MAX + 1}")
    assert oversized.status_code == 404


# --- Delete by email --------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_by_email(vault: AsyncClient) -> None:
    # Two rows share an email; one other email stays.
    for pw in ("p1", "p2"):
        await vault.post(
            "/api/credentials", json={"email": "dupe@example.com", "password": pw}
        )
    await vault.post(
        "/api/credentials", json={"email": "keep@example.com", "password": "k"}
    )

    res = await vault.delete("/api/credentials/by-email", params={"email": "dupe@example.com"})
    assert res.status_code == 204, res.text

    listed = await vault.get("/api/credentials")
    emails = [r["email"] for r in listed.json()]
    assert "dupe@example.com" not in emails
    assert "keep@example.com" in emails

    # nothing matches now → 404 (same as a garbage email).
    miss = await vault.delete("/api/credentials/by-email", params={"email": "dupe@example.com"})
    assert miss.status_code == 404
    assert miss.json()["code"] == "credential_not_found"
    garbage = await vault.delete("/api/credentials/by-email", params={"email": "nope@nope.com"})
    assert garbage.status_code == 404
