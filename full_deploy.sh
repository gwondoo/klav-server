#!/bin/bash

# 리눅스 서버에서 실행할 배포 스크립트
# 사용법: ./full_deploy.sh

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log() { echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

log "========================================"
log "Klav Server 전체 배포 시작"
log "========================================"

# 1. 기존 컨테이너 종료
log "Step 1: 기존 컨테이너 확인 및 종료"
if docker ps -a | grep -q klav; then
    warn "기존 klav 컨테이너 발견. 종료합니다..."
    docker ps -a | grep klav | awk '{print $1}' | xargs docker rm -f || true
    log "기존 컨테이너 종료 완료"
else
    log "실행 중인 klav 컨테이너 없음"
fi

# 2. 기존 이미지 삭제 (선택)
log "Step 2: 기존 이미지 정리"
if docker images | grep -q klav-server; then
    warn "기존 klav-server 이미지를 삭제하시겠습니까? (y/n)"
    read -r response
    if [[ "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
        docker rmi klav-server:latest || true
        log "기존 이미지 삭제 완료"
    fi
fi

# 3. Python 환경 확인
log "Step 3: Python 환경 확인"
if ! command -v python3 &> /dev/null; then
    error "Python3가 설치되어 있지 않습니다!"
    exit 1
fi
if ! command -v pip3 &> /dev/null; then
    error "pip3가 설치되어 있지 않습니다!"
    exit 1
fi
log "Python $(python3 --version) 확인 완료"

# 4. .env 파일 확인
log "Step 4: 환경 설정 확인"
if [ ! -f ".env" ]; then
    error ".env 파일이 없습니다!"
    exit 1
fi
log ".env 파일 확인 완료"

# 5. Docker 설치 확인
log "Step 5: Docker 확인"
if ! command -v docker &> /dev/null; then
    error "Docker가 설치되어 있지 않습니다!"
    exit 1
fi
log "Docker $(docker --version) 확인 완료"

# 6. DB 초기화 여부 확인
log "Step 6: 데이터베이스 초기화"
warn "데이터베이스를 초기화하시겠습니까? (기존 데이터 삭제) (y/n)"
read -r response
if [[ "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
    log "패키지 설치 중..."
    pip3 install -r requirements.txt
    
    log "DB 초기화 중..."
    python3 reset_db.py
    
    # 마이그레이션 확인
    if [ -f "users.json" ] || [ -f "chat_state.json" ]; then
        warn "JSON 파일을 발견했습니다. 마이그레이션을 실행하시겠습니까? (y/n)"
        read -r migrate_response
        if [[ "$migrate_response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
            log "데이터 마이그레이션 중..."
            python3 migrate_to_postgres.py
        fi
    fi
else
    log "DB 초기화를 건너뜁니다"
fi

# 7. 도커 이미지 빌드
log "Step 7: Docker 이미지 빌드"
docker build -t klav-server:latest .
log "이미지 빌드 완료"

# 8. 컨테이너 실행
log "Step 8: 컨테이너 실행"
if [ -f "docker-compose.yml" ]; then
    log "docker-compose 사용"
    docker-compose up -d
else
    log "docker run 사용"
    docker run -d \
        --name klav-server \
        -p 5000:5000 \
        --env-file .env \
        --restart unless-stopped \
        klav-server:latest
fi
log "컨테이너 시작 완료"

# 9. 헬스체크
log "Step 9: 헬스체크 대기 (10초)"
sleep 10

if curl -s -f http://localhost:5000/health > /dev/null 2>&1; then
    log "✅ 서버가 정상적으로 시작되었습니다!"
    curl -s http://localhost:5000/health | python3 -m json.tool
else
    error "❌ 서버 시작 실패. 로그를 확인하세요:"
    docker logs klav-server --tail 50
    exit 1
fi

# 10. 상태 확인
log "Step 10: 최종 상태"
docker ps | grep klav

log "========================================"
log "배포 완료! 🎉"
log "========================================"
log "서버 주소: http://$(hostname -I | awk '{print $1}'):5000"
log ""
log "유용한 명령어:"
log "  docker logs -f klav-server      # 로그 확인"
log "  docker restart klav-server      # 재시작"
log "  docker stop klav-server         # 중지"
log "  curl http://localhost:5000/health  # 헬스체크"
