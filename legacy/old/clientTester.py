import asyncio
import aiohttp
import requests
import argparse
import shlex
from typing import Optional

# ---- 기본 설정 ----
DEFAULT_API = "http://127.0.0.1:5000"
DEFAULT_WS  = "ws://127.0.0.1:5000/ws"

def get_jwt(api_base: str, username: str, password: str) -> str:
    r = requests.post(
        f"{api_base}/login",
        json={"username": username, "password": password},
        timeout=5,
    )
    r.raise_for_status()
    tok = r.json()["access_token"]
    print(f"[INFO] 토큰 발급 성공 (앞부분): {tok[:24]}...")
    return tok

async def interactive_chat(api_base: str, ws_url: str, username: str, password: str,
                           default_room: str = "lobby"):
    """
    서버 특성:
      - 연결 종료 ≠ 방 탈퇴 (멤버십은 서버가 유지)
      - 수신자가 오프라인이면 DM을 오프라인 큐에 저장, 재접속 시 flush
    이 클라이언트:
      - 매 연결 시 새 JWT 발급(만료 이슈 회피)
      - 연결 끊기면 3→5→8→13초 백오프로 자동 재연결
    """
    current_room = default_room  # 기본 전송 방 상태

    async def run_once() -> Optional[int]:
        nonlocal current_room  # ✅ 수정: 바깥 스코프의 current_room을 재할당하기 위해 선언
        token = get_jwt(api_base, username, password)
        headers = {"Authorization": f"Bearer {token}"}

        async with aiohttp.ClientSession() as session:
            try:
                async with session.ws_connect(
                    ws_url,
                    headers=headers,
                    heartbeat=30,   # ping/pong keepalive
                    timeout=aiohttp.ClientTimeout(total=None),
                ) as ws:
                    print("[INFO] WebSocket 연결됨.")
                    print("명령 예시:")
                    print("  /join lobby")
                    print("  /say lobby 안녕하세요")
                    print("  /dm lobby alice 비밀 메시지")
                    print("  /leave lobby")
                    print("  /my_rooms")
                    print("  /room <이름>   (기본 전송 방 바꾸기)")
                    print("  /help, /quit")
                    print(f"[INFO] 기본 전송 방: {current_room}")

                    async def reader():
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                print("[RECV]", msg.data)
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                print("[ERR]", ws.exception())
                                break

                    reader_task = asyncio.create_task(reader())
                    loop = asyncio.get_running_loop()

                    try:
                        while True:
                            line = await loop.run_in_executor(None, input, f"{username}@{current_room}> ")
                            line = line.strip()
                            if not line:
                                continue

                            # 슬래시 명령 처리
                            if line.startswith("/"):
                                parts = shlex.split(line)
                                cmd = parts[0].lower()

                                if cmd == "/join" and len(parts) >= 2:
                                    room = parts[1]
                                    await ws.send_json({"type": "join", "room": room})
                                    current_room = room  # 기본 전송 방도 함께 이동

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
                                    await ws.send_json({
                                        "type": "room_dm",
                                        "room": room,
                                        "to": to_user,
                                        "text": text
                                    })

                                elif cmd in ("/my_rooms", "/rooms"):
                                    await ws.send_json({"type": "my_rooms"})

                                elif cmd == "/room":
                                    if len(parts) >= 2:
                                        current_room = parts[1]   # ✅ 수정: 바로 재할당
                                        print(f"[INFO] 기본 전송 방 → '{current_room}'")
                                    else:
                                        print("[INFO] 사용법: /room <방이름>")

                                elif cmd in ("/help", "/h"):
                                    print("명령 목록:")
                                    print("  /join <room>                방 입장(멤버십 추가)")
                                    print("  /leave <room>               방 나가기(멤버십 제거)")
                                    print("  /say <room> <text>          방 전체 메시지")
                                    print("  /dm <room> <user> <text>    방 내 특정 사용자 DM (오프라인이면 큐)")
                                    print("  /my_rooms                   내가 가입한 방 목록")
                                    print("  /room <room>                기본 전송 방 변경")
                                    print("  /quit                       종료")

                                elif cmd == "/quit":
                                    await ws.close()
                                    return 1000  # 정상 종료
                                
                                elif cmd == "/history":
    # /history <room> [limit] [--before ISO] [--after ISO] [--text]
    # 예: /history lobby 50 --before 2025-10-10T04:30:00Z
                                    room = parts[1] if len(parts) >= 2 else "lobby"
                                    limit = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 20
    # 옵션 파싱
                                    before = None; after = None; fmt = "text" if "--text" in parts else "json"
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
                                    await ws.send_json({
                                        "type": "history",
                                        "room": room,
                                        "limit": limit,
                                        "before": before,
                                        "after": after,
                                        "format": fmt  # 기본 json, 텍스트 원하면 --text
                                    })

                                

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
                print(f"[ERR] 핸드셰이크 실패: {e.status} {e.message}")
                return None
            except aiohttp.ClientConnectorError as e:
                print(f"[ERR] 서버에 연결할 수 없습니다: {e}")
                return None
            except aiohttp.ClientConnectionError as e:
                print(f"[ERR] 연결 중 오류: {e}")
                return None

    # --- 재연결 루프 ---
    backoff = [3, 5, 8, 13]  # 초 단위
    idx = 0
    while True:
        code = await run_once()
        if code == 1000:
            break  # 정상 종료
        wait = backoff[min(idx, len(backoff) - 1)]
        print(f"[INFO] {wait}초 후 재연결 시도...")
        try:
            await asyncio.sleep(wait)
        except (KeyboardInterrupt, EOFError):
            print("\n[INFO] 종료합니다…")
            break
        idx += 1

def parse_args():
    p = argparse.ArgumentParser(description="Persistent-membership chat client")
    p.add_argument("--api", default=DEFAULT_API, help="API base (login) URL")
    p.add_argument("--ws",  default=DEFAULT_WS,  help="WebSocket URL")
    p.add_argument("--username", default="master")
    p.add_argument("--password", default="secret")
    p.add_argument("--room", default="lobby", help="기본 전송 방")
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
            )
        )
    except KeyboardInterrupt:
        print("\n[INFO] 종료합니다…")
