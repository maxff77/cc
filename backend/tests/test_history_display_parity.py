"""Historial display-parity tests: ``GET /api/history`` must serve each text
through the SAME composition the cockpit "Aprobadas" panel uses —
``display_transform(redact_reply_text(text), True)``.

- An Amazon Approved verdict is rewritten to the branded LIVE card; a normal
  ✅ is unchanged; a non-verdict reply (``Status: live``) is left ALONE even
  with a ``⌿ Response:`` substring (locks the unconditional ``cookie_mode=True``
  boundary — only a real Amazon verdict is ever transformed).
- A legacy ✅ row stored BEFORE capture-time redaction shipped still carries the
  operator ``⌿ Checked By`` line; history must scrub it on read, never leaking
  the operator name.

Same idiom as test_history.py: real ASGI app against the dev Postgres, captures
go DIRECT to ``capture.process_incoming``, batches drain via ``send_worker.step``.

Run (from backend/, venv active):  pytest tests/test_history_display_parity.py
"""

import pytest
from app.core import capture, send_worker
from app.core.capture import IncomingReply
from app.db.base import async_session_factory
from app.db.models import Batch, Response, User
from httpx import AsyncClient

from tests.conftest import FakeGateway

# --- Local helpers (mirror test_history.py) ----------------------------------


async def _post_batch(http: AsyncClient, text: str, gate_id: int) -> int:
    res = await http.post("/api/batches", json={"text": text, "gate_id": gate_id})
    assert res.status_code == 201, res.text
    return int(res.json()["id"])


async def _drain() -> None:
    while await send_worker.step():
        pass


async def _capture(message_id: int, reply_to: int, text: str) -> None:
    await capture.process_incoming(
        IncomingReply(
            message_id=message_id, reply_to_msg_id=reply_to, text=text, edited=False
        )
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_history_text_matches_aprobadas_display_transform(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """Amazon verdict → ⌿ Response dropped; normal ✅ → unchanged; non-verdict
    (``Status: live``) with a ⌿ Response substring → untouched (the boundary)."""
    http, _ = client_user

    # 3 lines → message_id 1 (Amazon verdict), 2 (normal ✅), 3 (non-verdict).
    await _post_batch(http, "amz\nnormal\nlive", gate["id"])
    await _drain()
    amazon_raw = (
        "☇ CC: 377481016137504|05|2033|3845\n"
        "⌿ Status: Approved ✅\n"
        "⌿ Response: Tarjeta vinculada correctamente. | Removed: ✅ Removido"
    )
    amazon_transformed = (
        "CC: 377481016137504|05|2033|3845\n"
        "- Status: LIVE 100% ✅\n"
        "- Details: Tarjeta apta / Card successfully linked\n"
        "- Response: Process completed successfully ✅\n"
        "- System: Ranger Validation Engine"
    )
    normal = "✅ Aprobada CC: 4111 Status x"
    # Token after Status: is "live" (→ cookie_dead, NOT approved/declined), so
    # display_transform must NOT strip its ⌿ Response substring.
    nonverdict = "✅ CC: 5555 Status: live\n⌿ Response: deberia sobrevivir"
    await _capture(1, 1, amazon_raw)
    await _capture(2, 2, normal)
    await _capture(3, 3, nonverdict)

    res = await http.get("/api/history")
    assert res.status_code == 200, res.text
    items = res.json()["gates"][0]["items"]
    texts = {i["text"] for i in items}

    assert amazon_transformed in texts, texts  # rewritten LIVE card (Aprobadas)
    assert amazon_raw not in texts, texts
    assert normal in texts, texts  # normal ✅ unchanged
    assert nonverdict in texts, texts  # non-verdict untouched (⌿ Response kept)


@pytest.mark.asyncio(loop_scope="session")
async def test_history_redacts_legacy_unredacted_rows(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """A legacy ✅ row stored BEFORE capture-time redaction shipped still carries
    the operator ``⌿ Checked By`` line; history must scrub it on read (parity
    with the cockpit/exports), never leaking the operator name in Historial."""
    http, _ = client_user
    batch_id = await _post_batch(http, "seed", gate["id"])
    await _drain()
    await _capture(1, 1, "✅ Aprobada CC: 4111 Status x")  # establishes the session

    # The real normal-reply format (test_redact.py): the operator name rides a
    # ``⌿ Checked By`` line. Insert it raw/UN-redacted, as a pre-redaction row.
    legacy = (
        "✅ Approved\nCC: 4111111111111111|12|2026|123\n"
        "⌿ Checked By : Richard [User]\nStatus: live"
    )
    async with async_session_factory() as session:
        batch = await session.get(Batch, batch_id)
        assert batch is not None and batch.capture_session_id is not None
        session.add(
            Response(
                tenant_id=batch.tenant_id,
                capture_session_id=batch.capture_session_id,
                batch_id=batch_id,
                chat_id=0,
                message_id=777,
                kind="full",
                status="ok",
                text=legacy,
            )
        )
        await session.commit()

    res = await http.get("/api/history")
    assert res.status_code == 200, res.text
    texts = " ".join(i["text"] for g in res.json()["gates"] for i in g["items"])
    assert "Richard" not in texts, "operator name leaked in Historial"
    assert "Checked By" not in texts, texts
    assert "✅ Approved" in texts  # the rest of the legacy reply survives
