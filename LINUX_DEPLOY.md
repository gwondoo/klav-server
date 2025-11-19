# 리눅스 서버 배포 완전 가이드

## 🎯 전체 흐름

```
로컬 (Mac) → 파일 전송 → 리눅스 서버 → 기존 종료 → 새로 배포
```

## 1️⃣ 로컬에서 파일 준비 (Mac)

### Git 사용 (권장)
```bash
cd /Users/user/Downloads/klav-server
git add .
git commit -m "Add PostgreSQL and Docker deployment"
git push origin main
```

### 직접 전송 준비
```bash
cd /Users/user/Downloads/klav-server

# 필요한 파일만 압축
tar -czf klav-server.tar.gz \
  *.py *.md *.txt *.yml *.sh \
  Dockerfile .dockerignore .env \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.git' \
  --exclude='아카이브.zip' \
  --exclude='testKlavServer3.py'

# 서버로 전송
scp klav-server.tar.gz user@your-server-ip:/home/user/
```

## 2️⃣ 리눅스 서버 접속

```bash
ssh user@your-server-ip
```

## 3️⃣ 기존 컨테이너 완전 종료

```bash
# 현재 실행 중인 컨테이너 확인
docker ps

# 모든 klav 관련 컨테이너 찾기
docker ps -a | grep klav

# 방법 1: 이름으로 종료
docker stop klav-server
docker rm klav-server

# 방법 2: 모든 klav 관련 컨테이너 강제 종료
docker ps -a | grep klav | awk '{print $1}' | xargs docker rm -f

# 방법 3: docker-compose로 배포된 경우
cd /기존/디렉토리
docker-compose down

# 사용하지 않는 리소스 정리
docker system prune -f

# (선택) 기존 이미지도 삭제
docker images | grep klav-server
docker rmi klav-server:latest
```

## 4️⃣ 새 파일 배치

### Git으로 받은 경우
```bash
cd /home/user/klav-server
git pull origin main
```

### 압축 파일로 전송한 경우
```bash
# 작업 디렉토리 생성
mkdir -p /home/user/klav-server
cd /home/user/klav-server

# 압축 해제
tar -xzf ../klav-server.tar.gz

# 실행 권한 부여
chmod +x *.sh
```

## 5️⃣ 환경 확인 및 설정

```bash
# Python 확인
python3 --version
pip3 --version

# Docker 확인
docker --version
docker-compose --version

# .env 파일 확인
cat .env | grep DATABASE_URL

# PostgreSQL 연결 테스트
telnet 210.123.42.129 5432
# 또는
nc -zv 210.123.42.129 5432
```

## 6️⃣ 자동 배포 실행

```bash
cd /home/user/klav-server

# 전체 자동 배포
chmod +x full_deploy.sh
./full_deploy.sh
```

이 스크립트가 자동으로 처리하는 것:
- ✅ 기존 컨테이너 종료
- ✅ Python 패키지 설치
- ✅ DB 초기화 (선택)
- ✅ 데이터 마이그레이션 (선택)
- ✅ Docker 이미지 빌드
- ✅ 컨테이너 시작
- ✅ 헬스체크

## 7️⃣ 수동 배포 (단계별)

자동 스크립트 대신 수동으로 실행하려면:

```bash
cd /home/user/klav-server

# 1. 패키지 설치
pip3 install -r requirements.txt

# 2. DB 초기화 (선택)
python3 reset_db.py

# 3. 데이터 마이그레이션 (선택 - JSON 파일이 있는 경우)
python3 migrate_to_postgres.py

# 4. Docker 이미지 빌드
docker build -t klav-server:latest .

# 5. 컨테이너 실행
# 방법 A: docker-compose
docker-compose up -d

# 방법 B: docker run
docker run -d \
  --name klav-server \
  -p 5000:5000 \
  --env-file .env \
  --restart unless-stopped \
  klav-server:latest

# 6. 로그 확인
docker logs -f klav-server

# 7. 헬스체크
curl http://localhost:5000/health
```

## 8️⃣ 배포 확인

```bash
# 컨테이너 상태
docker ps

# 로그 확인
docker logs klav-server
docker logs -f klav-server  # 실시간

# 헬스체크
curl http://localhost:5000/health

# 응답 예시:
# {
#   "status": "healthy",
#   "database": "connected"
# }

# API 테스트
curl -X POST http://localhost:5000/register \
  -H "Content-Type: application/json" \
  -d '{"username":"test1","password":"test123","nickname":"테스트"}'

# 서버 외부에서 접속 (방화벽 열려있는 경우)
curl http://서버IP주소:5000/health
```

## 9️⃣ 포트 열기 (필요한 경우)

```bash
# Ubuntu/Debian - UFW
sudo ufw allow 5000/tcp
sudo ufw status

# CentOS/RHEL - firewalld
sudo firewall-cmd --permanent --add-port=5000/tcp
sudo firewall-cmd --reload

# 직접 iptables
sudo iptables -A INPUT -p tcp --dport 5000 -j ACCEPT
sudo iptables-save
```

## 🔄 운영 명령어

### 일상적인 관리
```bash
# 로그 확인
docker logs -f klav-server --tail 100

# 재시작
docker restart klav-server

# 중지/시작
docker stop klav-server
docker start klav-server

# 컨테이너 내부 접속
docker exec -it klav-server bash

# 리소스 사용량
docker stats klav-server
```

### 업데이트 배포
```bash
cd /home/user/klav-server

# Git으로 최신 코드 받기
git pull origin main

# 재빌드 및 재시작
docker-compose down
docker-compose build --no-cache
docker-compose up -d

# 또는
docker stop klav-server
docker rm klav-server
docker build -t klav-server:latest .
docker run -d --name klav-server -p 5000:5000 --env-file .env klav-server:latest
```

## 🐛 트러블슈팅

### 문제 1: 포트 이미 사용 중
```bash
# 5000 포트 사용 중인 프로세스 확인
sudo lsof -i :5000
sudo netstat -tlnp | grep 5000

# 프로세스 종료
sudo kill -9 <PID>
```

### 문제 2: DB 연결 실패
```bash
# PostgreSQL 연결 테스트
psql -h 210.123.42.129 -U klav -d klav -p 5432

# 연결 안 되면 방화벽 확인
telnet 210.123.42.129 5432
```

### 문제 3: 컨테이너가 계속 재시작됨
```bash
# 로그 확인
docker logs klav-server --tail 100

# 환경 변수 확인
docker exec klav-server env | grep DATABASE
```

### 문제 4: 이미지 빌드 실패
```bash
# 캐시 없이 재빌드
docker build --no-cache -t klav-server:latest .

# 빌드 로그 상세히 보기
docker build -t klav-server:latest . --progress=plain
```

## 📊 모니터링

### 시스템 리소스
```bash
# CPU, 메모리 사용량
docker stats klav-server

# 디스크 사용량
docker system df

# 로그 크기 확인
docker inspect --format='{{.LogPath}}' klav-server
ls -lh $(docker inspect --format='{{.LogPath}}' klav-server)
```

### 로그 관리
```bash
# 로그 파일 위치 확인
docker inspect klav-server | grep LogPath

# 로그 크기 제한 설정 (docker-compose.yml)
logging:
  driver: "json-file"
  options:
    max-size: "10m"
    max-file: "3"
```

## 🔒 보안 체크리스트

- [ ] .env 파일 권한 (chmod 600 .env)
- [ ] JWT_SECRET 변경
- [ ] PostgreSQL 비밀번호 강화
- [ ] 방화벽 설정 (필요한 포트만 개방)
- [ ] SSL/TLS 설정 (Nginx 리버스 프록시)
- [ ] 정기 백업 설정

## 🎯 빠른 참조

```bash
# 기존 종료
docker stop klav-server && docker rm klav-server

# 빌드 & 실행
docker build -t klav-server . && docker run -d --name klav-server -p 5000:5000 --env-file .env klav-server:latest

# 로그
docker logs -f klav-server

# 재시작
docker restart klav-server

# 완전 재배포
./full_deploy.sh
```
