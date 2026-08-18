#!/usr/bin/env bash
# [7-VLA] 학습된 SmolVLA 정책을 실기에서 돌린다 (= 평가).
#
#   bash 07_run_vla.sh                              # last 체크포인트로 10회
#   RUN=smolvla_pill_bottle_v1 bash 07_run_vla.sh 5 # 5회만
#   CKPT=020000 bash 07_run_vla.sh                  # 특정 체크포인트
#
# ⚠️ 처음 돌릴 때는 **비상 정지 준비**를 하세요. 손을 로봇 전원 스위치 근처에 두고,
#    작업대에 부딪힐 만한 물건을 치우고 시작합니다.
# ⚠️ 첫 액션이 나오기까지 SmolVLA 는 모델 로딩·초기 컴파일에 20~40초 걸린다.
#    "안 움직이네" 하고 손대지 말고 기다린다. 두 번째 회차부터는 즉시 시작한다.
#
# lerobot-record 로 도는 구조라 결과가 데이터셋으로도 남는다 (나중에 영상 확인용).
#
# ─────────────────────────────────────────────────────────────────────
#  이 스크립트가 왜 07_run.sh 와 별개인가
# ─────────────────────────────────────────────────────────────────────
#  · 06_train_vla.sh 와 같은 이유 — LeRobot v0.6.x + SmolVLA 는 별도 venv 에 있다.
#  · _common.sh 는 v0.4.4 venv 를 활성화하지만, 여기서 필요한 로봇 포트·카메라 설정
#    (FOLLOWER_PORT / LEADER_PORT / build_cams / check_ready) 도 거기 있다.
#  · 그래서 _common.sh 를 먼저 source 해서 설정만 챙기고, 그 뒤에 venv 를 갈아엎는다.
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

# 1) _common.sh 로 포트·카메라·REPO·TASK 를 로드. 부작용으로 il venv 가 활성화됨.
# shellcheck disable=SC1091
source "$HERE/_common.sh"

# 2) venv 를 SmolVLA 용으로 갈아엎는다.
deactivate 2>/dev/null || true
VLA_VENV=${VLA_VENV:-$HOME/venv/il_vla}
if [ ! -d "$VLA_VENV" ]; then
  echo "! SmolVLA 용 venv 가 없습니다: $VLA_VENV"
  echo "  06_train_vla.sh 상단의 안내대로 만들거나, 이미 만들었다면 VLA_VENV 를 지정하세요."
  exit 1
fi
# shellcheck disable=SC1091
source "$VLA_VENV/bin/activate"

# 3) 로봇·카메라 준비 상태 점검 (check_ready 는 _common.sh 에서 정의된 함수).
check_ready

# --- 실행 설정 ----------------------------------------------------------
RUN=${RUN:-smolvla_pill_bottle_v1}
CKPT=${CKPT:-last}
COUNT=${1:-10}
POLICY=$HOME/train/$RUN/checkpoints/$CKPT/pretrained_model

if [ ! -d "$POLICY" ]; then
  echo "! 체크포인트가 없습니다: $POLICY"
  echo "  있는 것들:"
  ls "$HOME/train/$RUN/checkpoints" 2>/dev/null || echo "   (학습 결과가 아예 없음 — 06_train_vla.sh 먼저)"
  exit 1
fi

CAMS=$(build_cams)
[ -n "$CAMS" ] || { echo "카메라 설정이 없습니다: bash 03_check_cameras.sh --view"; exit 1; }

# 평가 결과를 담을 데이터셋 이름.
# lerobot 은 정책을 주고 녹화할 때 이름이 **eval_ 로 시작**하기를 요구한다
# (control_utils.sanity_check_dataset_name). REPO 는 "계정/이름" 형태이므로
# 슬래시 뒤에만 접두사를 붙인다. ACT 평가(07_run.sh)와 겹치지 않게 _vla 도 붙인다.
EVAL_REPO="${REPO%/*}/eval_vla_${REPO##*/}"

echo "=============================================="
echo " 정책     : $POLICY  (SmolVLA)"
echo " 작업     : $TASK"
echo " 횟수     : $COUNT"
echo " 결과     : $EVAL_REPO 로 저장"
echo "=============================================="
echo
echo " ★ 카메라 위치·각도가 **녹화할 때와 같아야** 합니다. 조금만 밀려도 성능이 떨어집니다."
echo "   (ACT 때 화각 6px 밀린 걸 모르고 하루를 날린 사례 있음 — 카메라는 테이프로 고정)"
echo " ★ 조명도 녹화 때와 비슷하게. 커튼 열고 닫는 것만으로 달라짐."
echo " ★ TASK 문자열이 학습 때와 **글자 하나까지 같아야** 함 — SmolVLA 는 언어 조건화 정책이므로"
echo "   문장이 바뀌면 행동도 바뀔 수 있음. _common.sh 의 TASK 를 함부로 다듬지 말 것."
echo
echo " ★ SmolVLA 특유의 주의점"
echo "   · 첫 액션까지 20~40초 (모델 로딩·초기 warm-up). 그동안 손대지 말 것."
echo "   · 액션 청킹으로 예측하므로 한 번 이상한 방향을 잡으면 몇 스텝 밀고 나감."
echo "     빨리 개입해야 손상 안 남 — 스페이스바 반응이 ACT 때보다 더 중요."
echo
echo " [키] — 이 터미널 창에 포커스를 두고 누르세요"
echo "   스페이스  사람이 개입/복귀 (DAgger). 리더 팔이 꽂혀 있어야 함."
echo "             켜는 순간 리더가 팔로워 자세로 자동 정렬 — 1.5초 동안 리더에서 손을 떼세요."
echo "             개입한 시간은 에피소드 시간 상한에서 빠집니다."
echo "   →         이번 회차 끝     ←  이번 회차 버리고 다시     ESC  중단"
echo
echo " 시작하려면 엔터, 그만두려면 Ctrl+C."
read -r _

# 리더도 같이 붙인다 — 스페이스바 개입(DAgger)에 필요. 개입하지 않으면 정책이 그대로 몬다.
lerobot-record \
  --robot.type=omx_follower --robot.port="$FOLLOWER_PORT" --robot.id="$FOLLOWER_ID" \
  --robot.cameras="$CAMS" \
  --teleop.type=omx_leader --teleop.port="$LEADER_PORT" --teleop.id="$LEADER_ID" \
  --dataset.repo_id="$EVAL_REPO" \
  --dataset.single_task="$TASK" \
  --dataset.num_episodes="$COUNT" \
  --dataset.episode_time_s=60 \
  --dataset.reset_time_s=30 \
  --dataset.fps=30 \
  --dataset.push_to_hub=false \
  --policy.path="$POLICY" \
  --display_data=true

echo
echo "끝. ACT(07_run.sh) 결과와 나란히 비교하려면:"
echo "  · 같은 회차 조건(카메라·조명·물건 배치 시나리오)에서 10회 이상"
echo "  · 성공률·평균 완료 시간·개입 횟수를 TASK.md 에 나란히 적을 것"
echo
echo "잘 안 됐다면 데이터 늘리기 **전에** 원인부터 좁힌다:"
echo "  · 엉뚱한 데로 감          → 카메라 화각이 녹화 때와 같은지 (제일 흔한 원인)"
echo "  · 근처까지 가는데 못 잡음 → 시연 데이터의 파지 자세가 일관적이었는지"
echo "  · 아예 안 움직임          → warm-up 대기(20~40초) 충분히 줬는지, 카메라 키 이름(top/wrist) 일치 여부"
echo "  · TASK 문장을 살짝 바꿨다 → 그게 원인일 수 있음. 원문으로 돌리고 다시"
