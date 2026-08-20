#!/usr/bin/env bash
#
# 로컬 개발 스택 기동 — PostgreSQL · 백엔드 · 대시보드.
#
# docker 가 없고 sudo 에 비밀번호가 걸린 개발 머신을 위한 경로다. root 없이
# 되는 것만 쓴다. 이미 떠 있는 것은 건너뛰므로 몇 번 실행해도 안전하다.
#
#   ./.claude/skills/run-local/start.sh          # 전부 기동
#   ./.claude/skills/run-local/start.sh db       # DB 만
#
# 종료는 stop.sh.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TARGET="${1:-all}"

NODE_VERSION=24.19.0
NODE_HOME="$HOME/.local/opt/node-v${NODE_VERSION}-linux-x64"
PGVENV="$HOME/.local/opt/pgserver-venv"
PGBIN="$PGVENV/lib/python3.12/site-packages/pgserver/pginstall/bin"
export PGDATA="$HOME/.local/share/mingky-care-pg"
LOG_DIR="$HOME/.local/share/mingky-care-logs"

mkdir -p "$LOG_DIR"

say() { printf '\033[1;34m[run-local]\033[0m %s\n' "$*"; }

# 포트가 열려 있으면 이미 떠 있는 것으로 본다. pg_isready·curl 은 대상별로
# 다르므로 여기서는 리스닝 여부만 본다.
port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && exec 3<&- && return 0 || return 1; }

# 서버를 이 스크립트에서 완전히 떼어내 띄운다.
#
# `( nohup cmd & )` 로는 부족했다 — 자식이 그대로 이 스크립트에 매달려서
# start.sh 가 `wait4` 에 걸린 채 영영 끝나지 않았다 (프로세스가 쌓였다).
# setsid 로 새 세션을 만들고 표준 입출력을 전부 끊어야 진짜로 분리된다.
# `exec` 가 중요하다. 이게 없으면 서브셸 bash 가 자식을 기다리며 그대로 남아,
# start.sh 를 여러 번 부를수록 죽은 bash 가 쌓인다. exec 로 서브셸 자신을
# setsid 로 갈아치우면 남는 프로세스가 없다.
spawn() {
  local log="$1" dir="$2"; shift 2
  ( cd "$dir" && exec setsid "$@" >>"$log" 2>&1 </dev/null ) &
  disown 2>/dev/null || true
}

wait_for_port() {
  local port=$1 name=$2 tries=${3:-40}
  for _ in $(seq 1 "$tries"); do
    port_busy "$port" && return 0
    sleep 1
  done
  say "$name 이(가) :$port 에서 뜨지 않았습니다. 로그를 보세요."
  return 1
}

# ---------------------------------------------------------------- bootstrap

bootstrap_node() {
  [ -x "$NODE_HOME/bin/npm" ] && return 0
  # 시스템 node 는 18 이고 npm 은 아예 없다. Vite 8 은 Node 20.19+ 를 요구하고
  # .nvmrc 는 24 를 지정한다. nvm 도 없으므로 공식 tarball 을 직접 푼다.
  say "Node ${NODE_VERSION} 설치 (최초 1회)"
  mkdir -p "$HOME/.local/opt"
  local tmp
  tmp="$(mktemp -d)"
  curl -sSL --fail -o "$tmp/node.tar.xz" \
    "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz"
  tar -xf "$tmp/node.tar.xz" -C "$HOME/.local/opt"
  rm -rf "$tmp"
}

bootstrap_pg() {
  [ -x "$PGBIN/postgres" ] && return 0
  # docker 도 postgres 패키지도 없고 sudo 는 비밀번호를 묻는다. pgserver 휠에
  # 들어 있는 재배치 가능한 PostgreSQL 바이너리를 쓴다 — 설치에 root 가 필요없다.
  #
  # 주의: 이 바이너리는 PostgreSQL 16 이고 compose.yaml 은 18-alpine 이다.
  # 현재 마이그레이션에 18 전용 문법이 없어 그대로 적용되지만 운영과 같은
  # 버전은 아니다. 버전을 맞추려면 docker 가 필요하다.
  say "pgserver(PostgreSQL 바이너리) 설치 (최초 1회)"
  python3.12 -m venv "$PGVENV"
  "$PGVENV/bin/pip" install -q --disable-pip-version-check pgserver
}

bootstrap_backend_venv() {
  [ -x "$REPO_ROOT/backend/.venv/bin/uvicorn" ] && return 0
  say "백엔드 가상환경 생성 (최초 1회)"
  python3.12 -m venv "$REPO_ROOT/backend/.venv"
  "$REPO_ROOT/backend/.venv/bin/pip" install -q --disable-pip-version-check \
    -r "$REPO_ROOT/backend/requirements.txt"
}

# --------------------------------------------------------------------- db

start_db() {
  bootstrap_pg

  [ -f "$REPO_ROOT/database/.env" ] || cp "$REPO_ROOT/database/.env.example" "$REPO_ROOT/database/.env"
  # shellcheck disable=SC1091
  set -a; . "$REPO_ROOT/database/.env"; set +a

  export PGHOST=127.0.0.1 PGPORT="${POSTGRES_PORT}" PGUSER="${POSTGRES_USER}" \
         PGPASSWORD="${POSTGRES_PASSWORD}"

  if [ ! -f "$PGDATA/PG_VERSION" ]; then
    say "PGDATA 초기화 — $PGDATA"
    mkdir -p "$PGDATA"; chmod 700 "$PGDATA"
    local pwfile; pwfile="$(mktemp)"
    printf '%s' "${POSTGRES_PASSWORD}" > "$pwfile"
    "$PGBIN/initdb" -D "$PGDATA" -U "${POSTGRES_USER}" -E UTF8 \
      --auth-local=trust --auth-host=scram-sha-256 --pwfile="$pwfile" >/dev/null
    rm -f "$pwfile"
  fi

  if "$PGBIN/pg_isready" -q -h 127.0.0.1 -p "$PGPORT" 2>/dev/null; then
    say "PostgreSQL 이미 실행 중 — :$PGPORT"
  else
    mkdir -p "$PGDATA/sock"
    say "PostgreSQL 기동 — :$PGPORT"
    "$PGBIN/pg_ctl" -D "$PGDATA" -l "$PGDATA/server.log" \
      -o "-h 127.0.0.1 -p $PGPORT -k $PGDATA/sock" -w start >/dev/null
  fi

  # createdb 는 이미 있으면 에러로 끝난다. 그건 정상이므로 삼킨다.
  "$PGBIN/createdb" -w "${POSTGRES_DB}" </dev/null 2>/dev/null \
    && say "데이터베이스 ${POSTGRES_DB} 생성" || true

  # 저장소의 러너를 그대로 쓴다. 디렉터리만 알려주면 되고, 접속 정보는 psql 이
  # PGHOST·PGPORT·PGPASSWORD 에서 읽는다. 마이그레이션은 schema_migrations 로
  # 멱등하고 시드는 ON CONFLICT 로 멱등하다.
  #
  # `</dev/null` 이 중요하다 — PGPASSWORD 없이 또는 TTY 없이 psql 을 부르면
  # 비밀번호 프롬프트가 EOF 를 만나 무한 반복한다.
  say "마이그레이션·시드 적용"
  PATH="$PGBIN:$PATH" \
  MINGKY_MIGRATIONS_DIR="$REPO_ROOT/database/migrations" \
  MINGKY_SEEDS_DIR="$REPO_ROOT/database/seeds" \
    sh "$REPO_ROOT/deploy/init-db.sh" </dev/null 2>&1 | grep -E '^\[DB\]' || true

  if [ -x "$REPO_ROOT/backend/.venv/bin/python" ]; then
    "$REPO_ROOT/backend/.venv/bin/python" \
      "$REPO_ROOT/database/seeds/load_patient_photos.py" </dev/null >/dev/null 2>&1 \
      && say "환자 사진 적재" || say "환자 사진 적재 건너뜀"
  fi
}

# ---------------------------------------------------------------- backend

start_backend() {
  bootstrap_backend_venv

  if port_busy 8000; then
    say "백엔드 이미 실행 중 — :8000"
    return 0
  fi

  # 약국 조제는 기본이 시뮬레이션이다. PHARMACY_REAL=1 로 켤 때만 실제 로봇팔이
  # 움직인다 — 실수로 움직이지 않도록 여기서 한 번 더 확인하고 알린다.
  if [ "${PHARMACY_REAL:-0}" = "1" ]; then
    if ! "$(dirname "${BASH_SOURCE[0]}")/preflight-omx.sh" --no-ping >/dev/null 2>&1; then
      say "PHARMACY_REAL=1 이지만 준비물이 빠졌습니다. preflight-omx.sh 를 실행해 확인하세요."
      return 1
    fi
    export PHARMACY_REAL=1
    say "⚠ 실제 모드 — 조제를 시작하면 로봇팔이 실제로 움직입니다"
  fi

  # 인스턴스는 반드시 1개다. heartbeat·arming·orders 가 인메모리라 둘 이상
  # 뜨면 판정이 갈린다. 기동 시 advisory lock 으로 강제되고, 못 잡으면 종료
  # 코드 3 으로 죽는다. --workers 를 늘리지 말 것.
  say "백엔드 기동 — :8000 (로그: $LOG_DIR/backend.log)"
  spawn "$LOG_DIR/backend.log" "$REPO_ROOT/backend" \
    ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
  wait_for_port 8000 "백엔드"
}

# --------------------------------------------------------------- frontend

start_frontend() {
  bootstrap_node
  export PATH="$NODE_HOME/bin:$PATH"

  [ -f "$REPO_ROOT/frontend/.env" ] || cp "$REPO_ROOT/frontend/.env.example" "$REPO_ROOT/frontend/.env"
  if [ ! -d "$REPO_ROOT/frontend/node_modules" ]; then
    say "프런트엔드 의존성 설치 (최초 1회)"
    ( cd "$REPO_ROOT/frontend" && npm ci --no-audit --no-fund >/dev/null )
  fi

  if port_busy 5173; then
    say "대시보드 이미 실행 중 — :5173"
    return 0
  fi

  # vite.config.ts 가 /api → 127.0.0.1:8000 로 프록시한다. 백엔드에 CORS 설정이
  # 없어서 브라우저 직접 호출은 막힌다 — VITE_API_BASE_URL 은 상대경로로 둘 것.
  say "대시보드 기동 — :5173 (로그: $LOG_DIR/frontend.log)"
  spawn "$LOG_DIR/frontend.log" "$REPO_ROOT/frontend" \
    npm run dev -- --host 127.0.0.1 --port 5173
  wait_for_port 5173 "대시보드"
}

case "$TARGET" in
  db)       start_db ;;
  backend)  start_db; start_backend ;;
  frontend) start_frontend ;;
  all)      start_db; start_backend; start_frontend ;;
  *)        echo "사용법: start.sh [all|db|backend|frontend]" >&2; exit 2 ;;
esac

say "준비 완료"
# `[ ... ] && cat` 를 마지막 명령으로 두면 안 된다 — TARGET 이 all 이 아닐 때
# AND 목록이 1 을 돌려주고 set -e 가 그걸 실패로 보아 스크립트가 1 로 끝난다.
if [ "$TARGET" = all ]; then cat <<'EOF'

  대시보드   http://localhost:5173
  백엔드     http://localhost:8000/docs
  DB         postgresql://mingky_care@127.0.0.1:5432/mingky_care
EOF
fi
exit 0
