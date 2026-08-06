#!/usr/bin/env bash
#
# Fast DDS Discovery Server 를 띄운다. 로봇에서 실행한다.
#
# 왜 필요한가
#     기본 디스커버리는 멀티캐스트 그룹 239.255.0.1 을 쓴다. 이 그룹이
#     공유기를 넘지 못하는 환경에서는 ping 도 되고 대역폭도 남는데 ROS 만
#     서로를 못 본다. tcpdump 로 확인한 실제 증상이다.
#
#         PC   → 239.255.0.1:12650  송신됨, 로봇에 도착 안 함
#         로봇 → 239.255.0.1:12650  송신됨, PC 에 도착 안 함
#         ros2 multicast (225.0.0.1:49150) 는 통과
#
#     Discovery Server 는 멀티캐스트를 쓰지 않는다. 모든 장비가 서버 주소
#     하나만 알면 되므로, 노트북이 몇 대든 설정이 같다.
#
# 쓰는 법
#     로봇      ros2 run mingky_bringup run_discovery_server.sh
#     그 외 장비 source <repo>/mingky_ros/mingky_bringup/scripts/use_discovery_server.sh

set -eo pipefail

SERVER_IP="${MINGKY_DISCOVERY_IP:-192.168.0.21}"
SERVER_PORT="${MINGKY_DISCOVERY_PORT:-11811}"
SERVER_ID="${MINGKY_DISCOVERY_ID:-0}"

fail() {
  echo "[실패] $1" >&2
  exit 1
}

usage() {
  cat <<EOF
사용법:
  ros2 run mingky_bringup run_discovery_server.sh

환경 변수:
  MINGKY_DISCOVERY_IP    서버가 들을 주소. 기본 ${SERVER_IP}
  MINGKY_DISCOVERY_PORT  포트. 기본 ${SERVER_PORT}
  MINGKY_DISCOVERY_ID    서버 식별자. 기본 ${SERVER_ID}

pinky2 에서 띄우려면:
  MINGKY_DISCOVERY_IP=192.168.0.22 ros2 run mingky_bringup run_discovery_server.sh
EOF
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
esac

command -v fastdds >/dev/null 2>&1 \
  || fail "fastdds 명령을 찾을 수 없습니다. ROS 2 환경을 source 했는지 확인하세요."

# 지정한 주소가 이 장비의 것인지 본다. 남의 주소로는 들을 수 없다.
if ! ip -4 -br addr 2>/dev/null | grep -qF " ${SERVER_IP}/"; then
  echo "[주의] ${SERVER_IP} 는 이 장비의 주소가 아닌 것 같습니다." >&2
  echo "       이 장비의 주소:" >&2
  ip -4 -br addr 2>/dev/null | awk '$1 != "lo" {printf "         %s  %s\n", $1, $3}' >&2
  echo "       MINGKY_DISCOVERY_IP 로 바꿀 수 있습니다." >&2
  exit 1
fi

if ss -ltunp 2>/dev/null | grep -q ":${SERVER_PORT} "; then
  fail "포트 ${SERVER_PORT} 가 이미 사용 중입니다. 서버가 이미 떠 있는지 확인하세요."
fi

cat <<EOF

============================================================
Fast DDS Discovery Server

  주소   ${SERVER_IP}:${SERVER_PORT}
  ID     ${SERVER_ID}

다른 장비(노트북 포함)에서는 아래 한 줄만 하면 됩니다.

  export ROS_DISCOVERY_SERVER=${SERVER_IP}:${SERVER_PORT}
  export ROS_SUPER_CLIENT=true
  ros2 daemon stop

또는 저장소의 스크립트를 source 하세요.

  source <repo>/mingky_ros/mingky_bringup/scripts/use_discovery_server.sh

이 서버가 죽으면 새 노드가 서로를 찾지 못합니다.
이미 연결된 노드는 계속 동작합니다.

종료하려면 Ctrl+C.
============================================================

EOF

# 이 서버 자신도 클라이언트들과 같은 설정을 봐야 한다.
export ROS_DISCOVERY_SERVER="${SERVER_IP}:${SERVER_PORT}"

exec fastdds discovery \
  --server-id "${SERVER_ID}" \
  --udp-address "${SERVER_IP}" \
  --udp-port "${SERVER_PORT}"
