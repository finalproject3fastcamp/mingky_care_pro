#!/usr/bin/env bash
#
# Discovery Server 를 쓰도록 현재 셸을 설정한다.
#
#     source <repo>/mingky_ros/mingky_bringup/scripts/use_discovery_server.sh
#
# 반드시 source 로 실행해야 한다. 환경 변수를 부르는 셸에 남겨야 하므로
# ros2 run 이나 ./use_discovery_server.sh 로는 효과가 없다.
#
# 터미널마다 해야 한다. 매번 치기 싫으면 ~/.bashrc 에 넣는다.
#
#     echo 'source ~/mingky_care_pro/mingky_ros/mingky_bringup/scripts/use_discovery_server.sh' >> ~/.bashrc
#
# 서버는 로봇에서 띄운다.
#
#     ros2 run mingky_bringup run_discovery_server.sh

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "[실패] 이 스크립트는 source 로 실행해야 합니다." >&2
  echo "  source ${BASH_SOURCE[0]}" >&2
  exit 1
fi

__mingky_setup_discovery() {
  local server_ip="${MINGKY_DISCOVERY_IP:-192.168.0.21}"
  local server_port="${MINGKY_DISCOVERY_PORT:-11811}"
  local domain_id="${PINKY_DOMAIN_ID:-21}"

  export ROS_DOMAIN_ID="${domain_id}"
  export ROS_DISCOVERY_SERVER="${server_ip}:${server_port}"
  # 없으면 ros2 topic list 나 node list 가 전체 그래프를 못 본다.
  export ROS_SUPER_CLIENT=true

  # 데몬은 예전 설정을 물고 있다. 새 설정으로 다시 뜨게 한다.
  ros2 daemon stop >/dev/null 2>&1 || true

  echo "[설정] ROS_DOMAIN_ID=${ROS_DOMAIN_ID}"
  echo "[설정] ROS_DISCOVERY_SERVER=${ROS_DISCOVERY_SERVER}"
  echo "[설정] ROS_SUPER_CLIENT=${ROS_SUPER_CLIENT}"

  if command -v nc >/dev/null 2>&1; then
    if nc -z -u -w 2 "${server_ip}" "${server_port}" >/dev/null 2>&1; then
      echo "[확인] ${server_ip}:${server_port} 응답"
    else
      echo "[주의] ${server_ip}:${server_port} 를 확인하지 못했습니다."
      echo "       UDP 라 이 검사는 확실하지 않습니다. 로봇에서 서버가 떠 있는지 보세요."
      echo "         ros2 run mingky_bringup run_discovery_server.sh"
    fi
  fi

  cat <<'EOF'

확인하려면:
  ros2 node list        로봇 노드가 보여야 합니다
  ros2 topic hz /odom   약 30 Hz

되돌리려면:
  unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT && ros2 daemon stop

EOF
}

__mingky_setup_discovery
unset -f __mingky_setup_discovery
