import time
from typing import Dict, Optional, Literal
import jwt
from jwt import ExpiredSignatureError, InvalidTokenError
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, WebSocketException, HTTPException, status
from pydantic import BaseModel, Field
import uvicorn
from collections import defaultdict, deque
from typing import Dict, Set, Deque, List
from datetime import datetime, timedelta, timezone
import asyncio
import os
from dataclasses import asdict, replace
import secrets
from sqlalchemy import select, delete, and_, or_, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from data import LoginReq, UserInfo, RoomInfo
from serverHelper import extract_token, now_utc, _parse_iso, _evt, _send_json_many, is_valid_room_id
from database import get_db, init_db, close_db, AsyncSessionLocal
from models import User, Room, RoomMember, ChatLog, Follow

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
JWT_ALG = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MIN = 60

app = FastAPI()

def create_access_token(sub: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "exp": now + timedelta(minutes=JWT_EXPIRE_MIN),
        "iat": now,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def verify_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except ExpiredSignatureError:
        raise HTTPException(status_code=status.WS_1008_POLICY_VIOLATION, detail="Token expired")
    except InvalidTokenError:
        raise HTTPException(status_code=status.WS_1008_POLICY_VIOLATION, detail="Invalid token")

@app.post("/login")
async def login(body: LoginReq):
    if not body.username or not body.password:
        raise HTTPException(status_code=400, detail="username/password required")

    status_ = await manager.verify_credentials(body.username, body.password)
    if status_ == "NOT_REGISTERED":
        raise HTTPException(status_code=401, detail="not registered")
    if status_ == "INVALID_PASSWORD":
        raise HTTPException(status_code=401, detail="invalid credentials")

    token = create_access_token(sub=body.username)
    return {"access_token": token, "token_type": "bearer", "expires_in_minutes": JWT_EXPIRE_MIN}


@app.post("/register")
async def register(body: LoginReq):
    status_ = await manager.register_user(body.username, body.password, body.nickname)
    if status_ == "INVALID":
        raise HTTPException(status_code=400, detail="invalid username")
    return {"status": status_}

@app.get("/health")
async def health_check():
    """헬스체크 엔드포인트"""
    try:
        # DB 연결 확인
        async with get_db() as db:
            from sqlalchemy import text
            await db.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Service unhealthy: {str(e)}")

class ConnectionManager:
    MAX_LOGS_PER_ROOM = 1000

    def __init__(self):
        # 실시간 연결(비영속)
        self.user_conns: Dict[str, Set[WebSocket]] = defaultdict(set)
        # 오프라인 DM 큐(메모리만)
        self.offline_dm: Dict[str, Deque[dict]] = defaultdict(lambda: deque(maxlen=100))
        self.presence_friend_subs: Dict[str, Set[WebSocket]] = defaultdict(set)
        self.lock = asyncio.Lock()

    # ---------- 연결 관리 ----------
    async def accept(self, username: str, ws: WebSocket):
        await ws.accept()
        async with self.lock:
            self.user_conns[username].add(ws)

    async def remove(self, username: str, ws: WebSocket):
        async with self.lock:
            conns = self.user_conns.get(username)
            if conns and ws in conns:
                conns.remove(ws)
                if not conns:
                    self.user_conns.pop(username, None)

    async def is_online(self, username: str) -> bool:
        async with self.lock:
            return bool(self.user_conns.get(username))

    # ---------- 사용자 관리 ----------
    async def register_user(self, username: str, password: str = "default", nickname: str = "") -> str:
        if not username:
            return "INVALID"
        
        async with get_db() as db:
            # 기존 사용자 확인
            result = await db.execute(select(User).where(User.username == username))
            existing = result.scalar_one_or_none()
            
            if existing:
                return "ALREADY"
            
            # 새 사용자 생성
            new_user = User(
                username=username,
                password=password,
                nickname=nickname or username
            )
            db.add(new_user)
            await db.commit()
            return "CREATED"

    async def verify_credentials(self, username: str, password: str) -> str:
        async with get_db() as db:
            result = await db.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()
            
            if not user:
                return "NOT_REGISTERED"
            
            return "OK" if user.password == password else "INVALID_PASSWORD"

    async def get_user_info(self, username: str) -> Optional[UserInfo]:
        async with get_db() as db:
            result = await db.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()
            
            if not user:
                return None
            
            return UserInfo(
                username=user.username,
                password=user.password,
                extra=user.extra,
                nickname=user.nickname
            )

    async def _get_nickname(self, username: str) -> str:
        user_info = await self.get_user_info(username)
        return user_info.nickname if user_info and user_info.nickname else username

    # ---------- 채팅방 관리 ----------
    def _gen_room_id(self) -> str:
        return "r_" + secrets.token_hex(4)

    async def _ensure_room_by_id(self, room_id: str, name: str | None = None, db: AsyncSession = None):
        close_db_after = False
        if db is None:
            db = AsyncSessionLocal()
            close_db_after = True
        
        try:
            result = await db.execute(select(Room).where(Room.id == room_id))
            existing = result.scalar_one_or_none()
            
            if not existing:
                new_room = Room(
                    id=room_id,
                    name=name or room_id,
                    created_at=now_utc()
                )
                db.add(new_room)
                await db.commit()
        finally:
            if close_db_after:
                await db.close()

    async def _find_room_id_by_name(self, name: str) -> str | None:
        async with get_db() as db:
            result = await db.execute(select(Room).where(Room.name == name))
            room = result.scalar_one_or_none()
            return room.id if room else None

    async def _update_room_last(self, room_id: str, entry: dict):
        if entry.get("kind") == "dm":
            return
        
        async with get_db() as db:
            result = await db.execute(select(Room).where(Room.id == room_id))
            room = result.scalar_one_or_none()
            
            if room:
                room.last_message_text = entry.get("text")
                room.last_message_from = entry.get("from")
                room.last_message_kind = entry.get("kind")
                room.last_message_ts = _parse_iso(entry.get("ts")) if entry.get("ts") else now_utc()
                await db.commit()

    async def create_room(self, name: str, creator: str) -> dict:
        rid = self._gen_room_id()
        
        async with get_db() as db:
            new_room = Room(
                id=rid,
                name=name,
                created_at=now_utc()
            )
            db.add(new_room)
            await db.commit()
            
            creator_nickname = await self._get_nickname(creator)
            await self._append_log(
                rid, 
                kind="system", 
                text=f'대화방 "{name}"을 {creator_nickname} 님이 만들었습니다',
                from_user="system",
                from_nickname="system"
            )
            
            return {
                "id": rid,
                "name": name,
                "created_at": new_room.created_at.isoformat()
            }

    async def rooms_summary(self, username: str) -> List[dict]:
        async with get_db() as db:
            # 사용자가 속한 방들 조회
            stmt = (
                select(Room)
                .join(RoomMember, Room.id == RoomMember.room_id)
                .where(RoomMember.username == username)
                .order_by(desc(Room.last_message_ts))
            )
            result = await db.execute(stmt)
            rooms = result.scalars().all()
            
            items = []
            for room in rooms:
                last_info = None
                if room.last_message_text:
                    last_info = {
                        "text": room.last_message_text,
                        "from": room.last_message_from,
                        "kind": room.last_message_kind,
                        "ts": room.last_message_ts.isoformat() if room.last_message_ts else None
                    }
                
                items.append({
                    "id": room.id,
                    "name": room.name,
                    "last": last_info
                })
            
            return items

    async def join_or_create_by_name(self, name: str, username: str) -> str:
        rid = await self._find_room_id_by_name(name)
        if not rid:
            info = await self.create_room(name, creator=username)
            rid = info["id"]
        await self.join_room_by_id(rid, username)
        return rid

    async def join_room_by_id(self, room_id: str, username: str) -> bool:
        async with get_db() as db:
            await self._ensure_room_by_id(room_id, db=db)
            
            # 이미 멤버인지 확인
            result = await db.execute(
                select(RoomMember).where(
                    and_(RoomMember.room_id == room_id, RoomMember.username == username)
                )
            )
            existing = result.scalar_one_or_none()
            
            if existing:
                return False
            
            # 새 멤버 추가
            new_member = RoomMember(room_id=room_id, username=username)
            db.add(new_member)
            await db.commit()
            
            nickname = await self._get_nickname(username)
            await self._append_log(
                room_id,
                kind="system",
                text=f"{nickname} 님이 입장하셨습니다",
                from_user="system",
                from_nickname="system"
            )
            
            return True

    async def leave_room_by_id(self, room_id: str, username: str):
        nickname = await self._get_nickname(username)
        
        async with get_db() as db:
            await db.execute(
                delete(RoomMember).where(
                    and_(RoomMember.room_id == room_id, RoomMember.username == username)
                )
            )
            await db.commit()
        
        await self._append_log(
            room_id,
            kind="system",
            text=f"{nickname} 님이 나가셨습니다",
            from_user="system",
            from_nickname="system"
        )

    async def rooms_of(self, username: str) -> List[str]:
        async with get_db() as db:
            result = await db.execute(
                select(RoomMember.room_id).where(RoomMember.username == username)
            )
            return [row[0] for row in result.all()]

    # ---------- 메시지 관리 ----------
    async def _targets_in_room(self, room_id: str) -> List[WebSocket]:
        async with get_db() as db:
            result = await db.execute(
                select(RoomMember.username).where(RoomMember.room_id == room_id)
            )
            members = [row[0] for row in result.all()]
        
        async with self.lock:
            targets: List[WebSocket] = []
            for u in members:
                targets.extend(self.user_conns.get(u, []))
            return targets

    async def _append_log(
        self,
        room_id: str,
        kind: str,
        text: str,
        from_user: str,
        from_nickname: str = "",
        to_user: Optional[str] = None
    ):
        async with get_db() as db:
            new_log = ChatLog(
                room_id=room_id,
                ts=now_utc(),
                kind=kind,
                from_user=from_user,
                from_nickname=from_nickname or from_user,
                to_user=to_user,
                text=text
            )
            db.add(new_log)
            await db.commit()
            
            # 방의 마지막 메시지 업데이트
            entry = {
                "ts": new_log.ts.isoformat(),
                "kind": kind,
                "from": from_user,
                "text": text
            }
            await self._update_room_last(room_id, entry)

    async def broadcast_room_message(self, room_id: str, from_user: str, text: str, from_nickname: str = ""):
        await self._append_log(room_id, kind="msg", text=text, from_user=from_user, from_nickname=from_nickname)
        targets = await self._targets_in_room(room_id)
        payload = _evt("message", room=room_id, **{"from": from_user}, from_nickname=from_nickname, text=text)
        await _send_json_many(targets, payload)

    async def dm_in_room(self, room_id: str, from_user: str, to_user: str, text: str, from_nickname: str = "") -> str:
        async with get_db() as db:
            # 두 사용자가 모두 방에 속해있는지 확인
            sender_result = await db.execute(
                select(RoomMember).where(
                    and_(RoomMember.room_id == room_id, RoomMember.username == from_user)
                )
            )
            if not sender_result.scalar_one_or_none():
                return "SENDER_NOT_IN_ROOM"
            
            recipient_result = await db.execute(
                select(RoomMember).where(
                    and_(RoomMember.room_id == room_id, RoomMember.username == to_user)
                )
            )
            if not recipient_result.scalar_one_or_none():
                return "RECIPIENT_NOT_IN_ROOM"
        
        await self._append_log(room_id, kind="dm", text=text, from_user=from_user, to_user=to_user, from_nickname=from_nickname)
        
        async with self.lock:
            sockets = list(self.user_conns.get(to_user, []))
        
        payload = _evt("dm", room=room_id, **{"from": from_user}, from_nickname=from_nickname, to=to_user, text=text)
        if sockets:
            await _send_json_many(sockets, payload)
            return "DELIVERED"
        
        async with self.lock:
            self.offline_dm[to_user].append({
                "room": room_id,
                "from": from_user,
                "from_nickname": from_nickname,
                "text": text,
                "ts": now_utc().isoformat()
            })
        return "QUEUED"

    async def flush_offline(self, username: str, nickname: str = ""):
        async with self.lock:
            q = self.offline_dm.get(username)
            if not q or not len(q):
                return
            items = list(q)
            q.clear()
            sockets = list(self.user_conns.get(username, []))
        
        if not sockets:
            async with self.lock:
                self.offline_dm[username].extend(items)
            return
        
        await asyncio.gather(*(
            _send_json_many(
                sockets,
                _evt("offline_dm", room=it["room"], **{"from": it["from"]}, from_nickname=it.get("from_nickname", it["from"]), text=it["text"], at=it["ts"])
            )
            for it in items
        ), return_exceptions=True)

    async def get_history(self, room_id: str, limit: int = 50, before: str | None = None, after: str | None = None) -> list[dict]:
        async with get_db() as db:
            stmt = select(ChatLog).where(ChatLog.room_id == room_id)
            
            if after:
                stmt = stmt.where(ChatLog.ts > _parse_iso(after))
            if before:
                stmt = stmt.where(ChatLog.ts < _parse_iso(before))
            
            stmt = stmt.order_by(ChatLog.ts.desc()).limit(limit)
            result = await db.execute(stmt)
            logs = result.scalars().all()
            
            # 최신순으로 가져왔으니 역순으로 변환
            logs = list(reversed(logs))
            
            return [
                {
                    "ts": log.ts.isoformat(),
                    "kind": log.kind,
                    "room": log.room_id,
                    "from": log.from_user,
                    "from_nickname": log.from_nickname,
                    "text": log.text,
                    **({"to": log.to_user} if log.to_user else {})
                }
                for log in logs
            ]

    # ---------- 친구 관리 ----------
    async def follow(self, user: str, target: str) -> str:
        if user == target:
            return "SELF"
        
        async with get_db() as db:
            # 사용자 존재 확인
            user_result = await db.execute(select(User).where(User.username == user))
            target_result = await db.execute(select(User).where(User.username == target))
            
            if not user_result.scalar_one_or_none() or not target_result.scalar_one_or_none():
                return "NOT_REGISTERED"
            
            # 이미 팔로우 중인지 확인
            follow_result = await db.execute(
                select(Follow).where(
                    and_(Follow.follower_username == user, Follow.followee_username == target)
                )
            )
            if follow_result.scalar_one_or_none():
                return "ALREADY"
            
            # 팔로우 추가
            new_follow = Follow(follower_username=user, followee_username=target)
            db.add(new_follow)
            await db.commit()
            return "FOLLOWED"

    async def unfollow(self, user: str, target: str) -> str:
        async with get_db() as db:
            result = await db.execute(
                delete(Follow).where(
                    and_(Follow.follower_username == user, Follow.followee_username == target)
                ).returning(Follow.id)
            )
            deleted = result.scalar_one_or_none()
            await db.commit()
            
            return "UNFOLLOWED" if deleted else "NOT_FOLLOWING"

    async def list_following(self, user: str) -> List[str]:
        async with get_db() as db:
            result = await db.execute(
                select(Follow.followee_username).where(Follow.follower_username == user)
            )
            return sorted([row[0] for row in result.all()])

    async def list_followers(self, user: str) -> List[str]:
        async with get_db() as db:
            result = await db.execute(
                select(Follow.follower_username).where(Follow.followee_username == user)
            )
            return sorted([row[0] for row in result.all()])

    async def subscribe_presence_friends(self, observer: str, ws: WebSocket):
        async with self.lock:
            self.presence_friend_subs[observer].add(ws)

    async def unsubscribe_presence_friends(self, observer: str, ws: WebSocket):
        async with self.lock:
            s = self.presence_friend_subs.get(observer)
            if s and ws in s:
                s.remove(ws)
                if not s:
                    self.presence_friend_subs.pop(observer, None)

    async def online_friends_snapshot(self, observer: str) -> List[dict]:
        followees = await self.list_following(observer)
        async with self.lock:
            online_users = set(self.user_conns.keys())
            conn_counts = {u: len(self.user_conns[u]) for u in online_users}
        
        result = []
        for u in followees:
            if u in online_users:
                nick = await self._get_nickname(u)
                result.append({
                    "id": u,
                    "username": u,
                    "name": nick or u,
                    "nickname": nick or u,
                    "connections": conn_counts[u],
                })
        result.sort(key=lambda x: x["name"].lower())
        return result

    async def _presence_targets_for_followers(self, subject: str) -> List[WebSocket]:
        followers = await self.list_followers(subject)
        async with self.lock:
            targets: List[WebSocket] = []
            for obs in followers:
                targets.extend(list(self.presence_friend_subs.get(obs, set())))
        return targets

    async def broadcast_presence_change_to_followers(self, subject: str, status: Literal["online", "offline"]):
        nick = await self._get_nickname(subject)
        payload = _evt("presence_change",
                       scope="friends",
                       user=subject,
                       name=nick or subject,
                       status=status)
        targets = await self._presence_targets_for_followers(subject)
        await _send_json_many(targets, payload)

    async def send_user(self, username: str, payload: dict | str):
        async with self.lock:
            sockets = list(self.user_conns.get(username, []))
        if isinstance(payload, str):
            payload = _evt("system", text=payload)
        await _send_json_many(sockets, payload)


manager = ConnectionManager()

# ----- FastAPI 수명주기 -----
@app.on_event("startup")
async def _on_startup():
    await init_db()
    print("[INFO] Database initialized")

@app.on_event("shutdown")
async def _on_shutdown():
    await close_db()
    print("[INFO] Database connection closed")


# ===== WebSocket =====
@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    token = extract_token(websocket)
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    try:
        payload = verify_token(token)
        username = payload.get("sub", "anonymous")
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    
    was_online = await manager.is_online(username)
    await manager.accept(username, websocket)
    await manager.flush_offline(username)

    if not was_online:
        await manager.broadcast_presence_change_to_followers(username, "online")

    try:
        while True:
            data = await websocket.receive_json()
            typ = data.get("type")

            if typ == "create_room":
                name = (data.get("name") or "").strip()
                if not name:
                    await websocket.send_json(_evt("create_room_ack", status="INVALID"))
                    continue
                info = await manager.create_room(name, creator=username)
                await websocket.send_json(_evt("create_room_ack", status="CREATED",
                                               room_id=info["id"], name=info["name"]))
            
            elif typ == "join":
                room_id = data.get("room_id")
                if room_id:
                    added = await manager.join_room_by_id(room_id, username)
                else:
                    name = data.get("room")
                    if not name:
                        await websocket.send_json(_evt("error", code="ROOM_ID_OR_NAME_REQUIRED"))
                        continue
                    room_id = await manager.join_or_create_by_name(name, username)
                    added = True
                
                if added:
                    nickname = await manager._get_nickname(username)
                    targets = await manager._targets_in_room(room_id)
                    payload = _evt("system", room=room_id, event="joined", user=username, user_nickname=nickname)
                    await _send_json_many(targets, payload)
            
            elif typ == "leave":
                room_id = data.get("room_id") or data.get("room")
                if not room_id:
                    await websocket.send_json(_evt("error", code="ROOM_ID_REQUIRED"))
                    continue
                nickname = await manager._get_nickname(username)
                await manager.leave_room_by_id(room_id, username)
                targets = await manager._targets_in_room(room_id)
                payload = _evt("system", room=room_id, event="left", user=username, user_nickname=nickname)
                await _send_json_many(targets, payload)

            elif typ == "msg":
                room_id = data.get("room_id") or data.get("room")
                text = data.get("text", "")
                if not room_id:
                    await websocket.send_json(_evt("error", code="ROOM_ID_REQUIRED"))
                    continue
                nickname = await manager._get_nickname(username)
                await manager.broadcast_room_message(room_id, username, text, from_nickname=nickname)

            elif typ == "room_dm":
                room_id = data.get("room_id") or data.get("room")
                to_user = data.get("to")
                text = data.get("text", "")
                if not room_id or not to_user:
                    await websocket.send_json(_evt("error", code="ROOM_ID_AND_TO_REQUIRED"))
                    continue
                nickname = await manager._get_nickname(username)
                status_ = await manager.dm_in_room(room_id, username, to_user, text, from_nickname=nickname)
                await websocket.send_json(_evt("dm_ack", room=room_id, to=to_user, status=status_))
            
            elif typ == "my_rooms":
                summaries = await manager.rooms_summary(username)
                await websocket.send_json(_evt("my_rooms",
                                               rooms=[it["id"] for it in summaries],
                                               rooms_info=summaries))
            
            elif typ == "history":
                room_id = data.get("room_id") or data.get("room")
                limit = int(data.get("limit", 20))
                before = data.get("before")
                after = data.get("after")
                items = await manager.get_history(room_id, limit=limit, before=before, after=after)
                await websocket.send_json({"type": "history", "room": room_id, "items": items})

            elif typ == "friend_follow":
                target = data.get("to")
                if not target:
                    await websocket.send_json(_evt("error", code="FOLLOW_TO_REQUIRED"))
                    continue
                status_ = await manager.follow(username, target)
                await websocket.send_json(_evt("friend_follow_ack", to=target, status=status_))
                if status_ == "FOLLOWED":
                    await manager.send_user(target, _evt("notify_followed", **{"from": username}))

            elif typ == "friend_unfollow":
                target = data.get("to")
                if not target:
                    await websocket.send_json(_evt("error", code="UNFOLLOW_TO_REQUIRED"))
                    continue
                status_ = await manager.unfollow(username, target)
                await websocket.send_json(_evt("friend_unfollow_ack", to=target, status=status_))

            elif typ == "following_list":
                lst = await manager.list_following(username)
                user_infos = []
                for uname in lst:
                    nickname = await manager._get_nickname(uname)
                    user_infos.append({
                        "username": uname,
                        "nickname": nickname
                    })
                await websocket.send_json(_evt("following_list", following=user_infos))

            elif typ == "followers_list":
                lst = await manager.list_followers(username)
                user_infos = []
                for uname in lst:
                    nickname = await manager._get_nickname(uname)
                    user_infos.append({
                        "username": uname,
                        "nickname": nickname
                    })
                await websocket.send_json(_evt("followers_list", followers=user_infos))

            elif typ == "get_online_friends":
                users = await manager.online_friends_snapshot(username)
                await websocket.send_json(_evt("online_friends", users=users))

            elif typ == "presence_friends_subscribe":
                await manager.subscribe_presence_friends(username, websocket)
                users = await manager.online_friends_snapshot(username)
                await websocket.send_json(_evt("online_friends", users=users))

            elif typ == "presence_friends_unsubscribe":
                await manager.unsubscribe_presence_friends(username, websocket)

    except WebSocketDisconnect:
        pass
    finally:
        await manager.remove(username, websocket)
        await manager.unsubscribe_presence_friends(username, websocket)
        is_still_online = await manager.is_online(username)
        if not is_still_online:
            await manager.broadcast_presence_change_to_followers(username, "offline")

if __name__ == "__main__":
    uvicorn.run("serverPostgres:app", host="0.0.0.0", port=5000, reload=True)
