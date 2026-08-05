#!/usr/bin/env bash

set -eo pipefail

PINKY_DOMAIN_ID="${PINKY_DOMAIN_ID:-21}"
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
  echo "사용법: ros2 run mingky_bringup capture_waypoint.sh <waypoint_name>" >&2
  echo "예: ros2 run mingky_bringup capture_waypoint.sh reception_goal" >&2
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
  fail "'${WAYPOINT_NAME}' waypoint가 이미 존재합니다: ${OUTPUT_FILE}"
fi

TF_OUTPUT="$(timeout 5 ros2 run tf2_ros tf2_echo map base_link 2>&1 || true)"
TRANSLATION_LINE="$(grep -- '- Translation:' <<<"${TF_OUTPUT}" | tail -n 1 || true)"
RPY_LINE="$(grep -- '- Rotation: in RPY (radian)' <<<"${TF_OUTPUT}" | tail -n 1 || true)"

if [[ -z "${TRANSLATION_LINE}" || -z "${RPY_LINE}" ]]; then
  echo "[실패] map → base_link 위치를 읽지 못했습니다." >&2
  echo "RViz에서 먼저 '2D Pose Estimate'를 지정하고 AMCL이 동작하는지 확인하세요." >&2
  exit 1
fi

TRANSLATION_VALUES="$(sed -E 's/.*\[([^]]+)\].*/\1/; s/,//g' <<<"${TRANSLATION_LINE}")"
RPY_VALUES="$(sed -E 's/.*\[([^]]+)\].*/\1/; s/,//g' <<<"${RPY_LINE}")"

read -r X Y _ <<<"${TRANSLATION_VALUES}"
read -r _ _ YAW <<<"${RPY_VALUES}"

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
