# Klav Server (PostgreSQL)

FastAPI + WebSocket 기반 실시간 채팅 서버 (PostgreSQL 연동)

## 구조

```
klav-server/
├── data.py              # 데이터 모델 (Pydantic)
├── serverHelper.py      # 유틸리티 함수들
├── database.py          # DB 연결 설정
├── models.py            # SQLAlchemy ORM 모델
├── serverPostgres.py    # 메인 서버 (PostgreSQL)
├── testKlavServer3.py   # 기존 서버 (JSON 파일)
├── requirements.txt     # 패키지 의존성
└── .env                 # 환경변수 설정
```

## 주요 기능

### 1. 인증
- JWT 기반 로그인/회원가입
- 토큰 기반 WebSocket 인증

### 2. 채팅방
- 채팅방 생성, 참가, 나가기
- 방별 채팅 히스토리 조회
- 최근 메시지 기준 방 목록 정렬

### 3. 메시징
- 일반 메시지 (broadcast)
- DM (Direct Message)
- 오프라인 DM 큐잉

### 4. 친구 시스템
- 단방향 팔로우/언팔로우
- 친구 목록 조회
- 온라인 친구 실시간 확인

### 5. 실시간 기능
- WebSocket 기반 실시간 메시지
- 온라인/오프라인 상태 변화 알림
- Presence 구독

## 설치 및 실행

### 1. 패키지 설치

```bash
pip install -r requirements.txt
```

### 2. PostgreSQL 설정

`.env` 파일에서 데이터베이스 연결 정보를 확인/수정:

```env
DATABASE_URL=postgresql+asyncpg://klav:klav6568@210.123.42.129:5432/klav
```

### 3. 데이터베이스 테이블 생성

서버를 처음 실행하면 자동으로 테이블이 생성됩니다.

또는 수동으로 생성하려면:

```python
from database import init_db
import asyncio

asyncio.run(init_db())
```

### 4. 서버 실행

```bash
# PostgreSQL 버전 실행
python serverPostgres.py

# 또는
uvicorn serverPostgres:app --host 0.0.0.0 --port 5000 --reload
```

서버는 `http://localhost:5000`에서 실행됩니다.

## API 엔드포인트

### REST API

- `POST /register` - 회원가입
  ```json
  {
    "username": "user1",
    "password": "pass123",
    "nickname": "사용자1"
  }
  ```

- `POST /login` - 로그인
  ```json
  {
    "username": "user1",
    "password": "pass123"
  }
  ```
  응답:
  ```json
  {
    "access_token": "eyJ...",
    "token_type": "bearer",
    "expires_in_minutes": 60
  }
  ```

### WebSocket

WebSocket 연결: `ws://localhost:5000/ws`

**헤더:**
```
Authorization: Bearer <access_token>
```

**메시지 타입:**

1. **방 생성**
   ```json
   {
     "type": "create_room",
     "name": "일반 대화방"
   }
   ```

2. **방 참가**
   ```json
   {
     "type": "join",
     "room_id": "r_abc12345"
   }
   ```

3. **메시지 전송**
   ```json
   {
     "type": "msg",
     "room_id": "r_abc12345",
     "text": "안녕하세요!"
   }
   ```

4. **DM 전송**
   ```json
   {
     "type": "room_dm",
     "room_id": "r_abc12345",
     "to": "user2",
     "text": "비밀 메시지"
   }
   ```

5. **내 방 목록 조회**
   ```json
   {
     "type": "my_rooms"
   }
   ```

6. **히스토리 조회**
   ```json
   {
     "type": "history",
     "room_id": "r_abc12345",
     "limit": 50
   }
   ```

7. **친구 팔로우**
   ```json
   {
     "type": "friend_follow",
     "to": "user2"
   }
   ```

8. **온라인 친구 조회**
   ```json
   {
     "type": "get_online_friends"
   }
   ```

9. **Presence 구독**
   ```json
   {
     "type": "presence_friends_subscribe"
   }
   ```

## 데이터베이스 스키마

### Users (사용자)
- username (PK)
- password
- nickname
- extra
- created_at

### Rooms (채팅방)
- id (PK)
- name
- created_at
- last_message_text
- last_message_from
- last_message_kind
- last_message_ts

### RoomMembers (방 멤버십)
- id (PK)
- room_id (FK)
- username (FK)
- joined_at

### ChatLogs (채팅 로그)
- id (PK)
- room_id (FK)
- ts (timestamp)
- kind (msg/dm/system)
- from_user
- from_nickname
- to_user (DM인 경우)
- text

### Follows (친구 관계)
- id (PK)
- follower_username (FK)
- followee_username (FK)
- created_at

## 기존 JSON 서버에서 마이그레이션

기존 `testKlavServer3.py`의 JSON 파일 데이터를 PostgreSQL로 마이그레이션하려면:

1. 기존 JSON 파일 백업:
   - `chat_state.json`
   - `users.json`
   - `friends_state.json`

2. 마이그레이션 스크립트 작성 (예정)

## 주의사항

1. **비밀번호 보안**: 현재는 평문으로 저장됩니다. 프로덕션에서는 bcrypt 등으로 해싱 필요
2. **JWT Secret**: `.env`의 `JWT_SECRET`을 강력한 값으로 변경 필요
3. **오프라인 DM**: 메모리에만 저장되므로 서버 재시작 시 사라짐
4. **로그 제한**: 방별 최대 1000개 로그 보관 (설정 변경 가능)

## 개발 팁

### 데이터베이스 직접 접속
```bash
psql -h 210.123.42.129 -U klav -d klav -p 5432
```

### 테이블 확인
```sql
\dt              -- 테이블 목록
\d users         -- users 테이블 스키마
SELECT * FROM users LIMIT 10;
```

### 로그 레벨 조정
`database.py`에서:
```python
engine = create_async_engine(
    DATABASE_URL,
    echo=True,  # SQL 쿼리 로그 출력
    ...
)
```

## 라이센스

MIT License
