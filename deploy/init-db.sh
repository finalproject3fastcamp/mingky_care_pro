#!/usr/bin/env sh

set -eu

run_sql_directory() {
  directory="$1"
  label="$2"

  for sql_file in "${directory}"/*.sql; do
    if [ ! -f "${sql_file}" ]; then
      continue
    fi

    echo "[DB] ${label}: $(basename "${sql_file}")"
    psql \
      --set ON_ERROR_STOP=1 \
      --username "${POSTGRES_USER}" \
      --dbname "${POSTGRES_DB}" \
      --file "${sql_file}"
  done
}

run_sql_directory /opt/mingky/migrations "migration"
run_sql_directory /opt/mingky/seeds "seed"
