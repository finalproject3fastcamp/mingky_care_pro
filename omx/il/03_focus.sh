#!/usr/bin/env bash
# [3단계 보조] 초점 맞추기 — 가상환경을 켜고 03_focus.py 를 부르는 껍데기다.
#
#   bash 03_focus.sh          # 카메라를 고르라고 물어본다
#   bash 03_focus.sh top      # cams.env 의 top 카메라
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1090
source "$HOME/venv/il/bin/activate"
exec python "$HERE/03_focus.py" "$@"
