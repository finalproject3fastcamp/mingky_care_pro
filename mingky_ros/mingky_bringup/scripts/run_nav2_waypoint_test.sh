#!/usr/bin/env bash

set -eo pipefail

PINKY_IP="${PINKY_IP:-192.168.4.1}"
PINKY_SSID="${PINKY_SSID:-pinky_6294}"
PINKY_DOMAIN_ID="${PINKY_DOMAIN_ID:-21}"
XY_TOLERANCE="0.25"
YAW_TOLERANCE="0.25"
INFLATION_RADIUS="0.15"

NAV2_PID=""
RVIZ_PID=""
ACTION_OUTPUT_FILE=""
CLEANED_UP=false

declare -a WAYPOINT_KEYS=()
declare -a WAYPOINT_LABELS=()
declare -a WAYPOINT_XS=()
declare -a WAYPOINT_YS=()
declare -a WAYPOINT_YAWS=()

usage() {
  cat <<'EOF'
사용법:
  run_nav2_waypoint_test.sh [--xy <meter>] [--yaw <radian>] [--inflation <meter>]

옵션:
  --xy <meter>    위치 도착 허용 오차 (기본값: 0.25)
  --yaw <radian>  방향 도착 허용 오차 (기본값: 0.25)
  --inflation <meter>
                  장애물 팽창 반경 (기본값: 0.15)
  -h, --help      도움말 출력

예시:
  run_nav2_waypoint_test.sh --xy 0.15 --yaw 0.25 --inflation 0.13
EOF
}

fail() {
  echo "[실패] $1" >&2
  exit 1
}

is_positive_number() {
  awk -v value="$1" 'BEGIN {
    exit !(value ~ /^[0-9]+([.][0-9]+)?$/ && value > 0)
  }'
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

  if [[ -n "${ACTION_OUTPUT_FILE}" && -f "${ACTION_OUTPUT_FILE}" ]]; then
    rm -f -- "${ACTION_OUTPUT_FILE}"
    ACTION_OUTPUT_FILE=""
  fi

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
    if [[ "${state}" == active* ]]; then
      if ros2 action list 2>/dev/null | grep -Fxq '/navigate_to_pose'; then
        echo "[확인] Nav2 controller와 /navigate_to_pose가 활성화되었습니다."
        return 0
      fi
    fi
    sleep 1
    ((elapsed += 1))
  done

  return 1
}

load_waypoints() {
  local key label x y yaw

  while IFS=$'\t' read -r key label x y yaw; do
    [[ -n "${key}" && -n "${x}" && -n "${y}" && -n "${yaw}" ]] || continue
    WAYPOINT_KEYS+=("${key}")
    WAYPOINT_LABELS+=("${label:-${key}}")
    WAYPOINT_XS+=("${x}")
    WAYPOINT_YS+=("${y}")
    WAYPOINT_YAWS+=("${yaw}")
  done < <(
    awk '
      /^  # / {
        label = $0
        sub(/^  # /, "", label)
        next
      }
      /^  [[:alnum:]_]+:$/ {
        key = $1
        sub(/:$/, "", key)
        x = y = yaw = ""
        next
      }
      /^    x:/ { x = $2; next }
      /^    y:/ { y = $2; next }
      /^    yaw:/ {
        yaw = $2
        printf "%s\t%s\t%s\t%s\t%s\n", key, label, x, y, yaw
      }
    ' "${WAYPOINT_FILE}"
  )

  ((${#WAYPOINT_KEYS[@]} > 0)) \
    || fail "waypoint를 읽지 못했습니다: ${WAYPOINT_FILE}"
}

print_waypoint_menu() {
  local index

  echo
  echo "================ 저장된 waypoint ================"
  for index in "${!WAYPOINT_KEYS[@]}"; do
    printf '[%2d] %-34s (%s)\n' \
      "$((index + 1))" "${WAYPOINT_LABELS[index]}" "${WAYPOINT_KEYS[index]}"
  done
  echo "[ q] 종료"
  echo "==================================================="
}

print_tolerance_warning() {
  local previous_index="$1"
  local target_index="$2"
  local result distance yaw_difference

  result="$(awk \
    -v x1="${WAYPOINT_XS[previous_index]}" \
    -v y1="${WAYPOINT_YS[previous_index]}" \
    -v yaw1="${WAYPOINT_YAWS[previous_index]}" \
    -v x2="${WAYPOINT_XS[target_index]}" \
    -v y2="${WAYPOINT_YS[target_index]}" \
    -v yaw2="${WAYPOINT_YAWS[target_index]}" '
      BEGIN {
        pi = atan2(0, -1)
        distance = sqrt((x2 - x1)^2 + (y2 - y1)^2)
        yaw_difference = yaw2 - yaw1
        while (yaw_difference > pi) yaw_difference -= 2 * pi
        while (yaw_difference < -pi) yaw_difference += 2 * pi
        if (yaw_difference < 0) yaw_difference = -yaw_difference
        printf "%.3f %.3f", distance, yaw_difference
      }
    ')"
  read -r distance yaw_difference <<<"${result}"

  echo "[비교] ${WAYPOINT_LABELS[previous_index]} → ${WAYPOINT_LABELS[target_index]}"
  echo "       저장 좌표 거리: ${distance}m"
  echo "       저장 방향 차이: ${yaw_difference}rad"

  if awk -v distance="${distance}" -v tolerance="${XY_TOLERANCE}" \
    'BEGIN {exit !(distance <= tolerance)}'; then
    if awk -v difference="${yaw_difference}" -v tolerance="${YAW_TOLERANCE}" \
      'BEGIN {exit !(difference <= tolerance)}'; then
      echo "[경고] 위치와 방향이 모두 현재 tolerance 안쪽입니다."
      echo "       로봇이 이동하지 않고 즉시 성공할 가능성이 있습니다."
    else
      echo "[경고] 위치는 현재 tolerance 안쪽이고 방향은 바깥입니다."
      echo "       로봇이 평행 이동 없이 제자리 회전만 할 가능성이 있습니다."
    fi
  else
    echo "[예상] 목표가 위치 tolerance 바깥이므로 위치 이동이 필요합니다."
  fi
}

send_waypoint_goal() {
  local index="$1"
  local x="${WAYPOINT_XS[index]}"
  local y="${WAYPOINT_YS[index]}"
  local yaw="${WAYPOINT_YAWS[index]}"
  local qz qw goal
  local action_succeeded=false

  qz="$(awk -v yaw="${yaw}" 'BEGIN {printf "%.12f", sin(yaw / 2)}')"
  qw="$(awk -v yaw="${yaw}" 'BEGIN {printf "%.12f", cos(yaw / 2)}')"
  goal="{pose: {header: {frame_id: map}, pose: {position: {x: ${x}, y: ${y}, z: 0.0}, orientation: {x: 0.0, y: 0.0, z: ${qz}, w: ${qw}}}}}"

  echo "[이동] ${WAYPOINT_LABELS[index]} (${WAYPOINT_KEYS[index]})"
  echo "       x=${x}, y=${y}, yaw=${yaw}"

  ACTION_OUTPUT_FILE="$(mktemp)"
  if ros2 action send_goal \
      /navigate_to_pose \
      nav2_msgs/action/NavigateToPose \
      "${goal}" \
      --feedback | tee "${ACTION_OUTPUT_FILE}"; then
    if grep -Fq 'Goal finished with status: SUCCEEDED' "${ACTION_OUTPUT_FILE}"; then
      action_succeeded=true
    fi
  fi

  rm -f -- "${ACTION_OUTPUT_FILE}"
  ACTION_OUTPUT_FILE=""

  if [[ "${action_succeeded}" == true ]]; then
    return 0
  fi

  return 1
}

while (($# > 0)); do
  case "$1" in
    --xy)
      (($# >= 2)) || fail "--xy 뒤에 값을 입력하세요."
      XY_TOLERANCE="$2"
      shift 2
      ;;
    --yaw)
      (($# >= 2)) || fail "--yaw 뒤에 값을 입력하세요."
      YAW_TOLERANCE="$2"
      shift 2
      ;;
    --inflation)
      (($# >= 2)) || fail "--inflation 뒤에 값을 입력하세요."
      INFLATION_RADIUS="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "알 수 없는 옵션입니다: $1"
      ;;
  esac
done

is_positive_number "${XY_TOLERANCE}" \
  || fail "--xy는 0보다 큰 숫자여야 합니다: ${XY_TOLERANCE}"
is_positive_number "${YAW_TOLERANCE}" \
  || fail "--yaw는 0보다 큰 숫자여야 합니다: ${YAW_TOLERANCE}"
is_positive_number "${INFLATION_RADIUS}" \
  || fail "--inflation은 0보다 큰 숫자여야 합니다: ${INFLATION_RADIUS}"

MINGKY_REPO="$(resolve_repo_root)"
[[ -n "${MINGKY_REPO}" ]] \
  || fail "저장소 경로를 찾을 수 없습니다. MINGKY_REPO 환경 변수를 지정하세요."

MAP_PATH="${MAP_PATH:-${MINGKY_REPO}/pinky_map/pinky_6294/yun_map.yaml}"
WAYPOINT_FILE="${WAYPOINT_FILE:-${MINGKY_REPO}/mingky_ros/mingky_bringup/config/hospital_waypoints.yaml}"

[[ -f /opt/ros/jazzy/setup.bash ]] \
  || fail "ROS 2 Jazzy setup 파일을 찾을 수 없습니다."
[[ -f "${MINGKY_REPO}/install/setup.bash" ]] \
  || fail "프로젝트 install/setup.bash가 없습니다. 먼저 프로젝트를 빌드하세요."
[[ -f "${MAP_PATH}" ]] \
  || fail "지도 파일을 찾을 수 없습니다: ${MAP_PATH}"
[[ -f "${WAYPOINT_FILE}" ]] \
  || fail "waypoint 파일을 찾을 수 없습니다: ${WAYPOINT_FILE}"

command -v iwgetid >/dev/null 2>&1 \
  || fail "iwgetid 명령을 찾을 수 없습니다."
command -v ping >/dev/null 2>&1 \
  || fail "ping 명령을 찾을 수 없습니다."

load_waypoints

CURRENT_SSID="$(iwgetid -r 2>/dev/null || true)"
[[ "${CURRENT_SSID}" == "${PINKY_SSID}" ]] \
  || fail "현재 Wi-Fi는 '${CURRENT_SSID:-연결 안 됨}'입니다. PC를 '${PINKY_SSID}'에 연결하세요."
echo "[확인] Wi-Fi: ${CURRENT_SSID}"

ping -c 1 -W 2 "${PINKY_IP}" >/dev/null 2>&1 \
  || fail "Pinky(${PINKY_IP})에 연결할 수 없습니다."
echo "[확인] Pinky 응답: ${PINKY_IP}"

# setup.bash는 정의되지 않은 변수를 참조할 수 있으므로 set -u 전에 불러온다.
# shellcheck disable=SC1091
source /opt/ros/jazzy/setup.bash
# shellcheck disable=SC1090
source "${MINGKY_REPO}/install/setup.bash"
set -u

export ROS_DOMAIN_ID="${PINKY_DOMAIN_ID}"
export ROS_LOCALHOST_ONLY=0

ros2 daemon stop >/dev/null 2>&1 || true
ros2 daemon start >/dev/null

wait_for_publisher /odom 15 \
  || fail "/odom publisher가 없습니다. Pinky bringup과 Domain ID를 확인하세요."
wait_for_publisher /scan 15 \
  || fail "/scan publisher가 없습니다. Pinky LiDAR와 bringup을 확인하세요."

if ros2 node list 2>/dev/null | grep -Fxq '/controller_server'; then
  fail "이미 /controller_server가 실행 중입니다. 기존 Nav2를 먼저 종료하세요."
fi

echo "[실행] yun_map으로 Nav2를 시작합니다."
ros2 launch pinky_navigation bringup_launch.xml \
  map:="${MAP_PATH}" use_composition:=False &
NAV2_PID=$!

wait_for_nav2 40 \
  || fail "Nav2가 제한 시간 안에 활성화되지 않았습니다. 위 로그를 확인하세요."

ros2 param set /controller_server \
  general_goal_checker.xy_goal_tolerance "${XY_TOLERANCE}" >/dev/null \
  || fail "xy_goal_tolerance를 적용하지 못했습니다."
ros2 param set /controller_server \
  general_goal_checker.yaw_goal_tolerance "${YAW_TOLERANCE}" >/dev/null \
  || fail "yaw_goal_tolerance를 적용하지 못했습니다."
ros2 param set /global_costmap/global_costmap \
  inflation_layer.inflation_radius "${INFLATION_RADIUS}" >/dev/null \
  || fail "global costmap inflation_radius를 적용하지 못했습니다."
ros2 param set /local_costmap/local_costmap \
  inflation_layer.inflation_radius "${INFLATION_RADIUS}" >/dev/null \
  || fail "local costmap inflation_radius를 적용하지 못했습니다."

APPLIED_XY="$(ros2 param get /controller_server general_goal_checker.xy_goal_tolerance \
  | awk '{print $NF}')"
APPLIED_YAW="$(ros2 param get /controller_server general_goal_checker.yaw_goal_tolerance \
  | awk '{print $NF}')"
APPLIED_GLOBAL_INFLATION="$(ros2 param get /global_costmap/global_costmap \
  inflation_layer.inflation_radius | awk '{print $NF}')"
APPLIED_LOCAL_INFLATION="$(ros2 param get /local_costmap/local_costmap \
  inflation_layer.inflation_radius | awk '{print $NF}')"
echo "[확인] 적용된 위치 tolerance: ${APPLIED_XY}m"
echo "[확인] 적용된 방향 tolerance: ${APPLIED_YAW}rad"
echo "[확인] 적용된 global inflation radius: ${APPLIED_GLOBAL_INFLATION}m"
echo "[확인] 적용된 local inflation radius: ${APPLIED_LOCAL_INFLATION}m"

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
3. 준비가 끝나면 이 터미널로 돌아와 Enter를 누르세요.
4. 메뉴에서 저장된 waypoint를 선택하면 Nav2가 주행합니다.

주행 중에는 teleop을 함께 실행하지 마세요.
전체 테스트를 종료하려면 메뉴에서 q를 선택하거나 Ctrl+C를 누르세요.
============================================================

EOF
read -r -p "초기 위치 설정을 마쳤으면 Enter를 누르세요: "

PREVIOUS_INDEX=""

while true; do
  print_waypoint_menu
  read -r -p "이동할 위치 번호를 선택하세요: " selection

  if [[ "${selection}" == "q" || "${selection}" == "Q" ]]; then
    break
  fi

  if [[ ! "${selection}" =~ ^[0-9]+$ ]] \
    || ((selection < 1 || selection > ${#WAYPOINT_KEYS[@]})); then
    echo "[안내] 1~${#WAYPOINT_KEYS[@]} 사이의 번호 또는 q를 입력하세요."
    continue
  fi

  TARGET_INDEX=$((selection - 1))

  if [[ -n "${PREVIOUS_INDEX}" ]]; then
    print_tolerance_warning "${PREVIOUS_INDEX}" "${TARGET_INDEX}"
  else
    echo "[안내] 첫 목표입니다. 다음 목표부터 waypoint 간 tolerance를 비교합니다."
  fi

  read -r -p "이 목표로 주행할까요? [y/N]: " confirmation
  if [[ "${confirmation}" != "y" && "${confirmation}" != "Y" ]]; then
    echo "[취소] 목표를 전송하지 않았습니다."
    continue
  fi

  if send_waypoint_goal "${TARGET_INDEX}"; then
    echo "[완료] ${WAYPOINT_LABELS[TARGET_INDEX]} 목표 요청이 성공으로 종료되었습니다."
    PREVIOUS_INDEX="${TARGET_INDEX}"
  else
    echo "[실패] ${WAYPOINT_LABELS[TARGET_INDEX]} 목표 요청이 실패하거나 취소되었습니다."
  fi
done
