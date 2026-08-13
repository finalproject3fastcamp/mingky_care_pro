#!/usr/bin/env bash
# [3단계] 카메라 확인 — 가상환경을 켜고 03_check_cameras.py 를 부르는 껍데기다.
#
#   bash 03_check_cameras.sh           # 목록 + 상태 점검
#   bash 03_check_cameras.sh --view    # 화면 보고 top/wrist 고르기 (이걸 하세요)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1090
source "$HOME/venv/il/bin/activate"
exec python "$HERE/03_check_cameras.py" "$@"
