import asyncio
import aiohttp
import requests
import argparse
import shlex
import json
from typing import Optional

DEFAULT_API = "http://127.0.0.1:5000"
DEFAULT_WS  = "ws://127.0.0.1:5000/ws"

# ---------------- HTTP helpers ----------------
def http_register(api_base: str, username: str, password: str) -> Optional[str]:
    """POST /register → {'status': 'CREATED'|'ALREADY'}"""
    try:
        r = requests.post(
            f"{api_base}/register",
            json={"username": username, "password": password},
            timeout=5,
        )
        r.raise_for_status()
        status_ = r.json().get("status")
        print(f"[INFO] /register 결과: {status_}")
        return status_
    except requests.HTTPError as e:
        try:
            detail = r.json().get("detail")
        except Exception:
            detail = str(e)
        print(f"[ERR] /register 실패: {r.status_code} {detail}")
        return None
    except requests.RequestException as e:
        print(f"[ERR] /register 호출 오류: {e}")
        return None

def http_login(api_base: str, username: str, password: str) -> tuple[Optional[str], Optional[str]]:
    """
    POST /login → (token, err)
    err: None | "not_registered" | "invalid_credentials" | "network"
    """
    try:
        r = requests.post(
            f"{api_base}/login",
            json={"username": username, "password": password},
            timeout=5,
        )
        if r.status_code == 200:
            tok = r.json()["access_token"]
            print(f"[INFO] 토큰 발급 성공 (앞부분): {tok[:24]}...")
            return tok, None
        else:
            # 서버가 detail을 표준 메시지로 보냄
            detail = ""
            try:
                detail = r.json().get("detail", "")
            except Exception:
                pass
            if detail == "not registered":
                return None, "not_registered"
            if detail == "invalid credentials":
                return None, "invalid_credentials"
            return None, f"http_{r.status_code}"
    except requests.RequestException as e:
        print(f"[ERR] /login 호출 오류: {e}")
        return None, "network"

# ---------------- WebSocket client ----------------
async def interactive_chat(api_base: str, ws_url: str, username: str, password: str,
                           default_room: str = "lobby",
                           auto_register: bool = False):
    """
    - 서버 정책: 미등록/비번 불일치 시 /login 401
    - auto_register=True이면 /login에서 'not registered'면 /register 후 자동 재시도
    - 연결은 채팅 화면에서만 유지(간단 구현)
    """
    current_room = default_room

    async def run_once() -> Optional[int]:
        nonlocal current_room

        # 1) 로그인 (필요시 자동 가입)
        token, err = http_login(api_base, username, password)
        if err == "not_registered":
            if auto_register:
                print("[INFO] 미등록 사용자입니다. 자동 가입을 시도합니다...")
                reg = http_register(api_base, username, password)
                if reg in ("CREATED", "ALREADY"):
                    token, err = http_login(api_base, username, password)
                else:
                    print("[ERR] 자동 가입 실패")
                    return None
            else:
                print("[ERR] 미등록 사용자입니다. 먼저 /register 를 실행하세요.")
                return None
        if err == "invalid_credentials":
            print("[ERR] 비밀번호가 일치하지 않습니다.")
            return None
        if token is None:
            print(f"[ERR] 로그인 실패: {err}")
            return None

        headers = {"Authorization": f"Bearer {token}"}

        async with aiohttp.ClientSession() as session:
            try:
                async with session.ws_connect(
                    ws_url,
                    headers=headers,
                    heartbeat=30,
                    timeout=aiohttp.ClientTimeout(total=None),
                ) as ws:
                    print("[INFO] WebSocket 연결됨.")
                    print("명령 예시:")
                    print("  /register                 가입(이미 가입이면 ALREADY)")
                    print("  /join <room>              방 입장")
                    print("  /leave <room>             방 나가기")
                    print("  /say <room> <text>        방 메시지")
                    print("  /dm <room> <user> <text>  방 내 DM (오프라인이면 큐)")
                    print("  /history <room> [limit] [--before ISO] [--after ISO] [--text]")
                    print("  /friend_follow <user>     단방향 친구(팔로우)")
                    print("  /friend_unfollow <user>   팔로우 해제")
                    print("  /following_list           내가 팔로우")
                    print("  /followers_list           나를 팔로우")
                    print("  /room <room>              기본 전송 방 변경")
                    print("  /help, /quit")
                    print(f"[INFO] 기본 전송 방: {current_room}")

                    async def reader():
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                raw = msg.data
                                # history JSON이면 예쁘게 출력
                                try:
                                    obj = json.loads(raw)
                                    if isinstance(obj, dict) and obj.get("type") == "history":
                                        room = obj.get("room")
                                        items = obj.get("items", [])
                                        print(f"[HISTORY] room={room}")
                                        for it in items:
                                            k = it.get("kind"); t = it.get("ts"); frm = it.get("from")
                                            if k == "msg":
                                                print(f"  {t} [{room}] {frm}: {it.get('text','')}")
                                            elif k == "dm":
                                                print(f"  {t} [dm #{room}] {frm}→{it.get('to','?')}: {it.get('text','')}")
                                            else:
                                                print(f"  {t} [system #{room}] {it.get('text','')}")
                                        continue
                                except Exception:
                                    pass
                                print("[RECV]", raw)
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                print("[ERR] WS 오류:", ws.exception())
                                break

                    reader_task = asyncio.create_task(reader())
                    loop = asyncio.get_running_loop()

                    try:
                        while True:
                            line = await loop.run_in_executor(None, input, f"{username}@{current_room}> ")
                            line = line.strip()
                            if not line:
                                continue

                            if line.startswith("/"):
                                parts = shlex.split(line)
                                cmd = parts[0].lower()

                                # 계정
                                if cmd == "/register":
                                    http_register(api_base, username, password)

                                # 친구(팔로우)
                                elif cmd == "/friend_follow" and len(parts) >= 2:
                                    await ws.send_json({"type": "friend_follow", "to": parts[1]})
                                elif cmd == "/friend_unfollow" and len(parts) >= 2:
                                    await ws.send_json({"type": "friend_unfollow", "to": parts[1]})
                                elif cmd == "/following_list":
                                    await ws.send_json({"type": "following_list"})
                                elif cmd == "/followers_list":
                                    await ws.send_json({"type": "followers_list"})

                                # 방/메시지
                                elif cmd == "/join" and len(parts) >= 2:
                                    room = parts[1]
                                    await ws.send_json({"type": "join", "room": room})
                                    current_room = room
                                elif cmd in ("/leave", "/part") and len(parts) >= 2:
                                    room = parts[1]
                                    await ws.send_json({"type": "leave", "room": room})
                                    if current_room == room:
                                        current_room = "lobby"
                                elif cmd in ("/say", "/msg") and len(parts) >= 3:
                                    room = parts[1]
                                    text = " ".join(parts[2:])
                                    await ws.send_json({"type": "msg", "room": room, "text": text})
                                elif cmd in ("/dm", "/whisper") and len(parts) >= 4:
                                    room = parts[1]
                                    to_user = parts[2]
                                    text = " ".join(parts[3:])
                                    await ws.send_json({"type": "room_dm", "room": room, "to": to_user, "text": text})

                                # 히스토리
                                elif cmd == "/history":
                                    room = parts[1] if len(parts) >= 2 else current_room
                                    limit = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 20
                                    before = None; after = None; fmt = "json"
                                    if "--before" in parts:
                                        try:
                                            before = parts[parts.index("--before") + 1]
                                        except Exception:
                                            print("[INFO] 사용법: /history <room> [limit] [--before ISO] [--after ISO] [--text]")
                                            continue
                                    if "--after" in parts:
                                        try:
                                            after = parts[parts.index("--after") + 1]
                                        except Exception:
                                            print("[INFO] 사용법: /history <room> [limit] [--before ISO] [--after ISO] [--text]")
                                            continue
                                    if "--text" in parts:
                                        fmt = "text"
                                    await ws.send_json({
                                        "type": "history",
                                        "room": room,
                                        "limit": limit,
                                        "before": before,
                                        "after": after,
                                        "format": fmt
                                    })

                                elif cmd == "/room":
                                    if len(parts) >= 2:
                                        current_room = parts[1]
                                        print(f"[INFO] 기본 전송 방 → '{current_room}'")
                                    else:
                                        print("[INFO] 사용법: /room <방이름>")

                                elif cmd in ("/help", "/h"):
                                    print("명령 목록:")
                                    print("  /register                      가입")
                                    print("  /friend_follow <user>          팔로우")
                                    print("  /friend_unfollow <user>        팔로우 해제")
                                    print("  /following_list                내가 팔로우")
                                    print("  /followers_list                나를 팔로우")
                                    print("  /join <room>                   방 입장")
                                    print("  /leave <room>                  방 나가기")
                                    print("  /say <room> <text>             방 메시지")
                                    print("  /dm <room> <user> <text>       방 DM")
                                    print("  /history <room> [limit] [--before ISO] [--after ISO] [--text]")
                                    print("  /room <room>                   기본 전송 방 변경")
                                    print("  /quit                          종료")

                                elif cmd == "/quit":
                                    await ws.close()
                                    return 1000

                                else:
                                    print("[INFO] 알 수 없는 명령입니다. /help 참고")

                            else:
                                # 일반 입력: 현재 기본 방으로 전송
                                await ws.send_json({"type": "msg", "room": current_room, "text": line})

                    except (KeyboardInterrupt, EOFError):
                        print("\n[INFO] 종료합니다…")
                        await ws.close()
                        return 1000
                    finally:
                        await reader_task

            except aiohttp.WSServerHandshakeError as e:
                print(f"[ERR] 핸드셰이크 실패: {e.status} {getattr(e, 'message', '')}")
                return None
            except aiohttp.ClientConnectorError as e:
                print(f"[ERR] 서버 연결 실패: {e}")
                return None
            except aiohttp.ClientConnectionError as e:
                print(f"[ERR] 연결 중 오류: {e}")
                return None

    # 재연결 루프(간단 백오프)
    backoff = [3, 5, 8, 13]
    idx = 0
    while True:
        code = await run_once()
        if code == 1000:
            break
        wait = backoff[min(idx, len(backoff)-1)]
        print(f"[INFO] {wait}초 후 재연결 시도… (Ctrl+C로 종료)")
        try:
            await asyncio.sleep(wait)
        except (KeyboardInterrupt, EOFError):
            print("\n[INFO] 종료합니다…")
            break
        idx += 1

def parse_args():
    p = argparse.ArgumentParser(description="JWT+WS Chat Client (secure login)")
    p.add_argument("--api", default=DEFAULT_API, help="API base URL (http[s]://host:port)")
    p.add_argument("--ws",  default=DEFAULT_WS,  help="WebSocket URL (ws[s]://host:port/ws)")
    p.add_argument("--username", default="master")
    p.add_argument("--password", default="secret")
    p.add_argument("--room", default="lobby", help="기본 전송 방")
    p.add_argument("--auto-register", action="store_true", help="미가입이면 자동 /register 후 로그인 재시도")
    return p.parse_args()

if __name__ == "__main__":
    args = parse_args()
    try:
        asyncio.run(
            interactive_chat(
                api_base=args.api,
                ws_url=args.ws,
                username=args.username,
                password=args.password,
                default_room=args.room,
                auto_register=args.auto_register,
            )
        )
    except KeyboardInterrupt:
        print("\n[INFO] 종료합니다…")
