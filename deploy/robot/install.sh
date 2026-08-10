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
[ -n "$ROBOT_ID" ] || { echo "사용법: sudo ./install.sh <robot-id>   예: pinky-01" >&2; exit 1; }

HERE="$(cd "$(dirname "$0")" && pwd)"
[ "$(id -u)" -eq 0 ] || { echo "sudo 로 실행하세요." >&2; exit 1; }

# --- 로봇의 정체 -------------------------------------------------------------
# 로봇마다 다른 유일한 값이다. 코드가 아니라 여기 둔다.
install -d -m 755 /etc/mingky
if [ -f /etc/mingky/robot.env ]; then
    echo "  유지: /etc/mingky/robot.env (이미 있음)"
else
    sed "s/^MINGKY_ROBOT_ID=.*/MINGKY_ROBOT_ID=${ROBOT_ID}/" \
        "$HERE/robot.env.example" > /etc/mingky/robot.env
    chmod 644 /etc/mingky/robot.env
    echo "  생성: /etc/mingky/robot.env (MINGKY_ROBOT_ID=${ROBOT_ID})"
fi

install -m 755 "$HERE/bin/foxglove-remote.sh" /usr/local/bin/

# --- 유닛 --------------------------------------------------------------------
for unit in "$HERE"/systemd/*.service; do
    install -m 644 "$unit" /etc/systemd/system/
    echo "  설치: $(basename "$unit")"
done
systemctl daemon-reload

# 터널·게이트웨이·배터리는 항상 떠 있어야 한다.
# fg-* (Foxglove) 는 관측용이라 필요할 때만 켠다.
systemctl enable --now \
    mingky-ssh-tunnel \
    mingky-gateway \
    mingky-battery-pub \
    mingky-teleop-bridge

cat <<'EOF'

설치 끝. 확인:
    systemctl status mingky-ssh-tunnel mingky-gateway mingky-battery-pub

아직 남은 것 — 이 스크립트가 못 하는 일:

  1. 터널 키
     /home/pinky/.ssh/id_ed25519_tunnel 이 있어야 하고, 그 공개키가
     클라우드의 ~ubuntu/.ssh/authorized_keys 에 permitlisten 과 함께
     등록돼 있어야 한다. 키를 저장소에 둘 수 없으므로 수동이다.

  2. Wi-Fi 자동 접속
     nmcli -f NAME,AUTOCONNECT,AUTOCONNECT-PRIORITY con show
     쓰는 망이 yes / 100 이어야 한다. 아니면 재부팅 후 AP 모드로 떨어져
     로봇에 접근할 수 없게 된다. 실제로 겪은 사고다.
EOF
