"""
JSON 파일 데이터를 PostgreSQL로 마이그레이션하는 스크립트

사용법:
    python migrate_to_postgres.py

주의:
    - 기존 DB 데이터는 모두 삭제됩니다
    - JSON 파일이 같은 디렉토리에 있어야 합니다:
      * users.json
      * chat_state.json
      * friends_state.json
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from database import init_db, get_db, Base, engine
from models import User, Room, RoomMember, ChatLog, Follow
from sqlalchemy import delete

def parse_iso_safe(ts_str):
    """ISO 형식 문자열을 datetime으로 변환"""
    if not ts_str:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except:
        return datetime.now(timezone.utc)

async def clear_all_tables():
    """모든 테이블 데이터 삭제"""
    print("🗑️  기존 데이터 삭제 중...")
    async with get_db() as db:
        await db.execute(delete(ChatLog))
        await db.execute(delete(RoomMember))
        await db.execute(delete(Follow))
        await db.execute(delete(Room))
        await db.execute(delete(User))
        await db.commit()
    print("✅ 기존 데이터 삭제 완료")

async def migrate_users():
    """사용자 데이터 마이그레이션"""
    if not os.path.exists("users.json"):
        print("⚠️  users.json 파일을 찾을 수 없습니다")
        return
    
    print("\n👤 사용자 데이터 마이그레이션 중...")
    
    with open("users.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    
    users_list = data.get("users", [])
    user_info = data.get("userinfo", {})
    
    async with get_db() as db:
        count = 0
        for username in users_list:
            info = user_info.get(username, {})
            
            user = User(
                username=username,
                password=info.get("password", "default"),
                nickname=info.get("nickname", username),
                extra=info.get("extra", ""),
                created_at=datetime.now(timezone.utc)
            )
            db.add(user)
            count += 1
        
        await db.commit()
    
    print(f"✅ {count}명의 사용자 마이그레이션 완료")

async def migrate_rooms_and_messages():
    """채팅방 및 메시지 데이터 마이그레이션"""
    if not os.path.exists("chat_state.json"):
        print("⚠️  chat_state.json 파일을 찾을 수 없습니다")
        return
    
    print("\n💬 채팅방 및 메시지 데이터 마이그레이션 중...")
    
    with open("chat_state.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    
    room_members = data.get("room_members", {})
    chat_logs = data.get("chat_logs", {})
    room_infos = data.get("room_infos", {})
    
    async with get_db() as db:
        room_count = 0
        message_count = 0
        
        # 방 정보가 있는 경우 (최신 포맷)
        if room_infos:
            for room_id, info in room_infos.items():
                # 방 생성
                last = info.get("last")
                room = Room(
                    id=room_id,
                    name=info.get("name", room_id),
                    created_at=parse_iso_safe(info.get("created_at")),
                    last_message_text=last.get("text") if last else None,
                    last_message_from=last.get("from") if last else None,
                    last_message_kind=last.get("kind") if last else None,
                    last_message_ts=parse_iso_safe(last.get("ts")) if last and last.get("ts") else None
                )
                db.add(room)
                room_count += 1
                
                # 멤버 추가
                members = room_members.get(room_id, [])
                for member in members:
                    room_member = RoomMember(
                        room_id=room_id,
                        username=member,
                        joined_at=datetime.now(timezone.utc)
                    )
                    db.add(room_member)
                
                # 메시지 추가
                logs = chat_logs.get(room_id, [])
                for log in logs:
                    chat_log = ChatLog(
                        room_id=room_id,
                        ts=parse_iso_safe(log.get("ts")),
                        kind=log.get("kind", "msg"),
                        from_user=log.get("from", "system"),
                        from_nickname=log.get("from_nickname", log.get("from", "system")),
                        to_user=log.get("to"),
                        text=log.get("text", "")
                    )
                    db.add(chat_log)
                    message_count += 1
        
        await db.commit()
    
    print(f"✅ {room_count}개 방, {message_count}개 메시지 마이그레이션 완료")

async def migrate_follows():
    """친구 관계 데이터 마이그레이션"""
    if not os.path.exists("friends_state.json"):
        print("⚠️  friends_state.json 파일을 찾을 수 없습니다")
        return
    
    print("\n👥 친구 관계 데이터 마이그레이션 중...")
    
    with open("friends_state.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    
    following = data.get("following", {})
    
    async with get_db() as db:
        count = 0
        for follower, followees in following.items():
            for followee in followees:
                follow = Follow(
                    follower_username=follower,
                    followee_username=followee,
                    created_at=datetime.now(timezone.utc)
                )
                db.add(follow)
                count += 1
        
        await db.commit()
    
    print(f"✅ {count}개 친구 관계 마이그레이션 완료")

async def verify_migration():
    """마이그레이션 결과 확인"""
    print("\n📊 마이그레이션 결과 확인:")
    
    async with get_db() as db:
        from sqlalchemy import select, func
        
        user_count = await db.scalar(select(func.count()).select_from(User))
        room_count = await db.scalar(select(func.count()).select_from(Room))
        message_count = await db.scalar(select(func.count()).select_from(ChatLog))
        follow_count = await db.scalar(select(func.count()).select_from(Follow))
        member_count = await db.scalar(select(func.count()).select_from(RoomMember))
    
    print(f"  - 사용자: {user_count}명")
    print(f"  - 채팅방: {room_count}개")
    print(f"  - 메시지: {message_count}개")
    print(f"  - 방 멤버십: {member_count}개")
    print(f"  - 친구 관계: {follow_count}개")

async def main():
    print("=" * 60)
    print("JSON → PostgreSQL 마이그레이션 시작")
    print("=" * 60)
    
    # 테이블 생성
    print("\n🔧 데이터베이스 테이블 초기화 중...")
    await init_db()
    print("✅ 테이블 초기화 완료")
    
    # 기존 데이터 삭제
    await clear_all_tables()
    
    # 마이그레이션 실행
    try:
        await migrate_users()
        await migrate_rooms_and_messages()
        await migrate_follows()
        
        # 결과 확인
        await verify_migration()
        
        print("\n" + "=" * 60)
        print("✨ 마이그레이션 완료!")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ 마이그레이션 실패: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
