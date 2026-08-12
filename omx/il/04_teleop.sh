#!/usr/bin/env bash
# [4단계] 로봇 움직여 보기 — 리더를 손으로 움직이면 팔로워가 따라온다.
#
#   bash 04_teleop.sh          # 팔만 (제일 먼저 이걸로 확인)
#   bash 04_teleop.sh --cam    # 카메라 2대 + rerun 화면까지
#
# 여기까지 되면 하드웨어 세팅은 끝난 것이다. 녹화(5단계)로 넘어가면 된다.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
source "$HERE/_common.sh"
check_ready

USE_CAM=0
[ "${1:-}" = "--cam" ] && USE_CAM=1

echo "=============================================="
echo " 팔로워 $FOLLOWER_PORT ($FOLLOWER_ID)"
echo " 리더   $LEADER_PORT ($LEADER_ID)"
[ $USE_CAM -eq 1 ] && echo " 카메라 top=${TOP_CAM:-없음}"
[ $USE_CAM -eq 1 ] && echo "        wrist=${WRIST_CAM:-없음}"
echo "=============================================="
echo " 리더를 천천히 움직이세요. 팔로워가 따라오면 성공입니다."
echo " 끝내려면 Ctrl+C."
echo
echo " ※ 팔이 툭툭 튀거나 특정 자세에서 힘이 빠지면 → 모터가 아니라 케이블 장력입니다."
echo "   관절을 넘어가는 케이블이 팽팽해졌는지 먼저 보세요."
echo

if [ $USE_CAM -eq 1 ]; then
  CAMS=$(build_cams)
  if [ -z "$CAMS" ]; then
    echo "카메라 설정이 없습니다. 먼저: bash 03_check_cameras.sh --view"
    exit 1
  fi
  exec lerobot-teleoperate \
    --robot.type=omx_follower --robot.port="$FOLLOWER_PORT" --robot.id="$FOLLOWER_ID" \
    --robot.cameras="$CAMS" \
    --teleop.type=omx_leader --teleop.port="$LEADER_PORT" --teleop.id="$LEADER_ID" \
    --display_data=true
else
  exec lerobot-teleoperate \
    --robot.type=omx_follower --robot.port="$FOLLOWER_PORT" --robot.id="$FOLLOWER_ID" \
    --teleop.type=omx_leader --teleop.port="$LEADER_PORT" --teleop.id="$LEADER_ID"
fi
