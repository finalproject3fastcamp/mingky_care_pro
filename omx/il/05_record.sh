#!/usr/bin/env bash
# [5단계] 데이터 녹화 — 리더로 시연하고, 그걸 학습 데이터로 저장한다.
#
#   bash 05_record.sh 20          # 새로 시작 (20개)
#   bash 05_record.sh 20 resume   # 이어 찍기 (20개 더)
#
# ★ 개수는 "이번 실행에서 **추가로** 찍을 개수"다. 누적 목표가 아니다.
# ★ 중간에 프로그램이 죽어도 저장된 데이터는 멀쩡하다. resume 으로 이어 찍으면 된다.
#
# 데이터 저장 위치: ~/.cache/huggingface/lerobot/<REPO>/
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
source "$HERE/_common.sh"
check_ready

COUNT=${1:?"개수를 지정하세요:  bash 05_record.sh 20"}
RESUME=""
[ "${2:-}" = "resume" ] && RESUME="--resume=true"

CAMS=$(build_cams)
[ -n "$CAMS" ] || { echo "카메라 설정이 없습니다. 먼저: bash 03_check_cameras.sh --view"; exit 1; }

EP=${EP:-60}        # 에피소드 시간 상한(초). → 로 직접 넘기는 게 기본
RESET=${RESET:-60}  # 리셋(물건 다시 놓는) 시간 상한(초)

echo "=============================================="
echo " 데이터셋 : $REPO ${RESUME:+(이어 찍기)}"
echo " 작업     : $TASK"
echo " 개수     : $COUNT 개 (이번 실행에서 추가)"
echo " 시간     : 에피소드 ${EP}s / 리셋 ${RESET}s (→ 로 직접 넘김)"
echo "=============================================="
echo
echo " [키] — 이 터미널 창에 포커스를 두고 누르세요 (rerun 창 아님)"
echo "   →   시연 끝났음 / 배치 끝났음 (한 에피소드에 두 번: 시연 후, 배치 후)"
echo "   ←   지금 에피소드 버리고 다시 찍기"
echo "   ESC 중단"
echo
echo " [찍을 때 지킬 것]"
echo " · 매번 물건 위치와 각도를 바꾼다. 같은 자리에 두면 모델이 위치를 외워버린다"
echo "   (우리는 30개를 눈대중으로 찍었다가 딱 3자리에만 몰려서 전부 다시 찍었다)"
echo " · 시연 속도는 일정하게. 너무 빠르면 학습이 어려워진다"
echo " · 실패했다 다시 잡는 '회복 시연'도 10% 정도 섞으면 좋다"
echo "   (단, 그러면 물건이 없을 때 안 멈추고 계속 헤매는 부작용이 생긴다 — 반반이다)"
echo
echo " [순서] 첫 에피소드도 → 를 눌러야 시작합니다. 물건 놓고 누르세요."
echo

lerobot-record \
  $RESUME \
  --robot.type=omx_follower --robot.port="$FOLLOWER_PORT" --robot.id="$FOLLOWER_ID" \
  --robot.cameras="$CAMS" \
  --teleop.type=omx_leader --teleop.port="$LEADER_PORT" --teleop.id="$LEADER_ID" \
  --dataset.repo_id="$REPO" \
  --dataset.single_task="$TASK" \
  --dataset.num_episodes="$COUNT" \
  --dataset.episode_time_s="$EP" \
  --dataset.reset_time_s="$RESET" \
  --dataset.fps=30 \
  --dataset.push_to_hub=false \
  --display_data=true

echo
echo "녹화 끝. 몇 개가 쌓였는지 확인:"
echo "  ls ~/.cache/huggingface/lerobot/$REPO/videos/chunk-000/observation.images.top | wc -l"
echo
echo "30~50개쯤 모였으면 학습:  bash 06_train.sh"
