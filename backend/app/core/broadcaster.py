"""Tenant-scoped WebSocket fan-out (port of the legacy ``Broadcaster``).

Envelope is EXACTLY ``{"event": "<name>", "data": {...}}`` (architecture
communication contract). Events are tenant-scoped — a tab only ever receives
its own tenant's events — except ``emit_global`` (``flood.wait``: every
FloodWait is explained to everyone). Dead sockets are discarded on send
failure, never raised.
"""

import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class Broadcaster:
    """Registry of open sockets per tenant."""

    def __init__(self) -> None:
        self._conns: dict[int, set[WebSocket]] = {}

    def register(self, tenant_id: int, ws: WebSocket) -> None:
        self._conns.setdefault(tenant_id, set()).add(ws)

    def unregister(self, tenant_id: int, ws: WebSocket) -> None:
        conns = self._conns.get(tenant_id)
        if conns is None:
            return
        conns.discard(ws)
        if not conns:
            del self._conns[tenant_id]

    async def emit(self, tenant_id: int, event: str, data: dict) -> None:
        """Send an event to every open tab of one tenant."""
        await self._send(self._conns.get(tenant_id, set()), tenant_id, event, data)

    async def emit_global(self, event: str, data: dict) -> None:
        """Send an event to every connected tab of every tenant (flood.wait)."""
        for tenant_id, conns in list(self._conns.items()):
            await self._send(conns, tenant_id, event, data)

    async def _send(
        self, conns: set[WebSocket], tenant_id: int, event: str, data: dict
    ) -> None:
        message = {"event": event, "data": data}
        for ws in list(conns):
            try:
                await ws.send_json(message)
            except Exception:
                # Dead socket (closed mid-send): drop it silently — the
                # client's auto-reconnect will re-register and get a snapshot.
                self.unregister(tenant_id, ws)


# Module-level singleton (same idiom as settings / gateway).
broadcaster = Broadcaster()
