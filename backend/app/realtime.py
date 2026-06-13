from collections import defaultdict
from typing import Any

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[int, set[WebSocket]] = defaultdict(set)

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[user_id].add(websocket)

    def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        self._connections[user_id].discard(websocket)

    async def send_to_user(self, user_id: int, event: str, payload: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for socket in self._connections.get(user_id, set()):
            try:
                await socket.send_json({"event": event, "payload": payload})
            except RuntimeError:
                dead.append(socket)
        for socket in dead:
            self.disconnect(user_id, socket)
