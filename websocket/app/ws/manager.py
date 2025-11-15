# app/ws/manager.py
from typing import Dict, List

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        # room_id -> list[WebSocket]
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, room_id: int, websocket: WebSocket):
        await websocket.accept()
        if room_id not in self.active_connections:
            self.active_connections[room_id] = []
        self.active_connections[room_id].append(websocket)

    def disconnect(self, room_id: int, websocket: WebSocket):
        if room_id in self.active_connections:
            if websocket in self.active_connections[room_id]:
                self.active_connections[room_id].remove(websocket)
            if not self.active_connections[room_id]:
                del self.active_connections[room_id]

    async def broadcast(self, room_id: int, message: dict):
        """
        같은 방 room_id에 연결된 모든 클라이언트에 message 전송
        """
        if room_id not in self.active_connections:
            return
        for connection in list(self.active_connections[room_id]):
            try:
                await connection.send_json(message)
            except Exception:
                # 끊긴 소켓은 제거
                self.disconnect(room_id, connection)


manager = ConnectionManager()
