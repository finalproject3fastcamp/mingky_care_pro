#!/usr/bin/env bash
# 04~07 스크립트가 공통으로 읽는 설정. 직접 실행하는 파일이 아니다.
#
# 여기서 바꿀 것은 맨 아래 두 개(REPO, TASK)뿐이다.

VENV=$HOME/venv/il
HERE=${HERE:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}

# shellcheck disable=SC1090
source "$VENV/bin/activate"

# --- 로봇 포트 (02_find_ports.sh 가 고정한 이름) ---------------------------
FOLLOWER_PORT=${FOLLOWER_PORT:-/dev/omx_follower}
LEADER_PORT=${LEADER_PORT:-/dev/omx_leader}

# 팔에 붙이는 이름. lerobot 이 이 이름으로 설정 파일을 만든다.
FOLLOWER_ID=${FOLLOWER_ID:-omx_follower_arm}
LEADER_ID=${LEADER_ID:-omx_leader_arm}

# --- 카메라 (03_check_cameras.py 가 만든 파일) -----------------------------
if [ -f "$HERE/cams.env" ]; then
  # shellcheck disable=SC1091
  source "$HERE/cams.env"
fi

# lerobot 에 넘길 카메라 딕셔너리를 만든다.
# backend: V4L2 + fourcc: MJPG 는 반드시 준다 — 기본 FFMPEG 백엔드로 열면
# 해상도·포맷 설정이 그냥 무시된다 (원인 찾는 데 한참 걸렸던 항목).
build_cams() {
  local parts=()
  local spec="width: 640, height: 480, fps: 30, fourcc: MJPG, backend: V4L2"
  [ -n "${TOP_CAM:-}" ]   && parts+=("top: {type: opencv, index_or_path: $TOP_CAM, $spec}")
  [ -n "${WRIST_CAM:-}" ] && parts+=("wrist: {type: opencv, index_or_path: $WRIST_CAM, $spec}")
  if [ ${#parts[@]} -eq 0 ]; then
    echo ""
    return
  fi
  local joined
  joined=$(printf ", %s" "${parts[@]}")
  echo "{ ${joined:2} }"
}

check_ready() {
  local bad=0
  for p in "$FOLLOWER_PORT" "$LEADER_PORT"; do
    if [ ! -e "$p" ]; then
      echo "! $p 가 없습니다 — 로봇을 꽂고 전원을 켠 뒤 bash 02_find_ports.sh"
      bad=1
    fi
  done
  for c in "${TOP_CAM:-}" "${WRIST_CAM:-}"; do
    if [ -n "$c" ] && [ ! -e "$c" ]; then
      echo "! 카메라가 없습니다: $c"
      echo "  ls /dev/v4l/by-id/ 로 확인하고 다시: bash 03_check_cameras.sh --view"
      bad=1
    fi
  done
  [ $bad -eq 0 ] || exit 1
}

# --- 여기만 자기 것으로 바꾸면 된다 ----------------------------------------
# 작업 정의와 성공 기준은 TASK.md 에 적어둔다. 여기 두 줄과 그 문서를 같이 고친다.
#
# 데이터셋 이름. 작업이 바뀌면 반드시 새 이름을 쓴다 (섞이면 못 되돌린다).
# 킷 원본은 sujin/bottle_v1 이었다. 같은 작업이지만 데이터를 새로 찍으므로 이름을 나눈다.
REPO=${REPO:-mingky/pill_bottle_v1}

# 이 문자열이 그대로 데이터에 들어가고, 나중에 정책을 부를 때도 같은 문자열을 쓴다.
# 녹화(05)와 평가(07)에서 **글자 하나까지 같아야** 한다. 다듬고 싶어도 두지 않는다.
TASK=${TASK:-"put the pill bottle in the envelope"}
