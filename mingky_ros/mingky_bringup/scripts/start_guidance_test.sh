#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ! $1 =~ ^[1-9][0-9]*$ ]]; then
  echo "사용법: ros2 run mingky_bringup start_guidance_test.sh <session_id>" >&2
  exit 2
fi

if ! ros2 topic info /guide_manager/start_guidance >/dev/null 2>&1; then
  echo "[오류] /guide_manager/start_guidance 토픽이 없습니다." >&2
  echo "mingky_system.launch.xml과 guide_manager 실행 상태를 확인하세요." >&2
  exit 1
fi

echo "[요청] 안내 출발 session_id=$1"
ros2 topic pub --once /guide_manager/start_guidance \
  std_msgs/msg/String "{data: '$1'}"

