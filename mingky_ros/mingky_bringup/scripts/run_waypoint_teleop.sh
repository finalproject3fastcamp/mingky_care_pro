#!/usr/bin/env bash

set -eo pipefail

# 로봇이 공유기(mingky)에 붙어 있는 상태를 기본으로 한다.
# AP 모드(pinky_XXXX, 192.168.4.1)로 직접 붙어 작업하려면 환경변수로 덮어쓴다.
PINKY_IP="${PINKY_IP:-192.168.0.21}"
PINKY_SSID="${PINKY_SSID:-}"          # 비우면 SSID 검사를 건너뛴다
PINKY_DOMAIN_ID="${PINKY_DOMAIN_ID:-21}"

LOCALIZATION_PID=""
RVIZ_PID=""
SESSION_FILE=""
CLEANED_UP=false

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

cleanup() {
  if [[ "${CLEANED_UP}" == true ]]; then
    return
  fi
  CLEANED_UP=true

  if [[ -n "${SESSION_FILE}" && -f "${SESSION_FILE}" ]]; then
    rm -f -- "${SESSION_FILE}"
  fi

  echo
  echo "[정리] waypoint 작업용 RViz와 localization을 종료합니다."

  if [[ -n "${RVIZ_PID}" ]] && kill -0 "${RVIZ_PID}" 2>/dev/null; then
    kill -INT "${RVIZ_PID}" 2>/dev/null || true
    wait "${RVIZ_PID}" 2>/dev/null || true
  fi

  if [[ -n "${LOCALIZATION_PID}" ]] && kill -0 "${LOCALIZATION_PID}" 2>/dev/null; then
    kill -INT "${LOCALIZATION_PID}" 2>/dev/null || true
    wait "${LOCALIZATION_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

publisher_count() {
  ros2 topic info "$1" 2>/dev/null \
    | awk '/Publisher count:/ {print $3; exit}'
}

wait_for_publisher() {
  local topic="$1"
  local timeout_seconds="${2:-15}"
  local elapsed=0
  local count=0

  while ((elapsed < timeout_seconds)); do
    count="$(publisher_count "${topic}" || true)"
    count="${count:-0}"
    if [[ "${count}" =~ ^[0-9]+$ ]] && ((count > 0)); then
      echo "[확인] ${topic}: publisher ${count}개"
      return 0
    fi
    sleep 1
    ((elapsed += 1))
  done

  return 1
}

select_map() {
  local selection index

  mapfile -t MAP_FILES < <(find "${MAP_DIR}" -maxdepth 1 -type f -name '*.yaml' -print | sort)
  ((${#MAP_FILES[@]} > 0)) || fail "선택할 지도 YAML이 없습니다: ${MAP_DIR}"

  echo
  echo "================ waypoint 측정 지도 선택 ================"
  for index in "${!MAP_FILES[@]}"; do
    printf '[%d] %s\n' "$((index + 1))" "$(basename "${MAP_FILES[index]}")"
  done
  echo "[q] 종료"
  echo "========================================================="

  while true; do
    read -r -p "측정할 지도 번호를 선택하세요: " selection
    [[ "${selection}" == "q" || "${selection}" == "Q" ]] && exit 0
    if [[ "${selection}" =~ ^[0-9]+$ ]] \
      && ((selection >= 1 && selection <= ${#MAP_FILES[@]})); then
      MAP_PATH="${MAP_FILES[selection - 1]}"
      MAP_NAME="$(basename "${MAP_PATH}" .yaml)"
      WAYPOINT_FILE="${WAYPOINT_DIR}/${MAP_NAME}_waypoints.yaml"
      return
    fi
    echo "[안내] 1~${#MAP_FILES[@]} 사이의 번호 또는 q를 입력하세요."
  done
}

MINGKY_REPO="$(resolve_repo_root)"
[[ -n "${MINGKY_REPO}" ]] \
  || fail "저장소 경로를 찾을 수 없습니다. MINGKY_REPO 환경 변수를 지정하세요."

MAP_DIR="${MINGKY_REPO}/mingky_ros/mingky_bringup/map"
WAYPOINT_DIR="${MINGKY_REPO}/mingky_ros/mingky_bringup/config/waypoints"
SESSION_FILE="/tmp/mingky_waypoint_session_${PINKY_DOMAIN_ID}.txt"
MAP_FILES=()
MAP_PATH=""
MAP_NAME=""
WAYPOINT_FILE=""

select_map
mkdir -p "${WAYPOINT_DIR}"
umask 077
printf '%s\n%s\n' "${MAP_PATH}" "${WAYPOINT_FILE}" >"${SESSION_FILE}"
echo "[선택] 지도: $(basename "${MAP_PATH}")"
echo "[선택] waypoint 파일: $(basename "${WAYPOINT_FILE}")"

[[ -f /opt/ros/jazzy/setup.bash ]] \
  || fail "ROS 2 Jazzy setup 파일을 찾을 수 없습니다."
[[ -f "${MINGKY_REPO}/install/setup.bash" ]] \
  || fail "프로젝트 install/setup.bash가 없습니다. 먼저 프로젝트를 빌드하세요."
[[ -f "${MAP_PATH}" ]] \
  || fail "지도 파일을 찾을 수 없습니다: ${MAP_PATH}"

command -v ping >/dev/null 2>&1 \
  || fail "ping 명령을 찾을 수 없습니다."

# SSID 검사는 PINKY_SSID 를 지정했을 때만 한다.
# 관제컴퓨터처럼 유선으로 붙는 경우도 있어 무선 SSID 를 강제하지 않는다.
if [[ -n "${PINKY_SSID}" ]]; then
  CURRENT_SSID="$(iwgetid -r 2>/dev/null || true)"
  [[ "${CURRENT_SSID}" == "${PINKY_SSID}" ]] \
    || fail "현재 Wi-Fi는 '${CURRENT_SSID:-연결 안 됨}'입니다. PC를 '${PINKY_SSID}'에 연결하세요."
  echo "[확인] Wi-Fi: ${CURRENT_SSID}"
fi

# 실제로 닿는지가 기준이다. 어떤 경로로 붙었는지는 상관없다.
ping -c 1 -W 2 "${PINKY_IP}" >/dev/null 2>&1 \
  || fail "Pinky(${PINKY_IP})에 연결할 수 없습니다. PINKY_IP 환경변수로 주소를 바꿀 수 있습니다."
echo "[확인] Pinky 응답: ${PINKY_IP}"

# shellcheck disable=SC1091
source /opt/ros/jazzy/setup.bash
# shellcheck disable=SC1090
source "${MINGKY_REPO}/install/setup.bash"
set -u

export ROS_DOMAIN_ID="${PINKY_DOMAIN_ID}"
export ROS_LOCALHOST_ONLY=0

ros2 daemon stop >/dev/null 2>&1 || true
ros2 daemon start >/dev/null

wait_for_publisher /odom 60 \
  || fail "/odom publisher가 없습니다. Pinky bringup과 Domain ID를 확인하세요."
wait_for_publisher /scan 60 \
  || fail "/scan publisher가 없습니다. Pinky LiDAR와 bringup을 확인하세요."

ros2 pkg executables teleop_twist_keyboard 2>/dev/null \
  | grep -q 'teleop_twist_keyboard' \
  || fail "teleop_twist_keyboard 패키지를 찾을 수 없습니다."

echo "[실행] 지도 서버와 AMCL localization을 시작합니다."
ros2 launch pinky_navigation localization_launch.xml \
  map:="${MAP_PATH}" use_composition:=False &
LOCALIZATION_PID=$!

wait_for_publisher /map 60 \
  || fail "/map publisher가 시작되지 않았습니다. 위 localization 로그를 확인하세요."

if ! kill -0 "${LOCALIZATION_PID}" 2>/dev/null; then
  wait "${LOCALIZATION_PID}" || true
  LOCALIZATION_PID=""
  fail "localization이 시작 직후 종료되었습니다."
fi

echo "[실행] waypoint 작업용 RViz를 시작합니다."
# nav2_view의 Map display는 map_server의 일회성 지도를 받을 수 있도록
# Transient Local QoS로 설정되어 있다. 전체 Nav2 노드는 실행하지 않는다.
ros2 launch pinky_navigation nav2_view.launch.xml &
RVIZ_PID=$!
sleep 3

if ! kill -0 "${RVIZ_PID}" 2>/dev/null; then
  wait "${RVIZ_PID}" || true
  RVIZ_PID=""
  fail "RViz가 시작 직후 종료되었습니다."
fi

cat <<'EOF'

============================================================
1. RViz의 '2D Pose Estimate'로 실제 로봇 위치와 방향을 지정하세요.
2. 키보드 teleop으로 원하는 waypoint까지 이동하세요.
3. 키에서 손을 떼어 로봇을 정지하세요.
4. 다른 터미널에서 다음 명령으로 현재 위치를 저장하세요.

   ros2 run mingky_bringup capture_waypoint.sh <waypoint_name>

   현재 지도 전용 파일에 자동 저장됩니다.

teleop과 이 세션을 종료하려면 Ctrl+C를 누르세요.
============================================================

EOF

echo "[안내] 저장 파일: ${MAP_NAME}_waypoints.yaml"

ros2 run teleop_twist_keyboard teleop_twist_keyboard
