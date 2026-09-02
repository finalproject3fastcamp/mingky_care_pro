#!/usr/bin/env bash
#
# 관제 서버에서 **git 에 없는 것들**을 한 덩어리로 묶는다.
#
# ## 왜 필요한가
#
# 코드는 git 이 이미 백업이다. 실기 운용 마지막 상태는 `real-robot-final`
# 태그가 가리킨다. 하지만 실기가 회수되고 데모로 전환하는 시점에 실제로
# 날아갈 위험이 있는 것은 저장소에 **한 번도 들어간 적 없는** 쪽이다.
#
#   - PostgreSQL 볼륨 — 실기로 돌린 진짜 세션·이벤트 기록. 재현이 불가능하다.
#     데모 하네스가 같은 DB 에 계속 쓰기 때문에, 안 떠 두면 진짜 주행 기록과
#     가짜가 섞인 채로 남는다 (source_node 로 갈라낼 수는 있지만 그건 사후
#     구분이지 보존이 아니다)
#   - /etc/mingky/*.env — 저장소에는 .example 만 있다
#   - nginx 사이트 설정 — 역터널 포트·Foxglove 토큰이 박혀 있다
#   - systemd 유닛 — 실기가 어떤 서비스로 돌았는지의 유일한 기록
#   - deploy/.env — DB 비밀번호와 OMX 러너 주소
#
# ## 비밀이 들어 있다
#
# 산출물에는 DB 비밀번호와 Foxglove 토큰이 그대로 들어간다. 0600 으로 만들고,
# 공유 저장소나 저장소 안에 두지 마라.
#
# 사용법:  sudo ./deploy/backup-server-state.sh [출력디렉터리]

set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "sudo 로 실행하세요." >&2; exit 1; }

DEPLOY_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${DEPLOY_DIR}/.env"
COMPOSE_FILE="${DEPLOY_DIR}/compose.yaml"

OUT_ROOT="${1:-/var/backups/mingky}"
STAMP="$(date +%Y%m%d-%H%M%S)"
STAGE="$(mktemp -d)"
ARCHIVE="${OUT_ROOT}/mingky-server-state-${STAMP}.tar.gz"

trap 'rm -rf "${STAGE}"' EXIT

log() { printf '[백업] %s\n' "$*"; }
warn() { printf '[건너뜀] %s\n' "$*" >&2; }

mkdir -p "${OUT_ROOT}"
mkdir -p "${STAGE}/etc-mingky" "${STAGE}/nginx" "${STAGE}/systemd" "${STAGE}/deploy"

# --- 1. DB ---------------------------------------------------------------

if [ -f "${ENV_FILE}" ] && command -v docker >/dev/null 2>&1; then
    # shellcheck disable=SC1090
    POSTGRES_DB="$(sed -n 's/^POSTGRES_DB=//p' "${ENV_FILE}" | tail -1)"
    POSTGRES_USER="$(sed -n 's/^POSTGRES_USER=//p' "${ENV_FILE}" | tail -1)"

    log "PostgreSQL 덤프 (${POSTGRES_DB})"
    # -T 로 TTY 를 떼야 파이프가 깨지지 않는다. --clean 은 복원할 때 기존
    # 객체를 지우고 넣으라는 뜻이다 — 빈 볼륨이 아닌 곳에 되돌릴 수도 있다.
    if docker compose --env-file "${ENV_FILE}" --file "${COMPOSE_FILE}" \
        exec -T postgres pg_dump --clean --if-exists \
        -U "${POSTGRES_USER}" "${POSTGRES_DB}" > "${STAGE}/database.sql" 2>/dev/null
    then
        log "  $(wc -l < "${STAGE}/database.sql") 줄"
    else
        warn "pg_dump 실패 — 컨테이너가 떠 있는지 확인하세요"
        rm -f "${STAGE}/database.sql"
    fi
else
    warn "deploy/.env 나 docker 가 없어 DB 를 못 떴습니다"
fi

# --- 2. 설정 파일들 -------------------------------------------------------

copy_if() {
    local src="$1" dest="$2"
    if [ -e "${src}" ]; then
        cp -r "${src}" "${dest}"
        log "$(basename "${src}")"
    else
        warn "없음: ${src}"
    fi
}

log "설정 파일"
# glob 이 아무것도 안 맞으면 패턴 문자열 그대로 남는다. copy_if 가 -e 로
# 걸러 주지만, 그 실패가 for 의 마지막 상태가 되면 set -e 가 여기서 죽는다.
for env_file in /etc/mingky/*.env /etc/mingky/*.conf; do
    copy_if "${env_file}" "${STAGE}/etc-mingky/" || true
done
copy_if "${ENV_FILE}" "${STAGE}/deploy/.env" || true

log "nginx"
for dir in /etc/nginx/sites-available /etc/nginx/conf.d; do
    if [ -d "${dir}" ]; then
        cp -r "${dir}" "${STAGE}/nginx/" 2>/dev/null || true
    fi
done

log "systemd 유닛"
# -name 두 개를 -o 로 잇는 곳에 괄호가 없으면 -maxdepth 가 앞쪽에만 걸린다.
# 괄호를 씌워야 두 패턴이 같은 깊이 제한을 받는다.
find /etc/systemd/system -maxdepth 1 \( -name 'mingky-*' -o -name 'fg-*' \) \
    -print0 2>/dev/null \
    | while IFS= read -r -d '' unit; do
        cp -r "${unit}" "${STAGE}/systemd/" 2>/dev/null || true
    done

# --- 3. 무엇을 떴는지 남긴다 ---------------------------------------------

{
    echo "떠낸 시각: $(date -Is)"
    echo "호스트: $(hostname)"
    echo
    echo "== git =="
    if git -C "${DEPLOY_DIR}/.." rev-parse --short HEAD 2>/dev/null; then
        git -C "${DEPLOY_DIR}/.." log -1 --format='%H%n%s%n%ci'
        echo "태그: $(git -C "${DEPLOY_DIR}/.." tag --points-at HEAD | tr '\n' ' ')"
    fi
    echo
    echo "== 컨테이너 =="
    docker compose --env-file "${ENV_FILE}" --file "${COMPOSE_FILE}" ps 2>/dev/null || true
    echo
    echo "== mingky 관련 systemd =="
    systemctl list-units 'mingky-*' 'fg-*' --all --no-pager 2>/dev/null || true
} > "${STAGE}/MANIFEST.txt"

# --- 4. 묶는다 -----------------------------------------------------------

tar -czf "${ARCHIVE}" -C "${STAGE}" .
chmod 600 "${ARCHIVE}"

echo
log "완료: ${ARCHIVE} ($(du -h "${ARCHIVE}" | cut -f1))"
echo
echo "이 파일에는 DB 비밀번호와 Foxglove 토큰이 들어 있습니다."
echo "저장소에 넣지 말고, 서버 밖으로 옮길 때도 안전한 경로를 쓰세요."
echo
echo "복원 (DB 만):"
echo "  docker compose --env-file deploy/.env --file deploy/compose.yaml \\"
echo "    exec -T postgres psql -U <USER> -d <DB> < database.sql"
