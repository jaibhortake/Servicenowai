import logging
from typing import Dict, Set
from fastapi import WebSocket

logger = logging.getLogger(__name__)

class WebSocketService:
    """Manages active WebSocket connections for ticket updates, keyed by ticket sys_id."""

    def __init__(self):
        # ticket_sys_id -> set of WebSocket connections
        self._connections: Dict[str, Set[WebSocket]] = {}

    def register(self, ticket_sys_id: str, ws: WebSocket) -> None:
        """Register a WebSocket connection for a ticket."""
        if ticket_sys_id not in self._connections:
            self._connections[ticket_sys_id] = set()
        self._connections[ticket_sys_id].add(ws)
        logger.debug(f"[WS] Registered connection for ticket {ticket_sys_id}, total: {len(self._connections[ticket_sys_id])}")

    def unregister(self, ticket_sys_id: str, ws: WebSocket) -> None:
        """Unregister a WebSocket connection."""
        if ticket_sys_id in self._connections:
            self._connections[ticket_sys_id].discard(ws)
            if not self._connections[ticket_sys_id]:
                del self._connections[ticket_sys_id]
        logger.debug(f"[WS] Unregistered connection for ticket {ticket_sys_id}")

    async def push(self, ticket_sys_id: str, payload: dict) -> None:
        """Push a message to all connected WebSocket clients for a ticket."""
        if ticket_sys_id not in self._connections:
            logger.debug(f"[WS] No connections for ticket {ticket_sys_id}, skipping push")
            return

        disconnected = []
        for ws in self._connections[ticket_sys_id]:
            try:
                await ws.send_json(payload)
            except Exception as e:
                logger.warning(f"[WS] Failed to send to connection: {e}")
                disconnected.append(ws)

        # Clean up broken connections
        for ws in disconnected:
            self.unregister(ticket_sys_id, ws)

websocket_service = WebSocketService()
