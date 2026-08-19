#!/usr/bin/env bash
# [7단계] 학습된 정책을 실기에서 돌린다 (= 평가).
#
#   bash 07_run.sh                       # last 체크포인트로 10회
#   RUN=act_pill_bottle_v1 bash 07_run.sh 5   # 5회만
#   CKPT=040000 bash 07_run.sh           # 특정 체크포인트
#
# ⚠️ 처음 돌릴 때는 **비상 정지 준비**를 하세요. 손을 로봇 전원 스위치 근처에 두고,
#    작업대에 부딪힐 만한 물건을 치우고 시작합니다. 학습된 대로 움직이지 않을 수 있습니다.
#
# lerobot-record 로 도는 구조라 결과가 데이터셋으로도 남습니다 (나중에 영상으로 다시 봄).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
source "$HERE/_common.sh"
check_ready

RUN=${RUN:-act_pill_bottle_v1}
CKPT=${CKPT:-last}
COUNT=${1:-10}
POLICY=$HOME/train/$RUN/checkpoints/$CKPT/pretrained_model

if [ ! -d "$POLICY" ]; then
  echo "! 체크포인트가 없습니다: $POLICY"
  echo "  있는 것들:"
  ls "$HOME/train/$RUN/checkpoints" 2>/dev/null || echo "   (학습 결과가 아예 없습니다 — 06_train.sh 먼저)"
  exit 1
fi

CAMS=$(build_cams)
[ -n "$CAMS" ] || { echo "카메라 설정이 없습니다: bash 03_check_cameras.sh --view"; exit 1; }

# 평가 결과를 담을 데이터셋 이름.
# lerobot 은 정책을 주고 녹화할 때 이름이 **eval_ 로 시작**하기를 요구한다
# (control_utils.sanity_check_dataset_name). 접미사 _eval 로는 시작하자마자 죽는다.
# REPO 는 "계정/이름" 형태이므로 슬래시 뒤에만 접두사를 붙인다.
EVAL_REPO="${REPO%/*}/eval_${REPO##*/}"

echo "=============================================="
echo " 정책   : $POLICY"
echo " 작업   : $TASK"
echo " 횟수   : $COUNT"
echo " 결과   : $EVAL_REPO 로 저장"
echo "=============================================="
echo
echo " ★ 카메라 위치·각도가 **녹화할 때와 같아야** 합니다. 조금만 밀려도 성능이 떨어집니다."
echo "   (우리는 화각이 6px 밀린 걸 모르고 평가하다 하루를 날렸습니다. 카메라를 테이프로 고정하세요.)"
echo " ★ 조명도 녹화 때와 비슷하게. 커튼 열고 닫는 것만으로도 달라집니다."
echo
echo " [키] — 이 터미널 창에 포커스를 두고 누르세요"
echo "   스페이스  사람이 개입/복귀 (DAgger). 리더 팔이 꽂혀 있어야 합니다."
echo "             켜는 순간 리더가 팔로워 자세로 자동 정렬됩니다 — 1.5초 동안 리더에서 손을 떼세요."
echo "             개입한 시간은 에피소드 시간 상한에서 빠집니다."
echo "   →         이번 회차 끝     ←  이번 회차 버리고 다시     ESC  중단"
echo
echo " 시작하려면 엔터, 그만두려면 Ctrl+C."
read -r _

# 리더도 같이 붙인다 — 스페이스바 개입(DAgger)에 필요하다. 개입하지 않으면 정책이 그대로 몬다.
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
echo "끝났습니다. 잘 안 됐다면 — 데이터를 더 모으기 **전에** 원인부터 좁히세요:"
echo "  · 엉뚱한 데로 간다      → 카메라 화각이 녹화 때와 같은지 (제일 흔한 원인)"
echo "  · 근처까지 가는데 못 잡음 → 시연 데이터의 파지 자세가 일관적이었는지"
echo "  · 아예 안 움직임        → 체크포인트/카메라 키 이름(top/wrist)이 맞는지"
echo "  · 계속 헤맴             → 회복 시연이 너무 많으면 '멈추는 법'을 못 배웁니다"
