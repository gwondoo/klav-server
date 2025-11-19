#!/bin/bash

# Klav Server 배포 스크립트
# 사용법: ./deploy.sh [init|build|start|stop|restart|logs|status]

set -e

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 로그 함수
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# DB 초기화 함수
init_db() {
    log_info "데이터베이스 초기화 중..."
    
    if [ ! -f ".env" ]; then
        log_error ".env 파일이 없습니다!"
        exit 1
    fi
    
    log_info "Python 패키지 설치..."
    pip install -r requirements.txt
    
    log_info "DB 테이블 초기화..."
    python reset_db.py
    
    log_warn "마이그레이션을 실행하시겠습니까? (y/n)"
    read -r response
    if [[ "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
        if [ -f "users.json" ] || [ -f "chat_state.json" ]; then
            log_info "JSON 데이터 마이그레이션 중..."
            python migrate_to_postgres.py
        else
            log_warn "JSON 파일을 찾을 수 없습니다. 마이그레이션을 건너뜁니다."
        fi
    fi
    
    log_info "데이터베이스 초기화 완료!"
}

# 도커 이미지 빌드
build_image() {
    log_info "도커 이미지 빌드 중..."
    docker build -t klav-server:latest .
    log_info "이미지 빌드 완료!"
}

# 컨테이너 시작
start_container() {
    log_info "컨테이너 시작 중..."
    
    if docker ps -a | grep -q klav-server; then
        log_warn "기존 컨테이너가 존재합니다. 삭제 후 재시작합니다."
        docker rm -f klav-server
    fi
    
    if [ -f "docker-compose.yml" ]; then
        docker-compose up -d
    else
        docker run -d \
            --name klav-server \
            -p 5000:5000 \
            --env-file .env \
            --restart unless-stopped \
            klav-server:latest
    fi
    
    log_info "컨테이너 시작 완료!"
    log_info "헬스체크 대기 중..."
    sleep 5
    check_health
}

# 컨테이너 중지
stop_container() {
    log_info "컨테이너 중지 중..."
    
    if [ -f "docker-compose.yml" ]; then
        docker-compose down
    else
        docker stop klav-server
        docker rm klav-server
    fi
    
    log_info "컨테이너 중지 완료!"
}

# 컨테이너 재시작
restart_container() {
    log_info "컨테이너 재시작 중..."
    stop_container
    start_container
}

# 로그 확인
show_logs() {
    if [ -f "docker-compose.yml" ]; then
        docker-compose logs -f
    else
        docker logs -f klav-server
    fi
}

# 상태 확인
check_status() {
    log_info "컨테이너 상태 확인..."
    
    if [ -f "docker-compose.yml" ]; then
        docker-compose ps
    else
        docker ps -a | grep klav-server
    fi
    
    echo ""
    check_health
}

# 헬스체크
check_health() {
    log_info "헬스체크 실행..."
    
    response=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:5000/health)
    
    if [ "$response" == "200" ]; then
        log_info "✅ 서버가 정상 동작 중입니다!"
        curl -s http://localhost:5000/health | python -m json.tool
    else
        log_error "❌ 서버가 응답하지 않습니다 (HTTP $response)"
        exit 1
    fi
}

# 전체 배포 (초기화 + 빌드 + 시작)
full_deploy() {
    log_info "=== 전체 배포 시작 ==="
    
    log_warn "DB를 초기화하시겠습니까? (y/n)"
    read -r response
    if [[ "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
        init_db
    fi
    
    build_image
    start_container
    
    log_info "=== 배포 완료! ==="
}

# 메인 로직
case "${1:-help}" in
    init)
        init_db
        ;;
    build)
        build_image
        ;;
    start)
        start_container
        ;;
    stop)
        stop_container
        ;;
    restart)
        restart_container
        ;;
    logs)
        show_logs
        ;;
    status)
        check_status
        ;;
    health)
        check_health
        ;;
    deploy)
        full_deploy
        ;;
    help|*)
        echo "Klav Server 배포 스크립트"
        echo ""
        echo "사용법: ./deploy.sh [명령어]"
        echo ""
        echo "명령어:"
        echo "  init      - DB 초기화 및 마이그레이션"
        echo "  build     - 도커 이미지 빌드"
        echo "  start     - 컨테이너 시작"
        echo "  stop      - 컨테이너 중지"
        echo "  restart   - 컨테이너 재시작"
        echo "  logs      - 로그 확인 (실시간)"
        echo "  status    - 컨테이너 상태 확인"
        echo "  health    - 헬스체크"
        echo "  deploy    - 전체 배포 (init + build + start)"
        echo "  help      - 도움말 표시"
        echo ""
        echo "예시:"
        echo "  ./deploy.sh deploy    # 처음 배포 시"
        echo "  ./deploy.sh restart   # 코드 변경 후 재배포"
        echo "  ./deploy.sh logs      # 로그 확인"
        ;;
esac
