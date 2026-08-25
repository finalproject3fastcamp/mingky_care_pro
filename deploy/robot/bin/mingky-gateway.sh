#!/usr/bin/env bash
# 로봇 이벤트를 관제 서버로 올리고 생존 신호를 보낸다.
#
# 설정은 /etc/mingky/robot.env 에 있다. 로봇마다 다른 값(robot_id)을 코드나
# 유닛 파일이 아니라 한 곳에 모아 두려는 것이다.
#
# 이 서비스는 로봇의 주행과 무관하게 항상 떠 있어야 한다. 이벤트는 SQLite
# 큐에 쌓이므로 서버가 죽어 있어도 로봇은 멈추지 않고, 복구되면 밀린 것을
# 보낸다.
set -e
source /opt/ros/jazzy/setup.bash
source /home/pinky/pinky_pro/install/local_setup.bash
source /home/pinky/mingky_care_pro/install/local_setup.bash

exec ros2 run mingky_event_gateway event_gateway --ros-args \
  -p backend_url:="${MINGKY_BACKEND_URL}" \
  -p robot_id:="${MINGKY_ROBOT_ID}" \
  -p low_obstacle_mode:="disabled" \
  -p order_interval_sec:="${MINGKY_ORDER_INTERVAL}" \
  -p order_wait_sec:="${MINGKY_ORDER_WAIT:-25.0}"
