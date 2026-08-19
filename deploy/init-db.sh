#!/usr/bin/env sh

# 마이그레이션·시드 적용기.
#
# 두 경로에서 불린다.
#   1. postgres 이미지의 docker-entrypoint-initdb.d — 빈 볼륨 최초 생성 시 한 번.
#   2. ./deploy/deploy.sh migrate — 이미 데이터가 있는 운영 DB 갱신.
#
# 2번에서 시드까지 돌리면 001_initial_data 의 DO UPDATE 가 운영자가 고쳐 둔
# 환자·약품 데이터를 덮는다. 그래서 MINGKY_MIGRATIONS_ONLY=1 이면 시드를
# 건너뛴다. 위치 인자 대신 환경 변수를 쓰는 이유는, initdb.d 가 실행 권한이
# 없는 스크립트를 source 로 읽어 $1 이 예상과 달라지기 때문이다.

set -eu

MIGRATIONS_ONLY="${MINGKY_MIGRATIONS_ONLY:-0}"

# 컨테이너 안에서는 compose 가 마운트한 경로를 그대로 쓴다. CI 러너처럼
# 저장소를 체크아웃한 곳에서 직접 부를 때만 덮어쓴다 — 러너에 서비스 컨테이너로
# 뜬 postgres 에는 저장소가 없어서 마운트 경로가 존재하지 않는다.
# 접속 정보는 psql 이 PGHOST·PGPORT·PGPASSWORD 로 알아서 읽는다.
MIGRATIONS_DIR="${MINGKY_MIGRATIONS_DIR:-/opt/mingky/migrations}"
SEEDS_DIR="${MINGKY_SEEDS_DIR:-/opt/mingky/seeds}"

psql_run() {
  psql \
    --set ON_ERROR_STOP=1 \
    --username "${POSTGRES_USER}" \
    --dbname "${POSTGRES_DB}" \
    "$@"
}

# schema_migrations 자체가 아직 없으면 psql 이 실패한다. 그때는 "미적용" 으로
# 본다 — 그 테이블을 만드는 000 이 바로 그 상태에서 돌아야 하는 파일이다.
is_applied() {
  psql \
    --tuples-only --no-align \
    --username "${POSTGRES_USER}" \
    --dbname "${POSTGRES_DB}" \
    --command "SELECT 1 FROM schema_migrations WHERE version = '$1'" 2>/dev/null \
    | grep -q '^1$'
}

# 기록은 러너가 한다. 마이그레이션 파일이 러너 사정을 알 필요가 없고,
# INSERT 를 빠뜨린 파일이 매번 다시 도는 사고도 생기지 않는다.
mark_applied() {
  psql_run --command \
    "INSERT INTO schema_migrations (version) VALUES ('$1') ON CONFLICT (version) DO NOTHING" \
    >/dev/null
}

run_migrations() {
  for sql_file in "${MIGRATIONS_DIR}"/*.sql; do
    if [ ! -f "${sql_file}" ]; then
      continue
    fi

    version="$(basename "${sql_file}" .sql)"

    if is_applied "${version}"; then
      echo "[DB] migration: ${version} (이미 적용됨)"
      continue
    fi

    echo "[DB] migration: ${version}"
    psql_run --file "${sql_file}"
    mark_applied "${version}"
  done
}

# 시드는 전부 ON CONFLICT 로 멱등하므로 이력을 추적하지 않는다.
run_seeds() {
  for sql_file in "${SEEDS_DIR}"/*.sql; do
    if [ ! -f "${sql_file}" ]; then
      continue
    fi

    echo "[DB] seed: $(basename "${sql_file}")"
    psql_run --file "${sql_file}"
  done
}

run_migrations

if [ "${MIGRATIONS_ONLY}" = "1" ]; then
  echo "[DB] MINGKY_MIGRATIONS_ONLY=1 — 시드는 건너뜁니다."
else
  run_seeds
fi
