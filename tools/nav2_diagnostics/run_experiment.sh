#!/usr/bin/env bash

# Nav2 수동 주행 실험을 프로파일별로 재현하고 SQLite 기록을 남긴다.
set -eo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "${SCRIPT_DIR}" rev-parse --show-toplevel)"
PROFILE_DIR="${SCRIPT_DIR}/profiles"
BASE_PARAMS="${REPO_ROOT}/pinky/pinky_navigation/params/nav2_params.yaml"
DEFAULT_MAP="${REPO_ROOT}/mingky_ros/mingky_bringup/map/yun_map_highres_clean.yaml"
ARTIFACT_DIR="${REPO_ROOT}/artifacts/nav2_diagnostics"

PROFILE="${PROFILE_DIR}/baseline.yaml"
MAP_PATH="${DEFAULT_MAP}"
LABEL="manual-nav2-test"
NOTES=""
SAMPLE_HZ="5"
RECORDER_PID=""

usage() {
  cat <<'EOF'
사용법:
  run_experiment.sh [옵션]

옵션:
  --profile <파일>     적용할 실험 프로파일 (기본: baseline.yaml)
  --map <파일>         사용할 지도 YAML
  --label <이름>       실험 목적 이름
  --notes <메모>       SQLite에 남길 추가 메모
  --sample-hz <Hz>     요약 수치 기록 주기 (기본: 5)
  --list-profiles      제공 프로파일 목록 출력
  -h, --help           도움말 출력

실행 중 RViz에서 2D Pose Estimate 후 Nav2 Goal을 직접 지정합니다.
원본 rosbag은 만들지 않으며, 실패 시점의 costmap·경로만 압축 SQLite에 남깁니다.
EOF
}

fail() { echo "[실패] $1" >&2; exit 1; }

while (($# > 0)); do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --map) MAP_PATH="$2"; shift 2 ;;
    --label) LABEL="$2"; shift 2 ;;
    --notes) NOTES="$2"; shift 2 ;;
    --sample-hz) SAMPLE_HZ="$2"; shift 2 ;;
    --list-profiles) find "${PROFILE_DIR}" -maxdepth 1 -type f -name '*.yaml' -printf '%f\n' | sort; exit 0 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "알 수 없는 옵션입니다: $1" ;;
  esac
done

[[ -f "${BASE_PARAMS}" ]] || fail "기본 Nav2 파라미터가 없습니다: ${BASE_PARAMS}"
[[ -f "${PROFILE}" ]] || fail "프로파일이 없습니다: ${PROFILE}"
[[ -f "${MAP_PATH}" ]] || fail "지도가 없습니다: ${MAP_PATH}"
[[ "${SAMPLE_HZ}" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "--sample-hz는 숫자여야 합니다."

safe_label="$(tr -cs '[:alnum:]_-' '_' <<<"${LABEL}" | sed 's/^_*//; s/_*$//')"
run_id="$(date +%Y%m%d_%H%M%S)_${safe_label:-nav2}"
run_dir="${ARTIFACT_DIR}/${run_id}"
db_path="${run_dir}/run.sqlite"
effective_params="${run_dir}/effective_nav2_params.yaml"
mkdir -p "${run_dir}"

cleanup() {
  if [[ -n "${RECORDER_PID}" ]] && kill -0 "${RECORDER_PID}" 2>/dev/null; then
    kill -INT "${RECORDER_PID}" 2>/dev/null || true
    wait "${RECORDER_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

python3 "${SCRIPT_DIR}/merge_params.py" \
  --base "${BASE_PARAMS}" --profile "${PROFILE}" --output "${effective_params}"

source /opt/ros/jazzy/setup.bash
source "${REPO_ROOT}/install/setup.bash"
export ROS_DOMAIN_ID="${PINKY_DOMAIN_ID:-21}"
unset ROS_LOCALHOST_ONLY
export ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET

echo "[실험] ${run_id}"
echo "[실험] 지도: ${MAP_PATH}"
echo "[실험] 프로파일: ${PROFILE}"
echo "[기록] ${db_path}"

python3 "${SCRIPT_DIR}/recorder.py" \
  --db "${db_path}" --run-id "${run_id}" --repo "${REPO_ROOT}" \
  --map "${MAP_PATH}" --profile "${PROFILE}" --effective-params "${effective_params}" \
  --label "${LABEL}" --notes "${NOTES}" --ros-domain-id "${ROS_DOMAIN_ID}" --sample-hz "${SAMPLE_HZ}" &
RECORDER_PID=$!

sleep 1
MINGKY_REPO="${REPO_ROOT}" MINGKY_MAP_PATH="${MAP_PATH}" NAV2_PARAMS_FILE="${effective_params}" \
  bash "${REPO_ROOT}/mingky_ros/mingky_bringup/scripts/run_nav2_manual_test.sh"

cleanup
RECORDER_PID=""
python3 "${SCRIPT_DIR}/report.py" "${db_path}" || true
