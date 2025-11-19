from fastapi import FastAPI, WebSocket
from datetime import datetime, timedelta, timezone
import asyncio
import re

def extract_token(ws: WebSocket) -> str | None:
    auth = ws.headers.get("authorization")
    if not auth:
        return None
    # e.g. "Bearer xxx.yyy.zzz"
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None

def now_utc():
    return datetime.now(timezone.utc)

def _parse_iso(ts: str) -> datetime:
    # "Z"도 허용
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))

async def _send_json_many(sockets: list[WebSocket], payload: dict):
    await asyncio.gather(*(ws.send_json(payload) for ws in sockets), return_exceptions=True)

    
def _evt(type_: str, **kwargs) -> dict:
    return {"type": type_, "ts": now_utc().isoformat(), **kwargs}

def is_valid_room_id(rid: str) -> bool:
    # r_ + 8자리 hex (secrets.token_hex(4)) 형식
    return bool(re.fullmatch(r"r_[0-9a-f]{8}", rid or ""))