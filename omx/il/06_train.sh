#!/usr/bin/env bash
# [6단계] 학습 — 찍은 데이터로 ACT 정책을 학습한다.
#
#   bash 06_train.sh                  # 기본 40000 스텝
#   STEPS=80000 bash 06_train.sh      # 더 길게
#   RUN=bottle_v2 bash 06_train.sh    # 다른 이름으로 저장
#
# 로봇·카메라는 필요 없다 (데이터만 있으면 된다). 몇 시간 걸리므로 끄지 말 것.
# 진행 상황은 다른 터미널에서:  tail -f ~/train/<이름>.log
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
source "$HERE/_common.sh"

RUN=${RUN:-act_pill_bottle_v1}
STEPS=${STEPS:-40000}
OUT=$HOME/train/$RUN
LOG=$HOME/train/$RUN.log
mkdir -p "$HOME/train"

if [ -d "$OUT" ]; then
  echo "! $OUT 가 이미 있습니다. 이어서 학습하려면 --resume, 새로 하려면 RUN= 로 다른 이름을 주세요."
  exit 1
fi

# GPU 메모리에 맞춰 배치 크기를 고른다. 넘치면 OOM 으로 죽고, 죽으면 처음부터 다시다.
VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 || echo 0)
if [ "${VRAM:-0}" -ge 20000 ]; then BATCH=32
elif [ "${VRAM:-0}" -ge 12000 ]; then BATCH=16
elif [ "${VRAM:-0}" -ge 7000 ]; then BATCH=8
else BATCH=4; fi
BATCH=${BATCH_SIZE:-$BATCH}

echo "=============================================="
echo " 데이터셋 : $REPO"
echo " 저장     : $OUT"
echo " 스텝     : $STEPS  (8000 스텝마다 체크포인트 저장)"
echo " VRAM     : ${VRAM}MB → batch_size=$BATCH"
echo " 로그     : $LOG"
echo "=============================================="
echo
echo " ★ --dataset.image_transforms.enable=true (색·밝기 증강) 를 켜고 갑니다."
echo "   이걸 껐다가 3일을 날렸습니다. 증강이 없으면 장면을 통째로 외워버려서"
echo "   '무엇을 보고 판단해야 하는지'를 배우지 않습니다. 끄지 마세요."
echo

lerobot-train \
  --dataset.repo_id="$REPO" \
  --dataset.image_transforms.enable=true \
  --policy.type=act \
  --policy.device=cuda \
  --policy.use_amp=true \
  --batch_size="$BATCH" \
  --steps="$STEPS" \
  --save_freq=8000 \
  --output_dir="$OUT" \
  --policy.push_to_hub=false \
  2>&1 | tee "$LOG"

echo
echo "학습 끝. 실기에서 돌려보기:"
echo "  RUN=$RUN bash 07_run.sh"
