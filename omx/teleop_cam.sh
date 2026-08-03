#!/usr/bin/env bash
# OMX 텔레옵 + 카메라 2대 + rerun 시각화
#   top   : Jieli USB Composite "HCAM0"  — 위에서 작업공간을 내려보는 카메라
#   wrist : Innomaker U20CAM-720P        — 로봇에 달린 카메라
# 2026-07-30: 화면을 실제로 확인해서 두 이름을 바로잡았다 (이전에는 반대로 붙어 있었음).
# /dev/videoN 번호는 재부팅마다 바뀌므로 by-id 고정 경로를 쓴다.
source /home/user/venv/il/bin/activate

TOP=/dev/v4l/by-id/usb-Jieli_Technology_USB_Composite_Device-video-index0
WRIST=/dev/v4l/by-id/usb-Innomaker_Innomaker-U20CAM-720P_SN0001-video-index0

lerobot-teleoperate \
  --robot.type=omx_follower \
  --robot.port=/dev/omx_follower \
  --robot.id=omx_follower_arm \
  --robot.cameras="{ top: {type: opencv, index_or_path: $TOP, width: 640, height: 480, fps: 30, fourcc: MJPG, backend: V4L2}, wrist: {type: opencv, index_or_path: $WRIST, width: 640, height: 480, fps: 30, fourcc: MJPG, backend: V4L2} }" \
  --teleop.type=omx_leader \
  --teleop.port=/dev/omx_leader \
  --teleop.id=omx_leader_arm \
  --display_data=true
