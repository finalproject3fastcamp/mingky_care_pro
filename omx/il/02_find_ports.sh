#!/usr/bin/env bash
# [2단계] 로봇 포트 찾아서 이름 고정하기 — 로봇 두 팔을 꽂고 전원을 켠 뒤 실행한다.
#
#   bash 02_find_ports.sh
#
# 왜 필요한가: /dev/ttyACM0, /dev/ttyACM1 번호는 **꽂는 순서와 재부팅마다 바뀐다.**
# 어제 팔로워였던 게 오늘 리더가 된다. 그래서 보드 시리얼로 이름을 고정한다.
#   → /dev/omx_follower , /dev/omx_leader  (이 두 이름은 앞으로 절대 안 바뀐다)
#
# 팔로워/리더 구분은 모터 ID로 한다 (팔로워 11~16, 리더 1~6).
# 한 번만 하면 되고, 다른 로봇으로 바꿔 꽂으면 그때 다시 한 번 실행한다.
set -euo pipefail

VENV=$HOME/venv/il
HERE=$(cd "$(dirname "$0")" && pwd)
RULES=/etc/udev/rules.d/99-omx.rules
# shellcheck disable=SC1090
source "$VENV/bin/activate"

echo "== 꽂혀 있는 팔 찾는 중 =="
python "$HERE/omx_scan.py" || true
echo

VALS=$(python "$HERE/omx_scan.py" --export) || {
  echo "두 팔을 다 못 찾아서 멈춥니다. 위 안내를 보고 고친 뒤 다시 실행하세요."
  exit 1
}
eval "$VALS"

if [ -z "${FOLLOWER_SERIAL:-}" ] || [ -z "${LEADER_SERIAL:-}" ]; then
  echo "시리얼을 못 읽었습니다. udev 고정 없이 쓰려면 아래 포트를 직접 쓰세요:"
  echo "  팔로워 ${FOLLOWER_PORT:-?} / 리더 ${LEADER_PORT:-?}"
  exit 1
fi

echo "=============================================="
echo " 팔로워  ${FOLLOWER_PORT}  serial=${FOLLOWER_SERIAL}"
echo " 리더    ${LEADER_PORT}  serial=${LEADER_SERIAL}"
echo "=============================================="
echo
echo "이 시리얼로 udev 규칙을 씁니다 ($RULES). sudo 비밀번호를 물어봅니다."
read -r -p "진행할까요? [y/N] " ans
[ "$ans" = "y" ] || { echo "취소했습니다."; exit 0; }

sudo tee "$RULES" >/dev/null <<EOF
# OMX 팔 포트 고정 — $(date '+%Y-%m-%d') 생성 (02_find_ports.sh)
SUBSYSTEM=="tty", ATTRS{idVendor}=="${FOLLOWER_VENDOR}", ATTRS{serial}=="${FOLLOWER_SERIAL}", SYMLINK+="omx_follower"
SUBSYSTEM=="tty", ATTRS{idVendor}=="${LEADER_VENDOR}", ATTRS{serial}=="${LEADER_SERIAL}", SYMLINK+="omx_leader"
EOF

sudo udevadm control --reload-rules
sudo udevadm trigger
sleep 2

echo
if [ -e /dev/omx_follower ] && [ -e /dev/omx_leader ]; then
  ls -l /dev/omx_follower /dev/omx_leader
  echo
  echo "성공. 다음: bash 03_check_cameras.sh"
else
  echo "! 링크가 아직 안 보입니다. USB 를 뺐다 다시 꽂아보세요."
  echo "  그래도 안 되면 확인:  cat $RULES"
  exit 1
fi
