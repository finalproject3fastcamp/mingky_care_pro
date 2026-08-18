#!/usr/bin/env bash
# [6-VLA] SmolVLA 파인튜닝 — ACT 대신 시험할 때 쓰는 학습 스크립트.
#
#   bash 06_train_vla.sh                        # 기본 20000 스텝
#   STEPS=60000 bash 06_train_vla.sh            # 더 길게
#   RUN=smolvla_pill_bottle_v2 bash 06_train_vla.sh
#   BATCH_SIZE=8 bash 06_train_vla.sh           # OOM 나면 강제로 낮춘다
#
# 로봇·카메라는 필요 없다 (데이터만 있으면 된다).
# 몇 시간 걸리므로 끄지 말 것. 진행은 다른 터미널에서: tail -f ~/train/<이름>.log
#
# ─────────────────────────────────────────────────────────────────────
#  이 스크립트가 왜 06_train.sh 와 별개인가
# ─────────────────────────────────────────────────────────────────────
#  · ACT 는 LeRobot v0.4.4 CLI (06_train.sh) 로 굳어져 있다.
#  · SmolVLA 는 LeRobot v0.6.x 부터 지원된다 — 같은 venv 에 섞어 설치하면
#    v0.4.4 의 CLI 옵션 이름들이 바뀌어 ACT 스크립트가 전부 깨진다.
#  · 그래서 venv 를 따로 둔다: 기본 ~/venv/il_vla  (환경변수 VLA_VENV 로 덮어씀)
#  · _common.sh 를 source 하지 않는 이유도 이것 — 그쪽은 v0.4.4 venv 를 활성화한다.
#
#  ─────────────────────────────────────────────────────────────────────
#   [TODO] 아직 검증되지 않은 것 — 첫 실행 전에 확인
#  ─────────────────────────────────────────────────────────────────────
#   1) v0.4.4 로 녹화된 데이터셋을 v0.6.x 로더가 그대로 읽는지 (스키마 마이너 차이 가능)
#      → STEPS=200 으로 dry-run 먼저 돌려서 로더 에러가 없는지 본다.
#   2) SmolVLA 사전학습 체크포인트 경로 (lerobot/smolvla_base) 가 유효한지 HF 에서 확인.
#   3) --policy.type=smolvla 아래의 서브 옵션 이름은 LeRobot 릴리스마다 미세하게 다르다.
#      릴리스노트로 대조하고, 다르면 policy.* 쪽만 손본다.
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

# _common.sh 는 참조만 한다 (source 는 하지 않음 — 다른 venv).
# REPO/TASK 는 그쪽과 반드시 같은 값을 쓴다. 데이터셋이 하나이므로.
REPO=${REPO:-mingky/pill_bottle_v1}
TASK=${TASK:-"put the pill bottle in the envelope"}

# --- venv 활성화 --------------------------------------------------------
VENV=${VLA_VENV:-$HOME/venv/il_vla}
if [ ! -d "$VENV" ]; then
  echo "! SmolVLA 용 venv 가 없습니다: $VENV"
  echo "  아직 01_install_vla.sh 를 안 만들었다면 아래처럼 손으로 만들어 둡니다:"
  echo
  echo "    python3 -m venv $VENV"
  echo "    source $VENV/bin/activate"
  echo "    pip install --upgrade pip"
  echo "    pip install 'lerobot[smolvla]>=0.6.0'"
  echo
  echo "  (기존 ~/venv/il 은 건드리지 말 것 — ACT 스크립트가 거기 붙어 있음)"
  exit 1
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

# --- 실행 설정 ----------------------------------------------------------
RUN=${RUN:-smolvla_pill_bottle_v1}
STEPS=${STEPS:-20000}          # SmolVLA 는 사전학습이 있어 ACT(40k)보다 짧아도 붙는다
BASE=${BASE_MODEL:-lerobot/smolvla_base}
OUT=$HOME/train/$RUN
LOG=$HOME/train/$RUN.log
mkdir -p "$HOME/train"

if [ -d "$OUT" ]; then
  echo "! $OUT 가 이미 있습니다. 새로 하려면 RUN= 로 다른 이름을 주세요."
  exit 1
fi

# --- 배치 크기 자동 결정 ------------------------------------------------
# SmolVLA(~450M) 는 ACT(~수십 M) 보다 무겁다. 같은 VRAM 기준으로 반 정도로 잡는다.
# 여기 숫자들은 실측 아니라 근사치다 — OOM 뜨면 BATCH_SIZE=... 로 강제로 낮추면 된다.
VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 || echo 0)
if   [ "${VRAM:-0}" -ge 22000 ]; then BATCH=32
elif [ "${VRAM:-0}" -ge 14000 ]; then BATCH=16
elif [ "${VRAM:-0}" -ge 10000 ]; then BATCH=8
else                                  BATCH=4
fi
BATCH=${BATCH_SIZE:-$BATCH}

echo "=============================================="
echo " 모드      : SmolVLA 파인튜닝"
echo " 사전학습  : $BASE"
echo " 데이터셋  : $REPO"
echo " 작업 문장 : $TASK"
echo " 저장      : $OUT"
echo " 스텝      : $STEPS  (5000 스텝마다 체크포인트)"
echo " VRAM      : ${VRAM}MB → batch_size=$BATCH"
echo " 로그      : $LOG"
echo "=============================================="
echo
echo " ★ --dataset.image_transforms.enable=true 유지 — ACT 때와 같은 이유(암기 방지)."
echo " ★ 단일 작업(약통 1종)에서는 SmolVLA 의 언어 조건화 이점이 잘 안 산다."
echo "   여러 작업으로 늘렸을 때(약통 여러 종·트레이 칸 지정 등) ACT 대비 실효가 난다."
echo "   지금은 ACT 대비 baseline 비교용으로 돌린다."
echo

# --- 학습 실행 ---------------------------------------------------------
# CLI 는 LeRobot v0.6.x 기준. 릴리스마다 --policy.* 하위 옵션명이 조금씩 바뀌므로
# 첫 실행 때 'lerobot-train --policy.type=smolvla --help' 로 대조해 두면 안전하다.
lerobot-train \
  --dataset.repo_id="$REPO" \
  --dataset.image_transforms.enable=true \
  --policy.type=smolvla \
  --policy.path="$BASE" \
  --policy.device=cuda \
  --policy.use_amp=true \
  --batch_size="$BATCH" \
  --steps="$STEPS" \
  --save_freq=5000 \
  --output_dir="$OUT" \
  --policy.push_to_hub=false \
  2>&1 | tee "$LOG"

echo
echo "학습 끝. 실기에서 돌려보기 (07_run.sh 를 그대로 쓰되 RUN 만 바꾼다):"
echo "  RUN=$RUN CKPT=last bash 07_run.sh"
echo
echo "ACT 와 성능 비교하려면 같은 초기 조건(카메라 위치·조명·물건 배치) 에서 각각 10회씩."
