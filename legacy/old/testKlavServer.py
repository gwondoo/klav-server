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
import os, tempfile
import json
from dataclasses import asdict, replace

from data import LoginReq, UserInfo
from serverHelper import extract_token, now_utc, _parse_iso

JWT_SECRET = "dev-secret-change-me"
JWT_ALG = "HS256"
JWT_EXPIRE_MIN = 60

app = FastAPI()

def create_access_token(sub: str) -> str:
    now = datetime.now(timezone.utc)  # aware UTC
    payload = {
        "sub": sub,
        "exp": now + timedelta(minutes=JWT_EXPIRE_MIN),
        "iat": now,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def verify_token(token: str) -> dict:
    try:
        # exp/iat 필수화 원하면 options={"require": ["exp","iat"]}
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
    status_ = await manager.register_user(body.username, body.password)
    if status_ == "INVALID":
        raise HTTPException(status_code=400, detail="invalid username")
    return {"status": status_}  # CREATED | ALREADY

class ConnectionManager:
    STATE_PATH = "chat_state.json"          # 방 멤버/채팅 로그
    USERS_PATH = "users.json"               # 가입자 명부(+ 상세)
    FRIENDS_PATH = "friends_state.json"     # 단방향 친구(팔로우)
    MAX_LOGS_PER_ROOM = 1000                # 방별 로그 보관 한도

    def __init__(self):
        # 실시간 연결(비영속)
        self.user_conns: Dict[str, Set[WebSocket]] = defaultdict(set)
        # 방 멤버십(영속)
        self.room_members: Dict[str, Set[str]] = defaultdict(set)
        # 채팅 로그(영속)
        self.chat_logs: Dict[str, List[dict]] = defaultdict(list)
        # 오프라인 DM 큐(메모리만)
        self.offline_dm: Dict[str, Deque[dict]] = defaultdict(lambda: deque(maxlen=100))

        # 가입자/상세(영속)
        self.users: Set[str] = set()                         # 가입자 집합
        self.user_info: Dict[str, UserInfo] = {}             # username -> UserInfo (O(1) 탐색)

        # 단방향 친구(팔로우)
        self.following: Dict[str, Set[str]] = defaultdict(set)  # user -> {followees}

        self.lock = asyncio.Lock()
        self.save_lock = asyncio.Lock()

    # ---------- 상태(JSON) ----------
    async def load_state(self):
        if not os.path.exists(self.STATE_PATH):
            return
        try:
            with open(self.STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[WARN] 상태 파일 로드 실패: {e}")
            return

        async with self.lock:
            self.room_members.clear()
            for room, members in data.get("room_members", {}).items():
                self.room_members[room] = set(members)

            self.chat_logs.clear()
            for room, logs in data.get("chat_logs", {}).items():
                cleaned = []
                for it in logs:
                    cleaned.append({
                        "ts": it.get("ts"),
                        "kind": it.get("kind", "msg"),
                        "room": room,
                        "from": it.get("from"),
                        "text": it.get("text", ""),
                        **({"to": it["to"]} if "to" in it else {})
                    })
                self.chat_logs[room] = cleaned[-self.MAX_LOGS_PER_ROOM:]
        print("[INFO] 상태 파일 로드 완료")

    async def save_state(self):
        async with self.lock:
            data = {
                "room_members": {room: sorted(list(members)) for room, members in self.room_members.items()},
                "chat_logs": self.chat_logs,
            }
        async with self.save_lock:
            try:
                dir_ = os.path.dirname(self.STATE_PATH) or "."
                os.makedirs(dir_, exist_ok=True)
                fd, tmp = tempfile.mkstemp(prefix="state_", suffix=".json", dir=dir_)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self.STATE_PATH)
            except Exception as e:
                print(f"[WARN] 상태 파일 저장 실패: {e}")

    # ---------- presence / 연결 ----------
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

    # ---------- 멤버십 ----------
    async def join_room(self, room: str, username: str):
        async with self.lock:
            already = username in self.room_members[room]
            if not already:
                self.room_members[room].add(username)

        if not already:
            # ✅ 처음 가입될 때에만 시스템 로그 기록
            await self._append_log(room, kind="system", text=f"{username} joined", from_user="system")
            await self.save_state()

        return not already
    
    async def leave_room(self, room: str, username: str):
        async with self.lock:
            if username in self.room_members.get(room, set()):
                self.room_members[room].remove(username)
                if not self.room_members[room]:
                    self.room_members.pop(room, None)
        await self._append_log(room, kind="system", text=f"{username} left", from_user="system")
        await self.save_state()

    async def rooms_of(self, username: str) -> List[str]:
        async with self.lock:
            return [r for r, members in self.room_members.items() if username in members]

    # ---------- 메시지/DM ----------
    async def _targets_in_room(self, room: str) -> List[WebSocket]:
        async with self.lock:
            members = list(self.room_members.get(room, set()))
            targets: List[WebSocket] = []
            for u in members:
                targets.extend(self.user_conns.get(u, []))
            return targets

    async def _append_log(self, room: str, kind: str, text: str, from_user: str, to_user: Optional[str]=None):
        entry = {
            "ts": now_utc().isoformat(),
            "kind": kind,
            "room": room,
            "from": from_user,
            "text": text,
        }
        if to_user:
            entry["to"] = to_user
        async with self.lock:
            logs = self.chat_logs[room]
            logs.append(entry)
            if len(logs) > self.MAX_LOGS_PER_ROOM:
                del logs[: len(logs) - self.MAX_LOGS_PER_ROOM]

    async def broadcast_room_message(self, room: str, from_user: str, text: str):
        await self._append_log(room, kind="msg", text=text, from_user=from_user)
        await self.save_state()
        targets = await self._targets_in_room(room)
        wire_text = f"[{room}] {from_user}: {text}"
        await asyncio.gather(*(ws.send_text(wire_text) for ws in targets), return_exceptions=True)

    async def dm_in_room(self, room: str, from_user: str, to_user: str, text: str) -> str:
        async with self.lock:
            members = self.room_members.get(room, set())
            if from_user not in members:
                return "SENDER_NOT_IN_ROOM"
            if to_user not in members:
                return "RECIPIENT_NOT_IN_ROOM"
            sockets = list(self.user_conns.get(to_user, []))
        await self._append_log(room, kind="dm", text=text, from_user=from_user, to_user=to_user)
        await self.save_state()

        msg = f"[dm #{room}] {from_user} → {to_user}: {text}"
        if sockets:
            await asyncio.gather(*(ws.send_text(msg) for ws in sockets), return_exceptions=True)
            return "DELIVERED"
        else:
            async with self.lock:
                self.offline_dm[to_user].append({
                    "room": room, "from": from_user, "text": text, "ts": now_utc().isoformat()
                })
            return "QUEUED"

    async def flush_offline(self, username: str):
        async with self.lock:
            q = self.offline_dm.get(username)
            if not q or not len(q):
                return
            items = list(q); q.clear()
            sockets = list(self.user_conns.get(username, []))
        if not sockets:
            async with self.lock:
                dq = self.offline_dm[username]
                dq.extend(items)
            return
        await asyncio.gather(
            *(
                asyncio.gather(
                    *(ws.send_text(f"[offline dm #{it['room']}] {it['from']}: {it['text']} (at {it['ts']})")
                      for ws in sockets)
                )
                for it in items
            ),
            return_exceptions=True
        )

    async def get_history(self, room: str, limit: int = 50,
                          before: str | None = None, after: str | None = None) -> list[dict]:
        async with self.lock:
            logs = list(self.chat_logs.get(room, []))
        if after:
            aft = _parse_iso(after)
            logs = [it for it in logs if _parse_iso(it["ts"]) > aft]
        if before:
            bef = _parse_iso(before)
            logs = [it for it in logs if _parse_iso(it["ts"]) < bef]
        logs.sort(key=lambda it: it["ts"])
        if limit is not None:
            logs = logs[-int(limit):]
        return logs

    # ---------- USERS (가입자/상세) ----------
    async def load_users(self):
        if not os.path.exists(self.USERS_PATH):
            return
        try:
            with open(self.USERS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[WARN] users.json 로드 실패: {e}")
            return
        async with self.lock:
            self.users = set(data.get("users", []))
            raw = data.get("userinfo", {})
            ui: Dict[str, UserInfo] = {}
            if isinstance(raw, dict):
                for uname, u in raw.items():
                    if isinstance(u, dict):
                        ui[uname] = UserInfo(**u)
            elif isinstance(raw, list):
                for u in raw:
                    if isinstance(u, dict) and "username" in u:
                        ui[u["username"]] = UserInfo(**u)
            self.user_info = ui
        print("[INFO] users.json 로드 완료")

    async def save_users(self):
        async with self.lock:
            data = {
                "users": sorted(list(self.users)),
                "userinfo": {uname: asdict(info) for uname, info in self.user_info.items()},
            }
        async with self.save_lock:
            dir_ = os.path.dirname(self.USERS_PATH) or "."
            os.makedirs(dir_, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix="users_", suffix=".json", dir=dir_)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.USERS_PATH)

    async def register_user(self, username: str, password: str = "default") -> str:
        if not username:
            return "INVALID"
        async with self.lock:
            if username in self.users:
                # 이미 등록되어도 상세정보 없으면 기본값 채움(옵션)
                self.user_info.setdefault(username, UserInfo(username, password))
                status_ = "ALREADY"
            else:
                self.users.add(username)
                self.user_info[username] = UserInfo(username, password)
                status_ = "CREATED"
        await self.save_users()
        return status_

    async def verify_credentials(self, username: str, password: str) -> str:
        """반환: OK | NOT_REGISTERED | INVALID_PASSWORD"""
        async with self.lock:
            if username not in self.users or username not in self.user_info:
                return "NOT_REGISTERED"
            ok = (self.user_info[username].password == password)
        return "OK" if ok else "INVALID_PASSWORD"

    async def get_user_info(self, username: str) -> Optional[UserInfo]:
        async with self.lock:
            return self.user_info.get(username)

    # ---------- FOLLOWING (단방향 친구) ----------
    async def load_follows(self):
        if not os.path.exists(self.FRIENDS_PATH):
            return
        try:
            with open(self.FRIENDS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[WARN] friends_state.json 로드 실패: {e}")
            return
        async with self.lock:
            self.following.clear()
            raw = data.get("following", {})
            for u, lst in raw.items():
                self.following[u] = set(lst)
        print("[INFO] friends_state.json 로드 완료(단방향)")

    async def save_follows(self):
        async with self.lock:
            data = {"following": {u: sorted(list(v)) for u, v in self.following.items()}}
        async with self.save_lock:
            dir_ = os.path.dirname(self.FRIENDS_PATH) or "."
            os.makedirs(dir_, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix="friends_", suffix=".json", dir=dir_)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.FRIENDS_PATH)

    async def follow(self, user: str, target: str) -> str:
        if user == target:
            return "SELF"
        async with self.lock:
            if user not in self.users or target not in self.users:
                return "NOT_REGISTERED"
            if target in self.following[user]:
                return "ALREADY"
            self.following[user].add(target)
        await self.save_follows()
        return "FOLLOWED"

    async def unfollow(self, user: str, target: str) -> str:
        changed = False
        async with self.lock:
            if target in self.following.get(user, set()):
                self.following[user].remove(target)
                changed = True
        if changed:
            await self.save_follows()
            return "UNFOLLOWED"
        return "NOT_FOLLOWING"

    async def list_following(self, user: str) -> List[str]:
        async with self.lock:
            return sorted(list(self.following.get(user, set())))

    async def list_followers(self, user: str) -> List[str]:
        async with self.lock:
            result = [u for u, outs in self.following.items() if user in outs]
        return sorted(result)

    async def send_user(self, username: str, text: str):
        async with self.lock:
            sockets = list(self.user_conns.get(username, []))
        await asyncio.gather(*(ws.send_text(text) for ws in sockets), return_exceptions=True)


manager = ConnectionManager()

# ----- FastAPI 수명주기: 기동 시 로드, 종료 전 저장 -----
@app.on_event("startup")
async def _on_startup():
    await manager.load_state()
    await manager.load_users()     # ✅ 가입자
    await manager.load_follows()

@app.on_event("shutdown")
async def _on_shutdown():
    await manager.save_state()
    await manager.save_users()
    await manager.save_follows()


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

    await manager.accept(username, websocket)

    #user_rooms = await manager.rooms_of(username)
    #await websocket.send_text(f"[system] 환영합니다 {username}. 가입한 방: {user_rooms or '없음'}")
    await manager.flush_offline(username)

    try:
        while True:
            data = await websocket.receive_json()
            typ = data.get("type")

            if typ == "join":
                room = data["room"]
                created = await manager.join_room(room, username)
                # 브로드캐스트 메시지(시스템 공지 로그는 join_room에서 기록됨)
                if created:
                    targets = await manager._targets_in_room(room)
                    note = f"[system] {username} joined #{room}"
                    await asyncio.gather(*(ws.send_text(note) for ws in targets), return_exceptions=True)

            elif typ == "leave":
                room = data["room"]
                await manager.leave_room(room, username)
                targets = await manager._targets_in_room(room)
                note = f"[system] {username} left #{room}"
                await asyncio.gather(*(ws.send_text(note) for ws in targets), return_exceptions=True)

            elif typ == "msg":
                room = data.get("room")
                text = data.get("text", "")
                if not room:
                    await websocket.send_text("[system] room is required")
                    continue
                await manager.broadcast_room_message(room, username, text)

            elif typ == "room_dm":
                room = data.get("room")
                to_user = data.get("to")
                text = data.get("text", "")
                if not room or not to_user:
                    await websocket.send_text("[system] room & to are required")
                    continue
                status_ = await manager.dm_in_room(room, username, to_user, text)
                if status_ == "SENDER_NOT_IN_ROOM":
                    await websocket.send_text(f"[system] 먼저 #{room}에 가입하세요.")
                elif status_ == "RECIPIENT_NOT_IN_ROOM":
                    await websocket.send_text(f"[system] '{to_user}' 님은 #{room}에 없습니다.")
                elif status_ == "QUEUED":
                    await websocket.send_text(f"[system] '{to_user}' 님이 오프라인이라 DM을 큐에 저장했습니다.")
                else:
                    await websocket.send_text(f"[system] '{to_user}' 님에게 DM을 전달했습니다.")

            elif typ == "my_rooms":
                rooms = await manager.rooms_of(username)
                await websocket.send_text(f"[system] 가입한 방: {rooms or '없음'}")

            elif typ == "history":
                    room = data.get("room")
                    if not room:
                        await websocket.send_text("[system] room is required")
                        continue

                    # 기본값은 예전 동작과 맞추어 텍스트 + limit=20
                    limit  = int(data.get("limit", 20))
                    before = data.get("before")   # ISO8601 e.g. "2025-10-10T04:21:00Z"
                    after  = data.get("after")
                    fmt    = (data.get("format") or "text").lower()  # "text" | "json"

                    # 고급 요청(시간 필터 or 포맷 지정)이 있으면 get_history 사용
                    if before or after or "format" in data or "limit" in data:
                        items = await manager.get_history(room, limit=limit, before=before, after=after)
                    else:
                        # 완전 레거시 모드: 단순히 최근 N개만 자르기
                        async with manager.lock:
                            items = manager.chat_logs.get(room, [])[-limit:]

                    if fmt == "json":
                        await websocket.send_json({"type": "history", "room": room, "items": items})
                    else:
                        # 보기 좋은 라인 텍스트
                        lines = []
                        for it in items:
                            k = it.get("kind", "msg"); t = it.get("ts"); frm = it.get("from")
                            if k == "msg":
                                lines.append(f"{t} [{room}] {frm}: {it.get('text','')}")
                            elif k == "dm":
                                to_user = it.get("to", "?")
                                lines.append(f"{t} [dm #{room}] {frm}→{to_user}: {it.get('text','')}")
                            else:
                                lines.append(f"{t} [system #{room}] {it.get('text','')}")
                        await websocket.send_text("\n".join(lines) if lines else "[system] 기록이 없습니다.")

            elif typ == "friend_follow":
                target = data.get("to")
                if not target:
                    await websocket.send_text("[system] friend_follow: 'to'가 필요합니다.")
                    continue
                status_ = await manager.follow(username, target)
                msg_map = {
                    "SELF": "[system] 자기 자신은 팔로우할 수 없습니다.",
                    "NOT_REGISTERED": f"[system] '{target}' 님은 가입자 명부에 없습니다.",
                    "ALREADY": f"[system] 이미 '{target}' 님을 팔로우 중입니다.",
                    "FOLLOWED": f"[system] '{target}' 님을 팔로우했습니다.",
                }
                await websocket.send_text(msg_map.get(status_, "[system] friend_follow 실패"))
                if status_ == "FOLLOWED":
                    await manager.send_user(target, f"[system] '{username}' 님이 당신을 팔로우했습니다.")

            elif typ == "friend_unfollow":
                target = data.get("to")
                if not target:
                    await websocket.send_text("[system] friend_unfollow: 'to'가 필요합니다.")
                    continue
                status_ = await manager.unfollow(username, target)
                msg_map = {
                    "UNFOLLOWED": f"[system] '{target}' 님 팔로우를 해제했습니다.",
                    "NOT_FOLLOWING": f"[system] '{target}' 님을 팔로우하고 있지 않습니다.",
                }
                await websocket.send_text(msg_map.get(status_, "[system] friend_unfollow 실패"))

            elif typ == "following_list":
                lst = await manager.list_following(username)
                await websocket.send_text(f"[system] 내가 팔로우: {lst if lst else '없음'}")

            elif typ == "followers_list":
                lst = await manager.list_followers(username)
                await websocket.send_text(f"[system] 나를 팔로우: {lst if lst else '없음'}")


    except WebSocketDisconnect:
        pass
    finally:
        await manager.remove(username, websocket)
    
if __name__ == "__main__":
    uvicorn.run("testKlavServer:app", host="0.0.0.0", port=5000, reload=True)