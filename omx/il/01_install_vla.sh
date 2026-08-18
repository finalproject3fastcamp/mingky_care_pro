#!/usr/bin/env bash
# [1-VLA] SmolVLA 실험용 환경 설치 — 딱 한 번만 실행하면 된다.
#
#   bash 01_install_vla.sh
#
# 하는 일: 파이썬 가상환경(~/venv/il_vla) → LeRobot v0.6.x → SmolVLA extra 설치
#          → 사전학습 체크포인트(lerobot/smolvla_base) 사전 캐싱까지.
# 로봇·카메라는 안 건드린다. GPU 도 여기선 확인만 한다.
#
# ─────────────────────────────────────────────────────────────────────
#  왜 01_install.sh 와 분리하나
# ─────────────────────────────────────────────────────────────────────
#  · 01_install.sh 는 LeRobot v0.4.4 를 ~/venv/il 에 고정 설치한다 (ACT 전용).
#  · SmolVLA 는 v0.6.x 부터 지원 — 같은 venv 에 얹으면 v0.4.4 CLI 가 깨져서
#    02~07 스크립트가 다 오작동한다. 그래서 완전히 별도 venv 를 둔다.
#  · 시스템 패키지(ffmpeg·v4l-utils·dialout 그룹)는 01_install.sh 가 이미
#    깔았을 것이므로 여기서는 파이썬 쪽만 손댄다. (아직 안 깔았다면 먼저 01_install.sh)
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

VENV=${VLA_VENV:-$HOME/venv/il_vla}
LEROBOT_VERSION=${LEROBOT_VERSION:-0.6.0}     # v0.6 시리즈에서 SmolVLA 지원 시작
BASE_MODEL=${BASE_MODEL:-lerobot/smolvla_base}

echo "== 1/5  선결 조건 확인 =="
# 01_install.sh 가 이미 돌았는지 신호만 확인. 없어도 강제로 막지는 않지만 경고는 한다.
if ! command -v ffmpeg >/dev/null; then
  echo "  ! ffmpeg 가 없습니다. 먼저 01_install.sh 를 돌리거나 수동 설치:"
  echo "     sudo apt install -y ffmpeg v4l-utils python3-venv python3-dev"
  exit 1
fi
if ! groups | grep -qw dialout; then
  echo "  ! dialout 그룹이 아닙니다. 로봇 포트를 못 엽니다."
  echo "    01_install.sh 를 먼저 돌리고 로그아웃→로그인 후 다시 오세요."
  exit 1
fi
echo "  선결 조건 OK."

echo
echo "== 2/5  GPU 확인 =="
# SmolVLA(~450M) 는 CUDA 없이 CPU 로 파인튜닝 불가 (며칠 걸림). 확인만 하고 없어도 계속은 감.
if command -v nvidia-smi >/dev/null; then
  VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1)
  GPU=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)
  echo "  $GPU  (${VRAM} MB)"
  if [ "${VRAM:-0}" -lt 10000 ]; then
    echo "  ! VRAM 이 10GB 미만입니다. 파인튜닝은 batch_size=4 이하로 강제하세요."
  fi
else
  echo "  ! nvidia-smi 없음. CUDA GPU 가 없으면 파인튜닝은 사실상 불가합니다."
  echo "    계속 진행하지만 06_train_vla.sh 에서 막힐 수 있음."
fi

echo
echo "== 3/5  가상환경 $VENV =="
if [ -d "$VENV" ]; then
  echo "  이미 있습니다. 재사용."
else
  python3 -m venv "$VENV"
  echo "  생성 완료."
fi
# shellcheck disable=SC1090
source "$VENV/bin/activate"
pip install --upgrade pip

# 안전장치: 실수로 ~/venv/il 을 쓰고 있으면 즉시 중단.
# (activate 스크립트가 두 개 섞여 있을 때 실수로 이걸 il 안에서 돌리는 사고 방지)
if [[ "${VIRTUAL_ENV:-}" != "$VENV" ]]; then
  echo "  ! venv 활성화가 이상합니다. VIRTUAL_ENV=$VIRTUAL_ENV (기대: $VENV)"
  exit 1
fi

echo
echo "== 4/5  LeRobot $LEROBOT_VERSION + SmolVLA extra =="
# pip 으로 릴리스 버전을 그대로 설치. v0.4.4 처럼 git 클론+체크아웃 방식은 안 씀.
# 이유: 여기선 로컬 패치를 얹지 않을 것이므로 (필요해지면 그때 방식 바꾼다).
#
# lerobot[smolvla] extra 는 SmolVLA 정책에 필요한 vision·text 모델 의존성을 함께 끌어옴
# (transformers, sentencepiece, safetensors 등). 없으면 policy import 에서 조용히 실패한다.
pip install "lerobot[smolvla,dynamixel]>=${LEROBOT_VERSION}"

# 설치된 버전이 실제로 SmolVLA 를 알고 있는지 확인 — pip 버전 지정과 별개로 실 import 로 검증.
python - <<'PY'
import lerobot
print(f"  lerobot {lerobot.__version__}")
try:
    from lerobot.common.policies.smolvla.modeling_smolvla import SmolVLAPolicy  # 경로는 릴리스별로 바뀔 수 있음
    print("  SmolVLAPolicy import OK")
except Exception as e:
    print(f"  ! SmolVLAPolicy import 실패: {e}")
    print("    LeRobot 배포에서 SmolVLA 모듈 경로가 바뀌었을 수 있습니다.")
    print("    'lerobot-train --policy.type=smolvla --help' 로 실제 이름을 확인하고,")
    print("    06_train_vla.sh 의 --policy.type 값을 맞추세요.")
PY

echo
echo "== 5/5  사전학습 체크포인트 캐싱 ($BASE_MODEL) =="
# 처음 학습·평가 시작할 때 HF 에서 몇 GB 를 받는데, 그게 05_record 뒤 급한 순간에 걸리면
# 짜증남. 지금 미리 받아둔다. 실패해도(오프라인 등) 설치를 막지는 않는다.
python - <<PY || true
from huggingface_hub import snapshot_download
try:
    p = snapshot_download("$BASE_MODEL")
    print(f"  캐시됨: {p}")
except Exception as e:
    print(f"  ! 사전 캐싱 실패 (계속 진행): {e}")
    print("    첫 06_train_vla.sh 실행 시 자동으로 다시 시도합니다.")
PY

echo
echo "=============================================="
echo " SmolVLA 환경 설치 끝."
echo "  venv        : $VENV"
echo "  base model  : $BASE_MODEL"
echo "=============================================="
echo " 다음:"
echo "   1) 데이터셋은 기존 mingky/pill_bottle_v1 을 그대로 씀 (재녹화 불필요)"
echo "   2) 학습:   bash 06_train_vla.sh"
echo "   3) 평가:   bash 07_run_vla.sh"
echo
echo " 참고: 02_find_ports.sh / 03_check_cameras.sh 는 이 venv 로 다시 돌릴 필요 없음."
echo "       로봇 포트·카메라 설정은 기존 ~/venv/il 에서 만든 것을 그대로 공유함."
