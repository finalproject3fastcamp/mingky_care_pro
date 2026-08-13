#!/usr/bin/env bash
#
# 로봇 한 대를 관제에 연결하는 데 필요한 systemd 유닛을 설치한다.
#
# 로봇에서 직접 실행한다:
#     cd ~/mingky_care_pro/deploy/robot && sudo ./install.sh pinky-01
#
# 여기 있는 유닛들은 원래 로봇에만 손으로 만들어져 있었다. 그러면 로봇을
# 다시 깔거나 세 번째 로봇을 붙일 때 무엇이 있어야 하는지 아무도 모른다.
set -euo pipefail

ROBOT_ID="${1:-}"
FIRE_INFER_URL="${2:-}"
[ -n "$ROBOT_ID" ] || {
    echo "사용법: sudo ./install.sh <robot-id> [fire-infer-url]" >&2
    echo "예: sudo ./install.sh pinky-01 http://192.168.0.30:5000/infer" >&2
    exit 1
}

# 역터널 포트는 로봇마다 달라야 한다. 번호에서 유도해 사람이 고를 여지를 없앤다.
#
# 서버의 authorized_keys 가 키별로 permitlisten 을 걸어 두므로 값이 틀리면
# 서버가 바인딩을 거부한다. 유닛에 ExitOnForwardFailure=yes 가 있어 터널이
# 뜨지 않고 재시작만 반복하며, 그 로봇은 접근 불가가 된다. 두 로봇이 같은
# 포트를 쓰는 사고가 실제로 이 파일에서 나왔다.
N="${ROBOT_ID##*-}"
case "$N" in
    0[1-9]|[1-9]) ;;
    *) echo "robot-id 는 pinky-01 형식이어야 한다: $ROBOT_ID" >&2; exit 1 ;;
esac
N=$((10#$N))
SSH_PORT=$((22020 + N))
FG_PORT=$((18764 + N))
CAMERA_FRONT_PORT=$((18799 + N * 2))
CAMERA_REAR_PORT=$((18800 + N * 2))

HERE="$(cd "$(dirname "$0")" && pwd)"
[ "$(id -u)" -eq 0 ] || { echo "sudo 로 실행하세요." >&2; exit 1; }

# --- 로봇의 정체 -------------------------------------------------------------
# 로봇마다 다른 유일한 값이다. 코드가 아니라 여기 둔다.
install -d -m 755 /etc/mingky
if [ -f /etc/mingky/robot.env ]; then
    echo "  유지: /etc/mingky/robot.env (이미 있음)"
else
    sed -e "s/^MINGKY_ROBOT_ID=.*/MINGKY_ROBOT_ID=${ROBOT_ID}/" \
        -e "s/^MINGKY_SSH_TUNNEL_PORT=.*/MINGKY_SSH_TUNNEL_PORT=${SSH_PORT}/" \
        -e "s/^MINGKY_FOXGLOVE_TUNNEL_PORT=.*/MINGKY_FOXGLOVE_TUNNEL_PORT=${FG_PORT}/" \
        -e "s/^MINGKY_CAMERA_FRONT_TUNNEL_PORT=.*/MINGKY_CAMERA_FRONT_TUNNEL_PORT=${CAMERA_FRONT_PORT}/" \
        -e "s/^MINGKY_CAMERA_REAR_TUNNEL_PORT=.*/MINGKY_CAMERA_REAR_TUNNEL_PORT=${CAMERA_REAR_PORT}/" \
        "$HERE/robot.env.example" > /etc/mingky/robot.env
    chmod 644 /etc/mingky/robot.env
    echo "  생성: /etc/mingky/robot.env"
    echo "         MINGKY_ROBOT_ID=${ROBOT_ID}  SSH=${SSH_PORT}  Foxglove=${FG_PORT}"
fi

# 기존 설치의 robot.env도 새 카메라 포트만 보강한다. 이미 있는 기기별 설정은
# 덮어쓰지 않는다.
grep -q '^MINGKY_CAMERA_FRONT_TUNNEL_PORT=' /etc/mingky/robot.env \
    || echo "MINGKY_CAMERA_FRONT_TUNNEL_PORT=${CAMERA_FRONT_PORT}" >> /etc/mingky/robot.env
grep -q '^MINGKY_CAMERA_REAR_TUNNEL_PORT=' /etc/mingky/robot.env \
    || echo "MINGKY_CAMERA_REAR_TUNNEL_PORT=${CAMERA_REAR_PORT}" >> /etc/mingky/robot.env
grep -q '^MINGKY_FIRE_EVAC_ENABLED=' /etc/mingky/robot.env \
    || echo 'MINGKY_FIRE_EVAC_ENABLED=false' >> /etc/mingky/robot.env
grep -q '^MINGKY_FIRE_INFER_SERVER_URL=' /etc/mingky/robot.env \
    || echo 'MINGKY_FIRE_INFER_SERVER_URL=' >> /etc/mingky/robot.env

if [ -n "${FIRE_INFER_URL}" ]; then
    sed -i \
        -e 's/^MINGKY_FIRE_EVAC_ENABLED=.*/MINGKY_FIRE_EVAC_ENABLED=true/' \
        -e "s|^MINGKY_FIRE_INFER_SERVER_URL=.*|MINGKY_FIRE_INFER_SERVER_URL=${FIRE_INFER_URL}|" \
        /etc/mingky/robot.env
    echo "  화재 감지 활성화: ${FIRE_INFER_URL}"
else
    echo "  화재 감지 설정 유지: /etc/mingky/robot.env"
fi

install -m 755 "$HERE/bin/foxglove-remote.sh" /usr/local/bin/
install -m 440 "$HERE/mingky-system-control.sudoers" \
    /etc/sudoers.d/mingky-system-control
visudo -cf /etc/sudoers.d/mingky-system-control >/dev/null

# --- 유닛 --------------------------------------------------------------------
for unit in "$HERE"/systemd/*.service; do
    install -m 644 "$unit" /etc/systemd/system/
    echo "  설치: $(basename "$unit")"
done
systemctl daemon-reload

# 접속·관제·배터리와 원격 조작의 수신·모드·속도 상한은 항상 떠 있어야 한다.
# fg-bridge와 fg-tunnel만 Foxglove 관측용이라 필요할 때 켠다.
systemctl enable --now \
    mingky-ssh-tunnel \
    mingky-gateway \
    mingky-battery-pub \
    mingky-teleop-bridge \
    mingky-camera-tunnel \
    fg-teleop \
    mingky-system

cat <<EOF

설치 끝. 확인:
    systemctl status mingky-ssh-tunnel mingky-gateway mingky-battery-pub \
        mingky-teleop-bridge mingky-camera-tunnel fg-teleop mingky-system

아직 남은 것 — 이 스크립트가 못 하는 일:

  1. 터널 키
     /home/pinky/.ssh/id_fgtunnel 이 있어야 하고, 그 공개키가 클라우드의
     ~fgtunnel/.ssh/authorized_keys 에 아래 형태로 등록돼 있어야 한다.
     키를 저장소에 둘 수 없으므로 수동이다.

     로봇마다 키를 따로 쓰고 permitlisten 으로 자기 포트만 열게 한다.
     같은 키를 돌려쓰면 한 로봇이 남의 포트를 잡아, 죽은 로봇 자리에
     다른 로봇이 들어앉는 오배선이 생긴다.

     이 로봇이 필요한 줄:
     restrict,port-forwarding,permitlisten="127.0.0.1:${FG_PORT}",permitlisten="127.0.0.1:${SSH_PORT}",permitlisten="127.0.0.1:${CAMERA_FRONT_PORT}",permitlisten="127.0.0.1:${CAMERA_REAR_PORT}" <공개키> fgtunnel-${ROBOT_ID}

  2. Wi-Fi 자동 접속
     nmcli -f NAME,AUTOCONNECT,AUTOCONNECT-PRIORITY con show
     쓰는 망이 yes / 100 이어야 한다. 아니면 재부팅 후 AP 모드로 떨어져
     로봇에 접근할 수 없게 된다. 실제로 겪은 사고다.
EOF
