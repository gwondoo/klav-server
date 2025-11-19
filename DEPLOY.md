# Klav Server 도커 배포 가이드

## 📋 사전 준비

### 1. PostgreSQL 데이터베이스 준비
외부 PostgreSQL 서버 (210.123.42.129:5432)에 데이터베이스가 준비되어 있어야 합니다:

```sql
-- PostgreSQL에 접속하여 실행
CREATE DATABASE klav;
CREATE USER klav WITH PASSWORD 'klav6568';
GRANT ALL PRIVILEGES ON DATABASE klav TO klav;
```

### 2. 환경 변수 설정
`.env` 파일이 올바르게 설정되어 있는지 확인:

```env
DATABASE_URL=postgresql+asyncpg://klav:klav6568@210.123.42.129:5432/klav
JWT_SECRET=your-super-secret-key-change-me
JWT_ALGORITHM=HS256
```

## 🚀 배포 단계

### Step 1: DB 초기화 (로컬에서 실행)

```bash
# 1. 패키지 설치 (처음 한 번만)
pip install -r requirements.txt

# 2. DB 테이블 초기화
python reset_db.py

# 3. (선택) 기존 JSON 데이터 마이그레이션
python migrate_to_postgres.py
```

### Step 2: 도커 이미지 빌드

```bash
# 이미지 빌드
docker build -t klav-server:latest .

# 또는 태그와 함께 빌드
docker build -t klav-server:v1.0.0 .
```

### Step 3: 도커 실행

#### 방법 1: docker run 사용

```bash
docker run -d \
  --name klav-server \
  -p 5000:5000 \
  --env-file .env \
  --restart unless-stopped \
  klav-server:latest
```

#### 방법 2: docker-compose 사용 (권장)

```bash
# 백그라운드 실행
docker-compose up -d

# 로그 확인
docker-compose logs -f

# 중지
docker-compose down
```

## 🔍 상태 확인

### 컨테이너 상태 확인
```bash
docker ps
docker-compose ps
```

### 로그 확인
```bash
# docker run으로 실행한 경우
docker logs -f klav-server

# docker-compose로 실행한 경우
docker-compose logs -f klav-server
```

### 헬스체크
```bash
curl http://localhost:5000/health
```

응답 예시:
```json
{
  "status": "healthy",
  "database": "connected"
}
```

### API 테스트
```bash
# 회원가입
curl -X POST http://localhost:5000/register \
  -H "Content-Type: application/json" \
  -d '{"username":"test1","password":"test123","nickname":"테스트유저"}'

# 로그인
curl -X POST http://localhost:5000/login \
  -H "Content-Type: application/json" \
  -d '{"username":"test1","password":"test123"}'
```

## 🔄 업데이트 및 재배포

### 코드 변경 후 재배포

```bash
# 1. 컨테이너 중지 및 삭제
docker-compose down

# 2. 이미지 재빌드
docker-compose build --no-cache

# 3. 컨테이너 재시작
docker-compose up -d
```

### 빠른 재시작 (코드 변경 시)

```bash
docker-compose restart
```

## 🐛 트러블슈팅

### 1. 데이터베이스 연결 실패

**증상:** `Service unhealthy: database connection failed`

**해결:**
```bash
# PostgreSQL 서버 연결 테스트
telnet 210.123.42.129 5432

# 또는
nc -zv 210.123.42.129 5432

# PostgreSQL 직접 접속 테스트
psql -h 210.123.42.129 -U klav -d klav -p 5432
```

### 2. 포트 충돌

**증상:** `port is already allocated`

**해결:**
```bash
# 5000 포트 사용 중인 프로세스 확인
lsof -i :5000

# 또는 다른 포트로 변경
docker run -p 5001:5000 ...
```

### 3. 컨테이너가 계속 재시작됨

**원인 확인:**
```bash
docker logs klav-server
```

**일반적인 원인:**
- DB 연결 정보 오류
- 환경 변수 누락
- 패키지 설치 실패

### 4. DB 테이블이 생성되지 않음

```bash
# 컨테이너 내부에서 직접 실행
docker exec -it klav-server python reset_db.py
```

## 📊 모니터링

### 리소스 사용량 확인
```bash
docker stats klav-server
```

### 컨테이너 내부 접속
```bash
docker exec -it klav-server bash
```

## 🔐 프로덕션 체크리스트

배포 전 확인사항:

- [ ] `.env`의 `JWT_SECRET`을 강력한 값으로 변경
- [ ] PostgreSQL 사용자 비밀번호 변경
- [ ] 방화벽 설정 (5000 포트 개방)
- [ ] HTTPS/SSL 설정 (리버스 프록시 사용 권장)
- [ ] 로그 로테이션 설정
- [ ] 백업 전략 수립
- [ ] 모니터링 설정 (Prometheus, Grafana 등)

## 🌐 프로덕션 배포 (Nginx 리버스 프록시)

### Nginx 설정 예시

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://localhost:5000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## 📦 Docker Hub에 푸시 (선택)

```bash
# 태그 지정
docker tag klav-server:latest your-username/klav-server:latest

# 로그인
docker login

# 푸시
docker push your-username/klav-server:latest

# 다른 서버에서 풀
docker pull your-username/klav-server:latest
```

## 🔄 자동 배포 (GitHub Actions 예시)

`.github/workflows/deploy.yml`:

```yaml
name: Deploy to Production

on:
  push:
    branches: [ main ]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v2
    
    - name: Build Docker image
      run: docker build -t klav-server:latest .
    
    - name: Deploy to server
      run: |
        # SSH로 서버 접속하여 배포
        # 실제 배포 스크립트 작성 필요
```

## 📝 유지보수

### 정기 백업
```bash
# PostgreSQL 백업
pg_dump -h 210.123.42.129 -U klav -d klav > backup_$(date +%Y%m%d).sql

# 복원
psql -h 210.123.42.129 -U klav -d klav < backup_20241120.sql
```

### 로그 확인 및 정리
```bash
# 로그 크기 확인
docker logs klav-server | wc -l

# 로그 파일 정리 (선택)
docker logs klav-server --tail 1000 > recent_logs.txt
```
