#!/usr/bin/env bash
# OMX 리더-팔로워 텔레옵 실행
source /home/user/venv/il/bin/activate

lerobot-teleoperate \
  --robot.type=omx_follower \
  --robot.port=/dev/omx_follower \
  --robot.id=omx_follower_arm \
  --teleop.type=omx_leader \
  --teleop.port=/dev/omx_leader \
  --teleop.id=omx_leader_arm
