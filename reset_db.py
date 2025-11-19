"""
PostgreSQL 데이터베이스 초기화 스크립트
모든 테이블을 삭제하고 다시 생성합니다.

사용법:
    python reset_db.py
"""

import asyncio
from database import engine, Base, init_db
from models import User, Room, RoomMember, ChatLog, Follow

async def reset_database():
    print("=" * 60)
    print("🗑️  데이터베이스 초기화 시작")
    print("=" * 60)
    
    # 모든 테이블 삭제
    print("\n⚠️  모든 테이블 삭제 중...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    print("✅ 테이블 삭제 완료")
    
    # 테이블 재생성
    print("\n🔧 테이블 재생성 중...")
    await init_db()
    print("✅ 테이블 생성 완료")
    
    print("\n" + "=" * 60)
    print("✨ 데이터베이스 초기화 완료!")
    print("=" * 60)
    print("\n다음 단계:")
    print("  python migrate_to_postgres.py  # JSON 데이터 마이그레이션")
    print("  python serverPostgres.py       # 서버 실행")

if __name__ == "__main__":
    asyncio.run(reset_database())
