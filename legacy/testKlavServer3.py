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
import secrets


from data import LoginReq, UserInfo, RoomInfo
from serverHelper import extract_token, now_utc, _parse_iso, _evt, _send_json_many, is_valid_room_id

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
    status_ = await manager.register_user(body.username, body.password, body.nickname)
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
        self.room_members: Dict[str, Set[str]] = defaultdict(set)  # room_id -> members
        self.chat_logs: Dict[str, List[dict]] = defaultdict(list)  # room_id -> logs
        # 오프라인 DM 큐(메모리만)
        self.offline_dm: Dict[str, Deque[dict]] = defaultdict(lambda: deque(maxlen=100))

        self.room_infos: Dict[str, dict] = {}

        # 가입자/상세(영속)
        self.users: Set[str] = set()                         # 가입자 집합
        self.user_info: Dict[str, UserInfo] = {}             # username -> UserInfo (O(1) 탐색)

        # 단방향 친구(팔로우)
        self.following: Dict[str, Set[str]] = defaultdict(set)  # user -> {followees}

        self.presence_friend_subs: Dict[str, Set[WebSocket]] = defaultdict(set)

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
            old_room_members = data.get("room_members", {}) or {}
            old_chat_logs    = data.get("chat_logs", {}) or {}
            loaded_infos     = data.get("room_infos", {}) or {}

            # 새 컨테이너 초기화
            self.room_members.clear()
            self.chat_logs.clear()
            self.room_infos = {}

            def new_id():
                rid = self._gen_room_id()
                while rid in self.room_infos:
                    rid = self._gen_room_id()
                return rid

            # 이미 room_infos가 있고 "id" 필드가 들어있는 최신 포맷이면 그대로 사용
            is_newish = False
            for k, v in loaded_infos.items():
                if isinstance(v, dict) and "id" in v:
                    is_newish = True
                    break

            if is_newish:
                # room_infos의 key = room_id 기준으로 복원
                for rid, info in loaded_infos.items():
                    self.room_infos[rid] = info
                # members/logs도 room_id 기준일 것으로 가정
                for rid, members in old_room_members.items():
                    self.room_members[rid] = set(members)
                for rid, logs in old_chat_logs.items():
                    cleaned = []
                    for it in logs:
                        cleaned.append({
                            "ts": it.get("ts"),
                            "kind": it.get("kind", "msg"),
                            "room": rid,  # 기록상 room에는 id를 넣음
                            "from": it.get("from"),
                            "text": it.get("text", ""),
                            **({"to": it["to"]} if "to" in it else {})
                        })
                    self.chat_logs[rid] = cleaned[-self.MAX_LOGS_PER_ROOM:]
            else:
                # 구(이름 키) → 신(ID 키) 변환
                name_to_id: Dict[str, str] = {}
                # 먼저 방 이름들 수집
                all_room_names = set(old_room_members.keys()) | set(old_chat_logs.keys())
                # ID 발급
                for name in sorted(all_room_names):
                    rid = new_id()
                    name_to_id[name] = rid
                    self.room_infos[rid] = {
                        "id": rid,
                        "name": name,
                        "created_at": now_utc().isoformat(),
                        "last": None
                    }
                # 멤버/로그 이관
                for name, members in old_room_members.items():
                    rid = name_to_id[name]
                    self.room_members[rid] = set(members)
                for name, logs in old_chat_logs.items():
                    rid = name_to_id[name]
                    cleaned = []
                    for it in logs:
                        cleaned.append({
                            "ts": it.get("ts"),
                            "kind": it.get("kind", "msg"),
                            "room": rid,   # room에는 id 기록
                            "from": it.get("from"),
                            "text": it.get("text", ""),
                            **({"to": it["to"]} if "to" in it else {})
                        })
                    self.chat_logs[rid] = cleaned[-self.MAX_LOGS_PER_ROOM:]
                # last 채우기(msg 우선 → system)
                for rid, logs in self.chat_logs.items():
                    last = None
                    for it in reversed(logs):
                        if it.get("kind") == "msg":
                            last = it; break
                    if not last:
                        for it in reversed(logs):
                            if it.get("kind") == "system":
                                last = it; break
                    if last:
                        self.room_infos[rid]["last"] = {
                            "text": last.get("text"),
                            "from": last.get("from"),
                            "kind": last.get("kind"),
                            "ts":   last.get("ts"),
                        }

        print("[INFO] 상태 파일 로드 완료 (ID/이름 분리)")

    async def save_state(self):
        async with self.lock:
            data = {
                "room_members": {rid: sorted(list(members)) for rid, members in self.room_members.items()},
                "chat_logs": self.chat_logs,
                "room_infos": self.room_infos,
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

    def _gen_room_id(self) -> str:
        # 짧고 중복위험 낮은 ID (예: r_ab12cd34)
        return "r_" + secrets.token_hex(4)

    async def _ensure_room_by_id(self, room_id: str, name: str | None = None):
        if room_id not in self.room_infos:
            self.room_infos[room_id] = {
                "id": room_id,
                "name": name or room_id,
                "created_at": now_utc().isoformat(),
                "last": None
            }

    async def _find_room_id_by_name(self, name: str) -> str | None:
        for rid, info in self.room_infos.items():
            if info.get("name") == name:
                return rid
        return None

    async def _update_room_last(self, room_id: str, entry: dict):
        # DM은 프라이버시상 last 제외
        if entry.get("kind") == "dm":
            return
        async with self.lock:
            info = self.room_infos.get(room_id)
            if not info:
                # room_infos 엔트리 보장
                self.room_infos[room_id] = {
                    "id": room_id,
                    "name": room_id,
                    "created_at": now_utc().isoformat(),
                    "last": None
                }
            else:
                # 마이그레이션/혼재 대비: 필수 키 보강
                info.setdefault("id", room_id)
                info.setdefault("name", room_id)
                info.setdefault("created_at", now_utc().isoformat())
            self.room_infos[room_id]["last"] = {
                "text": entry.get("text"),
                "from": entry.get("from"),
                "kind": entry.get("kind"),
                "ts":   entry.get("ts"),
            }

    async def create_room(self, name: str, creator: str) -> dict:
        rid = self._gen_room_id()
        await self._ensure_room_by_id(rid, name=name)
        await self.save_state()
        # system 로그(선택)
        creator_nickname = await self._get_nickname(creator)  # ✅ 추가
        await self._append_log(rid, kind="system", text=f'대화방 "{name}"을 {creator_nickname} 님이 만들었습니다', from_user="system", from_nickname="system")  # ✅ 수정
        return self.room_infos[rid]

    async def rooms_summary(self, username: str) -> List[dict]:
        async with self.lock:
            rids = [rid for rid, members in self.room_members.items() if username in members]
            items: List[dict] = []
            for rid in rids:
                info = self.room_infos.get(rid) or {}
                items.append({
                    "id": info.get("id", rid),
                    "name": info.get("name", rid),
                    "last": info.get("last"),
                })
        def keyfn(x):
            ts = (x.get("last") or {}).get("ts")
            return (0, ts) if ts else (1, "")
        return sorted(items, key=keyfn, reverse=True)

    async def join_or_create_by_name(self, name: str, username: str) -> str:
        rid = await self._find_room_id_by_name(name)
        if not rid:
            info = await self.create_room(name, creator=username)
            rid = info["id"]
        await self.join_room_by_id(rid, username)
        return rid
    
    async def join_room_by_id(self, room_id: str, username: str) -> bool:
        await self._ensure_room_by_id(room_id)
        nickname = await self._get_nickname(username)  # ✅ 추가
        async with self.lock:
            already = username in self.room_members[room_id]
            if not already:
                self.room_members[room_id].add(username)
        if not already:
            await self._append_log(room_id, kind="system", text=f"{nickname} 님이 입장하셨습니다", from_user="system", from_nickname="system")  # ✅ 수정
            await self.save_state()
        return not already

    async def leave_room_by_id(self, room_id: str, username: str):
        nickname = await self._get_nickname(username)  # ✅ 추가
        async with self.lock:
            if username in self.room_members.get(room_id, set()):
                self.room_members[room_id].remove(username)
                if not self.room_members[room_id]:
                    self.room_members.pop(room_id, None)
        await self._append_log(room_id, kind="system", text=f"{nickname} 님이 나가셨습니다", from_user="system", from_nickname="system")  # ✅ 수정
        await self.save_state()


    async def rooms_of(self, username: str) -> List[str]:
        async with self.lock:
            return [r for r, members in self.room_members.items() if username in members]
        

    # ---------- 메시지/DM ----------

    async def _targets_in_room(self, room_id: str) -> List[WebSocket]:
        async with self.lock:
            members = list(self.room_members.get(room_id, set()))
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
            to_user: Optional[str]=None):
        entry = {
            "ts": now_utc().isoformat(), 
            "kind": kind, 
            "room": room_id, 
            "from": from_user, 
            "from_nickname": from_nickname or from_user,
            "text": text
            }
        if to_user: entry["to"] = to_user
        async with self.lock:
            logs = self.chat_logs[room_id]
            logs.append(entry)
            if len(logs) > self.MAX_LOGS_PER_ROOM:
                del logs[: len(logs) - self.MAX_LOGS_PER_ROOM]
        await self._update_room_last(room_id, entry)

    async def broadcast_room_message(self, room_id: str, from_user: str, text: str, from_nickname:str=""):
        await self._append_log(room_id, kind="msg", text=text, from_user=from_user, from_nickname=from_nickname)
        await self.save_state()
        targets = await self._targets_in_room(room_id)
        payload = _evt("message", room=room_id, **{"from": from_user}, from_nickname=from_nickname, text=text)
        await _send_json_many(targets, payload)


    async def dm_in_room(self, room_id: str, from_user: str, to_user: str, text: str, from_nickname:str="") -> str:
        async with self.lock:
            members = self.room_members.get(room_id, set())
            if from_user not in members: return "SENDER_NOT_IN_ROOM"
            if to_user not in members:   return "RECIPIENT_NOT_IN_ROOM"
            sockets = list(self.user_conns.get(to_user, []))
        await self._append_log(room_id, kind="dm", text=text, from_user=from_user, to_user=to_user, from_nickname=from_nickname)
        await self.save_state()
        payload = _evt("dm", room=room_id, **{"from": from_user}, from_nickname=from_nickname, to=to_user, text=text)  # ✅ 수정
        if sockets:
            await _send_json_many(sockets, payload); return "DELIVERED"
        async with self.lock:
            self.offline_dm[to_user].append({"room": room_id, "from": from_user, "from_nickname": from_nickname, "text": text, "ts": now_utc().isoformat()}) 
        return "QUEUED"

    async def flush_offline(self, username: str, nickname: str=""):
        async with self.lock:
            q = self.offline_dm.get(username)
            if not q or not len(q):
                return
            items = list(q); q.clear()
            sockets = list(self.user_conns.get(username, []))
        if not sockets:
            async with self.lock:
                self.offline_dm[username].extend(items)
            return
        await asyncio.gather(*(
            _send_json_many(
                sockets,
                _evt("offline_dm", room=it["room"], **{"from": it["from"]}, from_nickname=it.get("from_nickname", it["from"]), text=it["text"], at=it["ts"])  # ✅ 수정
            )
            for it in items
        ), return_exceptions=True)


    async def get_history(self, room_id: str, limit: int = 50, before: str | None = None, after: str | None = None) -> list[dict]:
        async with self.lock:
            logs = list(self.chat_logs.get(room_id, []))
        if after:  logs = [it for it in logs if _parse_iso(it["ts"]) > _parse_iso(after)]
        if before: logs = [it for it in logs if _parse_iso(it["ts"]) < _parse_iso(before)]
        logs.sort(key=lambda it: it["ts"])
        if limit is not None: logs = logs[-int(limit):]
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

    async def register_user(self, username: str, password: str = "default", nickname: str = "") -> str:
        if not username:
            return "INVALID"
        async with self.lock:
            if username in self.users:
                # 이미 등록되어도 상세정보 없으면 기본값 채움(옵션)
                self.user_info.setdefault(username, UserInfo(username, password, nickname=nickname))
                status_ = "ALREADY"
            else:
                self.users.add(username)
                self.user_info[username] = UserInfo(username, password, nickname=nickname)
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
        followees = await self.list_following(observer)  # 내가 추가한 친구들
        async with self.lock:
            online_users = set(self.user_conns.keys())
            conn_counts = {u: len(self.user_conns[u]) for u in online_users}
        result = []
        for u in followees:
            if u in online_users:
                nick = await self._get_nickname(u)
                result.append({
                    "id": u,                 # = username (현재 시스템의 user ID)
                    "username": u,
                    "name": nick or u,       # 표시용 이름
                    "nickname": nick or u,
                    "connections": conn_counts[u],
                })
        result.sort(key=lambda x: x["name"].lower())
        return result

    async def _presence_targets_for_followers(self, subject: str) -> List[WebSocket]:
        followers = await self.list_followers(subject)  # 누가 나를 팔로우하는지
        async with self.lock:
            targets: List[WebSocket] = []
            for obs in followers:
                targets.extend(list(self.presence_friend_subs.get(obs, set())))
        return targets

    async def broadcast_presence_change_to_followers(self, subject: str, status: Literal["online","offline"]):
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
            payload = _evt("system", text=payload)  # 하위호환
        await _send_json_many(sockets, payload)

    async def _get_nickname(self, username: str) -> str:
        """username으로 nickname 조회, 없으면 username 반환"""
        async with self.lock:
            info = self.user_info.get(username)
            if info and info.nickname:
                return info.nickname
            return username


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
                    await websocket.send_json(_evt("create_room_ack", status="INVALID")); continue
                info = await manager.create_room(name, creator=username)
                await websocket.send_json(_evt("create_room_ack", status="CREATED",
                                               room_id=info["id"], name=info["name"]))
            elif typ == "join":
                room_id = data.get("room_id")
                if room_id:
                    added = await manager.join_room_by_id(room_id, username)  # True=막 가입됨, False=이미 멤버
                else:
                    name = data.get("room")
                    if not name:
                        await websocket.send_json(_evt("error", code="ROOM_ID_OR_NAME_REQUIRED"))
                        continue
                    room_id = await manager.join_or_create_by_name(name, username)
                    added = True  # 이름 경로는 새로 만들었을 수 있음
            
                # ✅ 새로 가입된 경우에만 알림 브로드캐스트
                if added:
                    nickname = await manager._get_nickname(username)
                    targets = await manager._targets_in_room(room_id)
                    payload = _evt("system", room=room_id, event="joined", user=username, user_nickname=nickname)  # ✅ 수정
                    await _send_json_many(targets, payload)
            
            elif typ == "leave":
                room_id = data.get("room_id") or data.get("room")
                if not room_id:
                    await websocket.send_json(_evt("error", code="ROOM_ID_REQUIRED")); continue
                nickname = await manager._get_nickname(username)
                await manager.leave_room_by_id(room_id, username)
                targets = await manager._targets_in_room(room_id)
                payload = _evt("system", room=room_id, event="left", user=username, user_nickname=nickname)  # ✅ 수정
                await _send_json_many(targets, payload)

            elif typ == "msg":
                room_id = data.get("room_id") or data.get("room")
                text = data.get("text", "")
                if not room_id:
                    await websocket.send_json(_evt("error", code="ROOM_ID_REQUIRED")); continue
                nickname = await manager._get_nickname(username)
                await manager.broadcast_room_message(room_id, username, text, from_nickname=nickname)  # ✅ 수정

            elif typ == "room_dm":
                room_id = data.get("room_id") or data.get("room")
                to_user = data.get("to")
                text = data.get("text", "")
                if not room_id or not to_user:
                    await websocket.send_json(_evt("error", code="ROOM_ID_AND_TO_REQUIRED")); continue
                nickname = await manager._get_nickname(username)
                status_ = await manager.dm_in_room(room_id, username, to_user, text, from_nickname=nickname)
                await websocket.send_json(_evt("dm_ack", room=room_id, to=to_user, status=status_))
            
            elif typ == "my_rooms":
                summaries = await manager.rooms_summary(username)   # [{id,name,last}, ...]
                await websocket.send_json(_evt("my_rooms",
                                               rooms=[it["id"] for it in summaries],   # 하위호환: id 리스트
                                               rooms_info=summaries)) 
            
            elif typ == "history":
                room_id = data.get("room_id") or data.get("room")
                limit  = int(data.get("limit", 20))
                before = data.get("before")
                after  = data.get("after")
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
                # username 리스트를 UserInfo 형태로 변환
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
                # username 리스트를 UserInfo 형태로 변환
                user_infos = []
                for uname in lst:
                    nickname = await manager._get_nickname(uname)
                    user_infos.append({
                        "username": uname,
                        "nickname": nickname
                    })
                await websocket.send_json(_evt("followers_list", followers=user_infos))

            if typ == "get_online_friends":
                users = await manager.online_friends_snapshot(username)
                await websocket.send_json(_evt("online_friends", users=users))
                continue

            if typ == "presence_friends_subscribe":
                await manager.subscribe_presence_friends(username, websocket)
                # 구독 즉시 1회 스냅샷 제공
                users = await manager.online_friends_snapshot(username)
                await websocket.send_json(_evt("online_friends", users=users))
                continue

            if typ == "presence_friends_unsubscribe":
                await manager.unsubscribe_presence_friends(username, websocket)
                continue

    except WebSocketDisconnect:
        pass
    finally:
        await manager.remove(username, websocket)
        await manager.unsubscribe_presence_friends(username, websocket)
        is_still_online = await manager.is_online(username)
        if not is_still_online:
            await manager.broadcast_presence_change_to_followers(username, "offline")
    
if __name__ == "__main__":
    uvicorn.run("testKlavServer3:app", host="0.0.0.0", port=5000, reload=True)