#!/usr/bin/env bash

set -eo pipefail

PINKY_DOMAIN_ID="${PINKY_DOMAIN_ID:-21}"

# --force 는 같은 이름을 다시 찍을 때만 쓴다. 기본은 덮어쓰기 금지다.
FORCE=false
if [[ "${1:-}" == "--force" || "${1:-}" == "-f" ]]; then
  FORCE=true
  shift
fi
WAYPOINT_NAME="${1:-}"

fail() {
  echo "[실패] $1" >&2
  exit 1
}

resolve_repo_root() {
  if [[ -n "${MINGKY_REPO:-}" ]]; then
    printf '%s\n' "${MINGKY_REPO}"
    return
  fi

  local script_dir
  script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  git -C "${script_dir}" rev-parse --show-toplevel 2>/dev/null || true
}

if [[ -z "${WAYPOINT_NAME}" ]]; then
  echo "사용법: ros2 run mingky_bringup capture_waypoint.sh [--force] <waypoint_name>" >&2
  echo "예: ros2 run mingky_bringup capture_waypoint.sh reception_goal" >&2
  echo "    ros2 run mingky_bringup capture_waypoint.sh --force reception_goal  (다시 찍기)" >&2
  exit 2
fi

if [[ ! "${WAYPOINT_NAME}" =~ ^[A-Za-z][A-Za-z0-9_-]*$ ]]; then
  fail "이름은 영문자로 시작하고 영문자, 숫자, _, -만 사용할 수 있습니다."
fi

MINGKY_REPO="$(resolve_repo_root)"
[[ -n "${MINGKY_REPO}" ]] \
  || fail "저장소 경로를 찾을 수 없습니다. MINGKY_REPO 환경 변수를 지정하세요."

SESSION_FILE="/tmp/mingky_waypoint_session_${PINKY_DOMAIN_ID}.txt"
[[ -r "${SESSION_FILE}" ]] \
  || fail "waypoint 측정 세션을 찾지 못했습니다. 먼저 run_waypoint_teleop.sh를 실행하세요."

mapfile -t SESSION_VALUES <"${SESSION_FILE}"
MAP_PATH="${SESSION_VALUES[0]:-}"
OUTPUT_FILE="${SESSION_VALUES[1]:-}"
WAYPOINT_DIR="${MINGKY_REPO}/mingky_ros/mingky_bringup/config/waypoints"

[[ -f "${MAP_PATH}" ]] \
  || fail "측정 세션의 지도 파일을 찾지 못했습니다: ${MAP_PATH:-없음}"
case "${OUTPUT_FILE}" in
  "${WAYPOINT_DIR}"/*_waypoints.yaml) ;;
  *) fail "측정 세션의 waypoint 파일 경로가 올바르지 않습니다." ;;
esac

[[ -f /opt/ros/jazzy/setup.bash ]] \
  || fail "ROS 2 Jazzy setup 파일을 찾을 수 없습니다."
[[ -f "${MINGKY_REPO}/install/setup.bash" ]] \
  || fail "프로젝트 install/setup.bash가 없습니다. 먼저 프로젝트를 빌드하세요."

# shellcheck disable=SC1091
source /opt/ros/jazzy/setup.bash
# shellcheck disable=SC1090
source "${MINGKY_REPO}/install/setup.bash"
set -u

export ROS_DOMAIN_ID="${PINKY_DOMAIN_ID}"
export ROS_LOCALHOST_ONLY=0

if [[ -f "${OUTPUT_FILE}" ]] && grep -Eq "^[[:space:]]{2}${WAYPOINT_NAME}:" "${OUTPUT_FILE}"; then
  if [[ "${FORCE}" != true ]]; then
    echo "[실패] '${WAYPOINT_NAME}' waypoint가 이미 존재합니다: ${OUTPUT_FILE}" >&2
    echo "다시 찍으려면 --force 를 붙이세요." >&2
    exit 1
  fi

  # 기존 항목(이름 줄 + 그 아래 들여쓴 줄들)을 지우고 새로 붙인다.
  WAYPOINT_NAME="${WAYPOINT_NAME}" OUTPUT_FILE="${OUTPUT_FILE}" python3 <<'PY'
import os
import re

name = os.environ["WAYPOINT_NAME"]
path = os.environ["OUTPUT_FILE"]

with open(path) as handle:
    lines = handle.readlines()

kept, dropping = [], False
for line in lines:
    if re.match(rf"^  {re.escape(name)}:\s*$", line):
        dropping = True
        continue
    # 항목 본문은 4칸 이상 들여쓴 줄이다. 그보다 얕아지면 항목이 끝난 것이다.
    if dropping and (not line.strip() or not line.startswith("    ")):
        dropping = False
    if not dropping:
        kept.append(line)

with open(path, "w") as handle:
    handle.writelines(kept)
PY
  echo "[안내] 기존 '${WAYPOINT_NAME}' 좌표를 지우고 다시 찍습니다."
fi

# TF(map → base_link) 대신 /amcl_pose 를 읽는다.
#
# tf2_echo 는 노드를 새로 띄워 TF 버퍼를 채워야 하는데, 그 체인은
# odom → base_link 를 로봇이 내주어야 완성된다. 로봇 TF 가 안 잡히는
# 상황에서는 아무리 기다려도 좌표가 안 나온다.
#
# /amcl_pose 는 관제 PC 의 AMCL 이 직접 내는 map 기준 추정 위치라
# 같은 값을 로봇 TF 없이 얻을 수 있다.
POSE_FILE="$(mktemp)"
trap 'rm -f "${POSE_FILE}"' EXIT
timeout 15 ros2 topic echo /amcl_pose --once >"${POSE_FILE}" 2>/dev/null || true

if ! grep -q 'position:' "${POSE_FILE}"; then
  echo "[실패] /amcl_pose 를 읽지 못했습니다." >&2
  echo "RViz에서 먼저 '2D Pose Estimate'를 지정하고 AMCL이 동작하는지 확인하세요." >&2
  exit 1
fi

POSE_VALUES="$(POSE_FILE="${POSE_FILE}" python3 <<'PY'
import math
import os

import yaml

# ros2 topic echo 는 메시지 끝에 '---' 를 붙여 다중 문서로 만든다.
with open(os.environ["POSE_FILE"]) as handle:
    pose = next(yaml.safe_load_all(handle))["pose"]["pose"]

position, orientation = pose["position"], pose["orientation"]
yaw = math.atan2(
    2.0 * (orientation["w"] * orientation["z"] + orientation["x"] * orientation["y"]),
    1.0 - 2.0 * (orientation["y"] ** 2 + orientation["z"] ** 2),
)
print(f'{position["x"]:.6f} {position["y"]:.6f} {yaw:.6f}')
PY
)"

if [[ -z "${POSE_VALUES}" ]]; then
  echo "[실패] /amcl_pose 값을 해석하지 못했습니다." >&2
  exit 1
fi

read -r X Y YAW <<<"${POSE_VALUES}"

if [[ ! -f "${OUTPUT_FILE}" ]]; then
  mkdir -p "${WAYPOINT_DIR}"
  printf '# 지도: %s\nwaypoints:\n' "$(basename "${MAP_PATH}")" >"${OUTPUT_FILE}"
fi

{
  printf '  %s:\n' "${WAYPOINT_NAME}"
  printf '    x: %s\n' "${X}"
  printf '    y: %s\n' "${Y}"
  printf '    yaw: %s\n' "${YAW}"
} >>"${OUTPUT_FILE}"

echo "[저장 완료] ${WAYPOINT_NAME}"
echo "  x: ${X}"
echo "  y: ${Y}"
echo "  yaw: ${YAW}"
echo "  지도: $(basename "${MAP_PATH}")"
echo "  파일: ${OUTPUT_FILE}"
