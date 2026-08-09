# mingky_aruco_detector

ROS `sensor_msgs/Image`에서 ArUco 마커를 검출하고, 카메라 캘리브레이션과
마커 실측 크기를 이용해 optical frame 기준 자세와 거리를 발행한다.

기본 설정은 인형에 붙인 ChArUco 보드 마커에 맞춘다.

- dictionary: `DICT_4X4_50`
- marker length: `0.026 m` (검은 정사각형 외곽 한 변)
- input: `/rear_camera/image_raw`
- output namespace: `/rear_aruco`

## 실행

후방 카메라를 먼저 실행한 뒤, 로봇에 맞는 보정 파일을 지정한다.

```bash
ros2 launch mingky_bringup rear_camera.launch.py

ros2 launch mingky_aruco_detector aruco_detector.launch.py \
  calibration_file:=$(ros2 pkg prefix mingky_bringup)/share/mingky_bringup/config/camera/pinky_15e2/rear_camera.yaml
```

Pinky1에서는 경로의 `pinky_15e2`를 `pinky_6294`로 바꾼다. 특정 마커만
목표로 사용하려면 `target_marker_id:=<ID>`를 추가한다.

## 발행 토픽

- `detections`: 한 프레임에서 검출된 모든 마커의 ID, 자세, 거리
- `target_pose`: 지정한 마커 또는 가장 가까운 마커의 자세
- `target_distance`: 카메라 원점부터 목표 마커 중심까지의 직선거리(m)
- `target_visible`: 목표 마커의 현재 검출 여부

카메라 YAML의 해상도와 입력 영상 해상도가 다르면 잘못된 거리값을 내는
대신 해당 프레임을 거부한다. 전방 카메라도 ROS Image 토픽이 준비되면 같은
노드에서 `image_topic`, `calibration_file`, `namespace`만 바꿔 사용할 수 있다.
