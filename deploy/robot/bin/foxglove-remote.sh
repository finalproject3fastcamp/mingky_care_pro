#!/usr/bin/env bash
# 원격 관측 + 조작용 Foxglove Bridge.
#
# clientPublish 가 켜져 있다. **접속 주소를 아는 사람은 로봇을 몰 수 있다.**
# 지금은 URL 의 토큰이 유일한 접근 통제다. 주소가 새면 nginx 설정의 토큰을
# 바꿔 즉시 무효화한다.
#
# 127.0.0.1 에만 바인딩한다. 밖에서는 SSH 역터널을 통해서만 닿는다.
set -e
source /opt/ros/jazzy/setup.bash
source /home/pinky/pinky_pro/install/local_setup.bash
source /home/pinky/mingky_care_pro/install/local_setup.bash

# 2.4GHz 무선과 클라우드 아웃바운드를 함께 쓴다. 카메라 영상은 뺀다.
# 영상을 넣으면 주행 제어가 먼저 느려진다.
WHITELIST="['/map','/scan','/tf','/tf_static','/odom','/plan','/local_plan',"
WHITELIST+="'/amcl_pose','/particlecloud','/robot_description','/diagnostics',"
WHITELIST+="'/cmd_vel.*','/battery/.*','/global_costmap/costmap','/local_costmap/costmap','/events']"

exec ros2 launch mingky_bringup foxglove.launch.py \
  address:=127.0.0.1 \
  capabilities:="['clientPublish','parameters','parametersSubscribe','services','connectionGraph','assets']" \
  topic_whitelist:="${WHITELIST}"
