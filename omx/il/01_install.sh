#!/usr/bin/env bash
# [1단계] 환경 설치 — 딱 한 번만 실행하면 된다.
#
#   bash 01_install.sh
#
# 하는 일: 시스템 패키지 → 시리얼 포트 권한 → 파이썬 가상환경 → LeRobot v0.4.4 → 로컬 패치
# 로봇·카메라는 꽂혀 있어도 되고 아니어도 된다 (여기선 안 건드린다).
set -euo pipefail

VENV=$HOME/venv/il
LEROBOT=$HOME/il_ws/src/lerobot
COMMIT=8fff0fde7c79f23a93d845d1a50e985de01f8b8a   # = v0.4.4 태그
HERE=$(cd "$(dirname "$0")" && pwd)

echo "== 1/5  시스템 패키지 =="
sudo apt update
sudo apt install -y git python3-venv python3-dev ffmpeg v4l-utils

echo
echo "== 2/5  시리얼 포트 권한 (dialout 그룹) =="
if groups | grep -qw dialout; then
  echo "  이미 dialout 그룹입니다."
else
  sudo usermod -aG dialout "$USER"
  echo "  ★ 추가했습니다. 이 설치가 끝나면 반드시 로그아웃 → 로그인 하세요."
  echo "    (안 하면 로봇 포트를 못 엽니다)"
fi

echo
echo "== 3/5  가상환경 $VENV =="
if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
fi
# shellcheck disable=SC1090
source "$VENV/bin/activate"
pip install --upgrade pip

echo
echo "== 4/5  LeRobot v0.4.4 =="
# 버전을 고정한다. 최신(0.6.x)은 CLI 가 달라서 이 킷의 명령어가 안 맞는다.
if [ ! -d "$LEROBOT/.git" ]; then
  mkdir -p "$(dirname "$LEROBOT")"
  git clone https://github.com/huggingface/lerobot.git "$LEROBOT"
fi
cd "$LEROBOT"
git checkout "$COMMIT"
pip install -e ".[dynamixel]"

echo
echo "== 5/5  로컬 패치 =="
# 무엇을 고치는지는 패치 파일 맨 위 주석과 README 를 본다.
#   · USB 카메라 재연결 · 저장/인코딩 순서 · 첫 에피소드 대기
#   · DAgger 개입 · Wayland 키 입력 · ACT FiLM 목표 조건화
PATCH="$HERE/lerobot_local_v0.4.4.patch"
if git apply --check "$PATCH" 2>/dev/null; then
  git apply "$PATCH"
  echo "  적용 완료."
elif git apply --reverse --check "$PATCH" 2>/dev/null; then
  echo "  이미 적용되어 있습니다."
else
  # 예전에는 여기서 조용히 건너뛰었다. 그러면 패치 없는 lerobot 으로 녹화가 시작되고,
  # 0프레임 에피소드나 카메라 끊김으로 데이터를 잃은 뒤에야 알아차리게 된다.
  # 진행을 막고, 무엇이 걸리는지 보여준다.
  echo
  echo "  ! 패치를 적용할 수 없습니다. 로컬 lerobot 에 이미 다른 수정이 있을 수 있습니다."
  echo
  # set -e 아래라 실패하는 명령을 그냥 두면 안내를 다 못 찍고 여기서 끊긴다.
  echo "  --- git apply 오류 ---"
  git apply --check "$PATCH" 2>&1 | sed 's/^/  /' || true
  echo "  --- 현재 로컬 수정 ---"
  git -C "$LEROBOT" diff --stat | sed 's/^/  /' || true
  echo
  echo "  어떻게 할지:"
  echo "   1) 로컬 수정을 버려도 되면:  git -C $LEROBOT checkout -- src/  후 다시 실행"
  echo "   2) 로컬 수정을 살려야 하면:  파일별로 손으로 병합한 뒤"
  echo "      git -C $LEROBOT diff > $PATCH  로 이 파일을 다시 뽑는다"
  echo
  echo "  패치 없이 진행하면 데이터를 잃을 수 있으므로 여기서 멈춥니다."
  exit 1
fi

echo
echo "=============================================="
echo " 설치 끝."
python -c "import lerobot; print(' lerobot', lerobot.__version__)"
echo "=============================================="
echo " 다음: (dialout 을 방금 추가했다면 로그아웃 → 로그인 먼저)"
echo "   bash 02_find_ports.sh"
