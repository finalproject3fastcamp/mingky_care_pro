#!/usr/bin/env bash

set -euo pipefail

DEPLOY_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${DEPLOY_DIR}/.env"
COMPOSE_FILE="${DEPLOY_DIR}/compose.yaml"

usage() {
  cat <<'EOF'
사용법:
  ./deploy/deploy.sh up       이미지 빌드 후 전체 서비스 실행
  ./deploy/deploy.sh down     서비스 종료 (DB 데이터 유지)
  ./deploy/deploy.sh restart  서비스 재시작
  ./deploy/deploy.sh migrate  운영 DB에 미적용 마이그레이션만 적용 (시드 제외)
  ./deploy/deploy.sh status   컨테이너 상태 확인
  ./deploy/deploy.sh logs     전체 로그 확인
EOF
}

require_docker() {
  command -v docker >/dev/null 2>&1 || {
    echo "[실패] Docker가 설치되어 있지 않습니다." >&2
    exit 1
  }
  docker compose version >/dev/null 2>&1 || {
    echo "[실패] Docker Compose 플러그인이 설치되어 있지 않습니다." >&2
    exit 1
  }
}

prepare_env() {
  if [[ -f "${ENV_FILE}" ]]; then
    return
  fi

  cp "${DEPLOY_DIR}/.env.example" "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
  echo "[설정] ${ENV_FILE} 파일을 만들었습니다."
  echo "[필수] POSTGRES_PASSWORD를 변경한 뒤 같은 명령을 다시 실행하세요."
  exit 1
}

compose() {
  docker compose \
    --env-file "${ENV_FILE}" \
    --file "${COMPOSE_FILE}" \
    "$@"
}

wait_for_backend() {
  local attempt
  for attempt in {1..60}; do
    if compose exec -T backend python -c \
      "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" \
      >/dev/null 2>&1; then
      return
    fi
    sleep 2
  done

  echo "[실패] FastAPI가 제한 시간 안에 준비되지 않았습니다." >&2
  compose logs --tail=100 backend postgres >&2
  exit 1
}

wait_for_frontend() {
  local attempt
  for attempt in {1..30}; do
    if compose exec -T frontend wget -q -O /dev/null http://127.0.0.1/ \
      >/dev/null 2>&1; then
      return
    fi
    sleep 2
  done

  echo "[실패] 대시보드가 제한 시간 안에 준비되지 않았습니다." >&2
  compose logs --tail=100 frontend backend >&2
  exit 1
}

print_urls() {
  local web_port server_ip
  web_port="$(sed -n 's/^WEB_PORT=//p' "${ENV_FILE}" | tail -1)"
  web_port="${web_port:-8080}"
  server_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  server_ip="${server_ip:-localhost}"

  cat <<EOF

[완료] 밍키케어 관제 서버가 실행 중입니다.
  의료진 화면:   http://${server_ip}:${web_port}/medical
  엔지니어 화면: http://${server_ip}:${web_port}/engineer
  API 상태:      http://${server_ip}:${web_port}/api/health

Pinky의 backend_url:
  http://${server_ip}:${web_port}/api
EOF
}

require_docker

command_name="${1:-}"
case "${command_name}" in
  up)
    prepare_env
    compose up --detach --build
    wait_for_backend
    compose run --rm --no-deps backend \
      python database/seeds/load_patient_photos.py
    wait_for_frontend
    print_urls
    ;;
  down)
    prepare_env
    compose down
    ;;
  restart)
    prepare_env
    compose restart
    wait_for_backend
    wait_for_frontend
    print_urls
    ;;
  migrate)
    # 빈 볼륨 최초 생성 때만 도는 initdb.d 를 손으로 한 번 더 부른다.
    # schema_migrations 가 적용 이력을 들고 있으므로 몇 번 돌려도 안전하다.
    prepare_env
    compose exec -T -e MINGKY_MIGRATIONS_ONLY=1 postgres \
      sh /docker-entrypoint-initdb.d/10-mingky-init.sh
    ;;
  status)
    prepare_env
    compose ps
    ;;
  logs)
    prepare_env
    compose logs --follow
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac
