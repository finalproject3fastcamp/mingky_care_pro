#!/usr/bin/env bash

# 저장 waypoint 없이 RViz에서 Nav2 Goal을 직접 찍어 실주행을 시험한다.
set -eo pipefail

# 로봇이 공유기(mingky)에 연결된 구성을 기본으로 사용한다.
# AP 모드로 직접 연결할 때만 PINKY_IP와 PINKY_SSID를 지정한다.
PINKY_IP="${PINKY_IP:-192.168.0.21}"
PINKY_SSID="${PINKY_SSID:-}"
PINKY_DOMAIN_ID="${PINKY_DOMAIN_ID:-21}"
# 개발 진단 도구가 지도·합쳐진 파라미터를 넘길 때만 사용한다.
MINGKY_MAP_PATH="${MINGKY_MAP_PATH:-}"
NAV2_PARAMS_FILE="${NAV2_PARAMS_FILE:-}"

NAV2_PID=""
RVIZ_PID=""
CLEANED_UP=false

usage() {
  cat <<'EOF'
사용법:
  run_nav2_manual_test.sh

옵션:
  -h, --help  도움말 출력

지도를 선택한 뒤 RViz의 Nav2 Goal 도구로 목표 위치와 방향을 직접 지정합니다.
EOF
}

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

  if [[ -z "${RVIZ_PID}" && -z "${NAV2_PID}" ]]; then
    return
  fi

  echo
  echo "[정리] RViz와 Nav2를 종료합니다."

  if [[ -n "${RVIZ_PID}" ]] && kill -0 "${RVIZ_PID}" 2>/dev/null; then
    kill -INT "${RVIZ_PID}" 2>/dev/null || true
    wait "${RVIZ_PID}" 2>/dev/null || true
  fi

  if [[ -n "${NAV2_PID}" ]] && kill -0 "${NAV2_PID}" 2>/dev/null; then
    kill -INT "${NAV2_PID}" 2>/dev/null || true
    wait "${NAV2_PID}" 2>/dev/null || true
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

wait_for_nav2() {
  local timeout_seconds="${1:-40}"
  local elapsed=0
  local state=""

  while ((elapsed < timeout_seconds)); do
    state="$(ros2 lifecycle get /controller_server 2>/dev/null || true)"
    if [[ "${state}" == active* ]] \
      && ros2 action list 2>/dev/null | grep -Fxq '/navigate_to_pose'; then
      echo "[확인] Nav2 controller와 /navigate_to_pose가 활성화되었습니다."
      return 0
    fi
    sleep 1
    ((elapsed += 1))
  done

  return 1
}

wait_for_transform() {
  local source_frame="$1"
  local target_frame="$2"
  local timeout_seconds="${3:-15}"
  local elapsed=0
  local output=""

  while ((elapsed < timeout_seconds)); do
    output="$(timeout 5 ros2 run tf2_ros tf2_echo \
      "${source_frame}" "${target_frame}" 2>/dev/null || true)"
    if grep -Fq 'Translation:' <<<"${output}"; then
      echo "[확인] TF: ${source_frame} → ${target_frame}"
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
  echo "================ 일반 Nav2 주행 지도 선택 ================"
  for index in "${!MAP_FILES[@]}"; do
    printf '[%d] %s\n' "$((index + 1))" "$(basename "${MAP_FILES[index]}")"
  done
  echo "[q] 종료"
  echo "==========================================================="

  while true; do
    read -r -p "주행할 지도 번호를 선택하세요: " selection
    [[ "${selection}" == "q" || "${selection}" == "Q" ]] && exit 0
    if [[ "${selection}" =~ ^[0-9]+$ ]] \
      && ((selection >= 1 && selection <= ${#MAP_FILES[@]})); then
      MAP_PATH="${MAP_FILES[selection - 1]}"
      return
    fi
    echo "[안내] 1~${#MAP_FILES[@]} 사이의 번호 또는 q를 입력하세요."
  done
}

while (($# > 0)); do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "알 수 없는 옵션입니다: $1"
      ;;
  esac
done

MINGKY_REPO="$(resolve_repo_root)"
[[ -n "${MINGKY_REPO}" ]] \
  || fail "저장소 경로를 찾을 수 없습니다. MINGKY_REPO 환경 변수를 지정하세요."

MAP_DIR="${MINGKY_REPO}/mingky_ros/mingky_bringup/map"
MAP_FILES=()
MAP_PATH=""
if [[ -n "${MINGKY_MAP_PATH}" ]]; then
  MAP_PATH="${MINGKY_MAP_PATH}"
else
  select_map
fi

echo "[선택] 지도: $(basename "${MAP_PATH}")"

[[ -f /opt/ros/jazzy/setup.bash ]] \
  || fail "ROS 2 Jazzy setup 파일을 찾을 수 없습니다."
[[ -f "${MINGKY_REPO}/install/setup.bash" ]] \
  || fail "프로젝트 install/setup.bash가 없습니다. 먼저 프로젝트를 빌드하세요."
[[ -f "${MAP_PATH}" ]] \
  || fail "지도 파일을 찾을 수 없습니다: ${MAP_PATH}"

command -v ping >/dev/null 2>&1 \
  || fail "ping 명령을 찾을 수 없습니다."
command -v timeout >/dev/null 2>&1 \
  || fail "timeout 명령을 찾을 수 없습니다."

if [[ -n "${PINKY_SSID}" ]]; then
  CURRENT_SSID="$(iwgetid -r 2>/dev/null || true)"
  [[ "${CURRENT_SSID}" == "${PINKY_SSID}" ]] \
    || fail "현재 Wi-Fi는 '${CURRENT_SSID:-연결 안 됨}'입니다. PC를 '${PINKY_SSID}'에 연결하세요."
  echo "[확인] Wi-Fi: ${CURRENT_SSID}"
fi

ping -c 1 -W 2 "${PINKY_IP}" >/dev/null 2>&1 \
  || fail "Pinky(${PINKY_IP})에 연결할 수 없습니다. PINKY_IP 환경변수로 주소를 바꿀 수 있습니다."
echo "[확인] Pinky 응답: ${PINKY_IP}"

# setup.bash는 정의되지 않은 변수를 참조할 수 있으므로 set -u 전에 불러온다.
# shellcheck disable=SC1091
source /opt/ros/jazzy/setup.bash
# shellcheck disable=SC1090
source "${MINGKY_REPO}/install/setup.bash"
set -u

export ROS_DOMAIN_ID="${PINKY_DOMAIN_ID}"
unset ROS_LOCALHOST_ONLY
export ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET

ros2 daemon stop >/dev/null 2>&1 || true
ros2 daemon start >/dev/null

wait_for_publisher /odom 60 \
  || fail "/odom publisher가 없습니다. Pinky bringup과 Domain ID를 확인하세요."
wait_for_publisher /scan 60 \
  || fail "/scan publisher가 없습니다. Pinky LiDAR와 bringup을 확인하세요."

if ros2 node list 2>/dev/null | grep -Fxq '/controller_server'; then
  fail "이미 /controller_server가 실행 중입니다. 기존 Nav2를 먼저 종료하세요."
fi

echo "[실행] $(basename "${MAP_PATH}")으로 Nav2를 시작합니다."
NAV2_LAUNCH_ARGS=(map:="${MAP_PATH}" use_composition:=False)
if [[ -n "${NAV2_PARAMS_FILE}" ]]; then
  [[ -f "${NAV2_PARAMS_FILE}" ]] || fail "Nav2 파라미터 파일을 찾을 수 없습니다: ${NAV2_PARAMS_FILE}"
  NAV2_LAUNCH_ARGS+=(params_file:="${NAV2_PARAMS_FILE}")
  echo "[실행] Nav2 파라미터: ${NAV2_PARAMS_FILE}"
fi
ros2 launch pinky_navigation bringup_launch.xml "${NAV2_LAUNCH_ARGS[@]}" &
NAV2_PID=$!

wait_for_nav2 90 \
  || fail "Nav2가 제한 시간 안에 활성화되지 않았습니다. 위 로그를 확인하세요."

echo "[실행] Nav2 RViz를 시작합니다."
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
2. 지도와 LaserScan이 실제 환경에 맞는지 확인하세요.
3. 이 터미널에서 Enter를 눌러 TF 연결을 확인하세요.
4. RViz 상단의 'Nav2 Goal'을 선택합니다.
5. 지도에서 목표 위치를 클릭한 뒤, 드래그하여 도착 방향을 지정합니다.

목표를 바꿔 여러 번 시험할 수 있습니다.
주행 중에는 teleop을 함께 실행하지 마세요.
종료하려면 RViz를 닫거나 이 터미널에서 Ctrl+C를 누르세요.
============================================================

EOF
read -r -p "초기 위치 설정을 마쳤으면 Enter를 누르세요: "

wait_for_transform map base_footprint 45 \
  || fail "map → base_footprint TF가 없습니다. RViz에서 2D Pose Estimate를 다시 지정하세요."

echo "[준비 완료] RViz의 Nav2 Goal 도구로 자유롭게 목표를 지정하세요."
wait "${RVIZ_PID}" || true
RVIZ_PID=""
