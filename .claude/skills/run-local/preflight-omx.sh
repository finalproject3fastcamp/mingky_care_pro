#!/usr/bin/env bash
#
# 실제 모드(PHARMACY_REAL=1) 를 켤 수 있는 상태인지 점검한다. 아무것도 움직이지
# 않는다 — 서보는 broadcast ping 만 하고 카메라는 프레임만 읽는다.
#
#   ./.claude/skills/run-local/preflight-omx.sh
#   ./.claude/skills/run-local/preflight-omx.sh --no-ping   # 서보 ping 생략 (빠름)
#
# 기준은 `~/omx_hardware_inventory.md` 의 실측 BOM 이다.
# 하나라도 빠지면 종료 코드 1.

set -uo pipefail

OMX_PYTHON="${OMX_PYTHON:-$HOME/venv/il/bin/python}"
OMX_PILL_ROOT="${OMX_PILL_ROOT:-$HOME/omx_pill_project}"
DO_PING=1
[ "${1:-}" = "--no-ping" ] && DO_PING=0

# 인벤토리상의 USB ID. 값이 바뀌면 부품이 바뀐 것이므로 여기서 잡아야 한다.
TOP_CAM_ID="4c4a:4a55"
WRIST_CAM_ID="0c45:6367"
OPENRB_ID="2f5d:2202"

FAIL=0
WARN=0

ok()   { printf '  \033[32m✅\033[0m %-26s %s\n' "$1" "${2:-}"; }
bad()  { printf '  \033[31m❌\033[0m %-26s %s\n' "$1" "${2:-}"; FAIL=$((FAIL+1)); }
warn() { printf '  \033[33m⚠️\033[0m  %-26s %s\n' "$1" "${2:-}"; WARN=$((WARN+1)); }
hdr()  { printf '\n\033[1m%s\033[0m\n' "$1"; }

# vid:pid 로 가장 낮은 번호의 /dev/video* 노드를 찾는다. 이 카메라들은 캡처
# 노드와 메타데이터 노드를 쌍으로 만들고 캡처가 항상 앞 번호다.
find_video_node() {
  local vid="${1%%:*}" pid="${1##*:}" v
  for v in $(ls -1 /dev/video* 2>/dev/null | sort -V); do
    local props; props="$(udevadm info -q property -n "$v" 2>/dev/null)"
    if grep -q "^ID_VENDOR_ID=${vid}$" <<<"$props" \
       && grep -q "^ID_MODEL_ID=${pid}$" <<<"$props"; then
      echo "$v"; return 0
    fi
  done
  return 1
}

hdr "로봇 팔"

if [ -e /dev/ttyACM0 ]; then
  model="$(udevadm info -q property -n /dev/ttyACM0 2>/dev/null | sed -n 's/^ID_MODEL=//p')"
  ok "팔로워 컨트롤러" "/dev/ttyACM0 (${model:-?})"
else
  bad "팔로워 컨트롤러" "/dev/ttyACM0 없음 — USB·전원 확인"
fi

if id -nG | tr ' ' '\n' | grep -qx dialout; then
  ok "dialout 그룹" "시리얼 접근 가능"
else
  bad "dialout 그룹" "usermod -aG dialout \$USER 후 재로그인 필요"
fi

# 리더 암은 원격조작 데이터 수집용이다. 자율 조제에는 필요없으므로 경고만.
if lsusb 2>/dev/null | grep -q "$OPENRB_ID.*"; then
  n="$(lsusb | grep -c "$OPENRB_ID")"
  [ "$n" -ge 2 ] && ok "리더 컨트롤러" "/dev/ttyACM1" \
                 || warn "리더 컨트롤러" "미연결 — 자율 조제에는 불필요 (원격조작 수집용)"
fi

if [ "$DO_PING" = 1 ] && [ -e /dev/ttyACM0 ] && [ -x "$OMX_PYTHON" ]; then
  ids="$("$OMX_PYTHON" - <<'PY' 2>/dev/null
from dynamixel_sdk import PortHandler, PacketHandler
p = PortHandler("/dev/ttyACM0")
if p.openPort() and p.setBaudRate(1000000):
    found, comm = PacketHandler(2.0).broadcastPing(p)
    if comm == 0 and found:
        print(",".join(str(i) for i in sorted(found)))
    p.closePort()
PY
)"
  if [ -n "$ids" ]; then
    n="$(tr ',' '\n' <<<"$ids" | wc -l)"
    [ "$n" -eq 6 ] && ok "팔로워 서보" "ID $ids" \
                   || warn "팔로워 서보" "$n 개만 응답 (기대 6) — ID $ids"
  else
    bad "팔로워 서보" "1Mbps 에서 응답 없음 — 전원 어댑터 확인"
  fi
fi

hdr "카메라"

# 존재만 보지 않고 프레임까지 받아 본다. 꽂은 직후에는 노드가 생겼는데도 몇 초간
# 읽기가 실패하고, 자동노출이 잡히기 전에는 검은 화면이 나온다 — 둘 다 조제
# 중에 터지면 원인을 찾기 어려운 실패다.
check_camera() {
  # bash 의 local 은 비ASCII 식별자를 받지 않는다 — 변수명은 영문으로 둔다.
  local label="$1" usb_id="$2" why="$3" node
  if ! node="$(find_video_node "$usb_id")"; then
    bad "$label" "$usb_id 미연결 — $why"
    return
  fi
  ok "$label" "$node ($usb_id)"
  [ -x "$OMX_PYTHON" ] || return

  local mean
  mean="$("$OMX_PYTHON" - "$node" <<'PY' 2>/dev/null
import sys, cv2
cap = cv2.VideoCapture(sys.argv[1], cv2.CAP_V4L2)
got = None
if cap.isOpened():
    for _ in range(30):        # 자동노출이 잡힐 때까지 흘려보낸다
        rc, f = cap.read()
        if rc:
            got = f
    cap.release()
if got is not None:
    print(f"{got.mean():.1f}")
PY
)"
  if [ -z "$mean" ]; then
    bad "$label 프레임" "읽기 실패 — 다른 프로세스가 잡고 있거나 아직 안정화 전"
  elif awk "BEGIN{exit !($mean < 5)}"; then
    bad "$label 프레임" "검은 화면 (평균 $mean) — USB 를 다시 꽂아 주세요"
  else
    ok "$label 프레임" "평균 밝기 $mean"
  fi
}

check_camera "top 카메라"   "$TOP_CAM_ID"   "트레이 계수·정책 추론 불가"
check_camera "wrist 카메라" "$WRIST_CAM_ID" "정책이 두 시점을 함께 받는다"

hdr "조제 소프트웨어"

if [ -x "$OMX_PYTHON" ]; then
  ver="$("$OMX_PYTHON" -c 'import lerobot; print(lerobot.__version__)' 2>/dev/null)"
  [ -n "$ver" ] && ok "il venv" "lerobot $ver — $OMX_PYTHON" \
                || bad "il venv" "lerobot import 실패 — $OMX_PYTHON"
else
  bad "il venv" "$OMX_PYTHON 없음 (OMX_PYTHON 으로 지정 가능)"
fi

if [ -d "$OMX_PILL_ROOT" ]; then
  ok "조제 파트" "$OMX_PILL_ROOT"
  for f in run.sh pharmacy.py; do
    [ -f "$OMX_PILL_ROOT/$f" ] && ok "  $f" "" \
                               || bad "  $f" "$OMX_PILL_ROOT 안에 없음"
  done
else
  bad "조제 파트" "$OMX_PILL_ROOT 없음 — run.sh·pharmacy.py 가 있는 작업 디렉터리"
fi

hdr "결과"
if [ "$FAIL" -eq 0 ]; then
  printf '  실제 모드를 켤 수 있습니다%s\n\n' \
    "$([ "$WARN" -gt 0 ] && echo " (경고 ${WARN}건)")"
  printf '    PHARMACY_REAL=1 ./.claude/skills/run-local/start.sh backend\n\n'
  exit 0
fi
printf '  \033[31m%d 항목이 빠져 실제 모드를 켤 수 없습니다.\033[0m\n' "$FAIL"
printf '  시뮬레이션 모드는 그대로 동작합니다 (기본값).\n\n'
exit 1
