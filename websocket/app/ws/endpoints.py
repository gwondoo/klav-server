from typing import Optional

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from app.core.auth import get_user_from_django, AuthError
from app.core.config import settings
from .manager import manager

router = APIRouter()


@router.websocket("/ws/rooms/{room_id}")
async def websocket_room_endpoint(
    websocket: WebSocket,
    room_id: int,
    token: Optional[str] = Query(default=None),
):
    # 1) 토큰 추출
    if not token:
        await websocket.close(code=4401)
        return

    # 2) 토큰으로 Django에 유저 정보 요청
    try:
        user_data = await get_user_from_django(token)
    except AuthError:
        await websocket.close(code=4403)
        return

    user_id = user_data.get("id")
    username = user_data.get("username")

    # 3) 연결 등록
    await manager.connect(room_id, websocket)

    # 4) 입장 브로드캐스트
    await manager.broadcast(
        room_id,
        {
            "type": "system",
            "event": "join",
            "user_id": user_id,
            "username": username,
        },
    )

    try:
        # 5) 메시지 수신 루프
        while True:
            data = await websocket.receive_json()
            content = (data.get("content") or "").strip()
            if not content:
                continue

            # 5-1) Django에 메시지 저장 요청
            await save_message_to_django(room_id, token, content)

            # 5-2) 방 전체에 브로드캐스트
            await manager.broadcast(
                room_id,
                {
                    "type": "message",
                    "room_id": room_id,
                    "user_id": user_id,
                    "username": username,
                    "content": content,
                },
            )
    except WebSocketDisconnect:
        manager.disconnect(room_id, websocket)
        await manager.broadcast(
            room_id,
            {
                "type": "system",
                "event": "leave",
                "user_id": user_id,
                "username": username,
            },
        )


async def save_message_to_django(room_id: int, access_token: str, content: str):
    """
    Django REST API로 메시지 저장 요청 보내기.
    POST /api/chats/rooms/{room_id}/messages/
    """
    url = f"{settings.DJANGO_BASE_URL}/api/chats/rooms/{room_id}/messages/"
    headers = {"Authorization": f"Bearer {access_token}"}
    json = {"content": content}

    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(url, headers=headers, json=json)

    if resp.status_code not in (200, 201):
        pass
