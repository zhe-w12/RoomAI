from __future__ import annotations

import asyncio
from collections import defaultdict

from fastapi import WebSocket

from .database import database


class ConnectionManager:
    def __init__(self) -> None:
        self._sockets: dict[str, dict[WebSocket, tuple[str, str]]] = defaultdict(dict)
        self._lock = asyncio.Lock()

    async def connect(self, session_id: str, visitor_id: str, nickname: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._sockets[session_id][websocket] = (visitor_id, nickname)
        await self.broadcast_presence(session_id)

    async def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._sockets[session_id].pop(websocket, None)
            if not self._sockets[session_id]:
                self._sockets.pop(session_id, None)
        await self.broadcast_presence(session_id)

    async def broadcast(self, session_id: str, payload: dict) -> None:
        sockets = list(self._sockets.get(session_id, {}).keys())
        if not sockets:
            return
        results = await asyncio.gather(
            *(socket.send_json(payload) for socket in sockets), return_exceptions=True
        )
        stale = [socket for socket, result in zip(sockets, results) if isinstance(result, Exception)]
        for socket in stale:
            await self.disconnect(session_id, socket)

    async def broadcast_presence(self, session_id: str) -> None:
        online_by_id: dict[str, str] = {}
        for visitor_id, nickname in self._sockets.get(session_id, {}).values():
            online_by_id[visitor_id] = nickname
        await self.broadcast(
            session_id,
            {
                "type": "presence",
                "members": [
                    {"visitor_id": visitor_id, "nickname": nickname}
                    for visitor_id, nickname in online_by_id.items()
                ],
            },
        )


manager = ConnectionManager()


def visitor_for_session(session_id: str, visitor_id: str) -> dict | None:
    with database() as connection:
        row = connection.execute(
            "SELECT visitor_id, nickname, is_owner FROM visitors WHERE session_id = ? AND visitor_id = ?",
            (session_id, visitor_id),
        ).fetchone()
    return dict(row) if row else None
