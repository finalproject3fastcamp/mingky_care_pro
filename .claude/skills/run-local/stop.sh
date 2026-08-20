#!/usr/bin/env bash
#
# start.sh 로 띄운 로컬 스택을 내린다. DB 데이터는 지우지 않는다.
#
#   ./.claude/skills/run-local/stop.sh          # 전부 종료
#   ./.claude/skills/run-local/stop.sh frontend # 하나만

set -uo pipefail

PGBIN="$HOME/.local/opt/pgserver-venv/lib/python3.12/site-packages/pgserver/pginstall/bin"
PGDATA="$HOME/.local/share/mingky-care-pg"
TARGET="${1:-all}"

say() { printf '\033[1;34m[run-local]\033[0m %s\n' "$*"; }

# `pkill -f` 는 명령줄 전체를 본다. 그래서 패턴 문자열을 인자에 담고 있는 셸까지
# 같이 잡는다 — 이 스크립트를 부른 명령이 그 문자열을 포함하면 자기 자신을 죽인다
# (실제로 당했다). 프로세스 이름(comm)으로 한 번 더 걸러서 진짜 서버만 고른다.
pids_for() {
  local pattern="$1" want="$2" pid
  for pid in $(pgrep -f "$pattern" 2>/dev/null); do
    [ "$pid" = "$$" ] && continue
    [ "$(cat "/proc/$pid/comm" 2>/dev/null)" = "$want" ] || continue
    echo "$pid"
  done
}

stop_frontend() {
  local pids; pids="$(pids_for 'vite.*--port 5173' node)"
  [ -n "$pids" ] || { say "대시보드 미실행"; return 0; }
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null
  say "대시보드 종료"
}

stop_backend() {
  local pids; pids="$(pids_for 'uvicorn app\.main:app' uvicorn)"
  [ -n "$pids" ] || { say "백엔드 미실행"; return 0; }
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null

  # SIGTERM 만으로는 안 죽는다. 약국 화면이 열려 있으면 `/pharmacy/progress` SSE
  # 가 끝나지 않는 요청이라, uvicorn 이 리스너만 닫고 그 요청을 기다리며 매달린다.
  # 그동안 DB 세션을 쥔 채라 단일 인스턴스 advisory lock 이 반납되지 않고,
  # 다음 기동이 "다중 워커/레플리카 감지" 로 종료 코드 3 에 걸린다.
  for _ in $(seq 1 10); do
    [ -n "$(pids_for 'uvicorn app\.main:app' uvicorn)" ] || { say "백엔드 종료"; return 0; }
    sleep 1
  done
  # shellcheck disable=SC2086
  kill -9 $(pids_for 'uvicorn app\.main:app' uvicorn) 2>/dev/null
  say "백엔드 종료 (SSE 가 남아 있어 강제 종료)"
}

stop_db() {
  if [ -f "$PGDATA/postmaster.pid" ]; then
    "$PGBIN/pg_ctl" -D "$PGDATA" -m fast -w stop >/dev/null && say "PostgreSQL 종료"
  else
    say "PostgreSQL 미실행"
  fi
}

case "$TARGET" in
  db)       stop_db ;;
  backend)  stop_backend ;;
  frontend) stop_frontend ;;
  all)      stop_frontend; stop_backend; stop_db ;;
  *)        echo "사용법: stop.sh [all|db|backend|frontend]" >&2; exit 2 ;;
esac
