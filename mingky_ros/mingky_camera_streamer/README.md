# mingky_camera_streamer

ROS Image 토픽을 관제용 저대역폭 MJPEG로 변환합니다. 원본 영상 토픽은 Pinky
내부 처리에 그대로 사용하고, 브라우저 접속자가 있을 때만 최신 프레임을 JPEG로
인코딩합니다.

```bash
ros2 run mingky_camera_streamer image_streamer --ros-args \
  -p image_topic:=/rear_camera/image_raw \
  -p port:=8092 -p max_fps:=10.0 -p max_width:=640 -p jpeg_quality:=60
```

스트림은 로봇의 loopback에만 열립니다. `mingky-camera-tunnel.service`가
클라우드로 역터널을 만들고 nginx가 `/camera/<robot-id>/<front|rear>/stream`
경로로 전달합니다.
