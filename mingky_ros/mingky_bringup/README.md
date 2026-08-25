# mingky_bringup

Mingky Care 프로젝트의 통합 실행 설정과 병원 waypoint를 관리하는 ROS 2 패키지입니다.

## 운영 통합 실행

실제 로봇 베이스, Nav2, 속도 명령 중재기, 배터리 감시, 비상정지 안전 게이트,
Guide Manager, Navigation Manager와 LCD 상태 표시를 한 번에 실행합니다. 배터리 publisher, 이벤트 gateway와 원격
조작 bridge·모드 관리자·속도 제한기는 로봇 설치 시 등록한 systemd 상시
서비스를 사용합니다.

```bash
ros2 launch mingky_bringup mingky_system.launch.xml \
  robot_id:=pinky-01 backend_url:=https://mingkycarepro.site/api
```

통합 launch는 전방 QR 카메라, 후방 USB 카메라와 후방 QR 거리 측정을 함께
실행합니다. 원본 영상은 로봇 내부의 QR 거리 측정에 사용하고, 관제 화면을 연
동안에만 최대 640px·10 FPS·JPEG 품질 60으로 인코딩합니다. `robot_id`에 따라
`pinky-01`은 `pinky_6294`, `pinky-02`는 `pinky_15e2`의 후방 카메라 보정값을
자동으로 사용합니다.

실제 카메라 캡처는 마지막 사용 후 기본 15초 뒤 절전됩니다. 전방 카메라는
QR 스캔, 관제 미리보기 또는 `/front_camera/image_raw/compressed` 구독자가
있으면 즉시 켜집니다. 따라서 화재 감지 노드가 영상을 구독하는 동안에는
절전되지 않아 감지를 계속합니다. 후방 카메라는 안내 중이거나 관제 미리보기
접속자가 있을 때 켜지고, 노드와 MJPEG 주소는 절전 중에도 유지됩니다. 유예는
`camera_idle_timeout` 인자로 조정할 수 있습니다.

```text
전방 MJPEG  http://127.0.0.1:8091/stream
후방 MJPEG  http://127.0.0.1:8092/stream
```

카메라가 없는 개발 환경에서는 각각 끌 수 있습니다.

```bash
ros2 launch mingky_bringup mingky_system.launch.xml \
  start_qr_reader:=false start_rear_camera_stream:=false
```

ArUco 검출기는 후방 QR 거리 측정과 같은 영상을 중복 처리하므로 통합 launch에
포함하지 않습니다. 별도 시험이 필요할 때만 `mingky_aruco_detector` 패키지를
직접 실행합니다. 등록되지 않은 새 로봇은 `camera_profile`에 보정 폴더 이름을
명시합니다.

```bash
ros2 launch mingky_bringup mingky_system.launch.xml \
  robot_id:=pinky-03 camera_profile:=pinky_abcd
```

대역폭을 더 낮추려면 다음 인자를 사용합니다.

```bash
ros2 launch mingky_bringup mingky_system.launch.xml \
  camera_preview_max_width:=320 camera_preview_max_fps:=2.0 \
  camera_preview_jpeg_quality:=55
```

실로봇 기본값은 CSI QR 카메라와 LiDAR 적응형 복구를 함께 실행합니다. QR
Reader는 인식 후 백엔드에서 받은 세션을 Guide Manager로 전달하며, 일반
주행이 실패하면 적응형 복구가 안전한 임시 탈출 지점을 찾아 원래 목표를 다시
시도합니다. 전역 경로 계획기는 검증된 `navfn`을 그대로 사용합니다.

Guide Manager는 환자 세션과 검사실 순서를 관리합니다. 엔지니어 화면의 저장
Waypoint·임시 좌표 시험 주행은 별도 Navigation Manager가 담당합니다. 시험
주행은 한 번에 하나만 허용하며 환자 안내, 저전압 또는 비상정지 상태에서는
시작하지 않습니다. 시험 중 환자 세션이 확인되면 시험 목표를 취소합니다.

LCD는 환자 확인, `출발 위치 → X-ray` 또는 `X-ray → CT` 안내, 검사실 도착,
대기 장소 이동·도착과 안내 완료를 표시합니다. 비상정지와 배터리 부족은 안내
문구보다 우선합니다. LCD를 쓰지 않는 개발 PC에서는
`start_lcd_status:=false`를 전달합니다. 같은 SPI 장치를 사용하는
`pinky_emotion emotion_server`와 동시에 실행하면 안 됩니다.
자동 모드에서 활성 세션이 없는 대기·충전 상태는 기본 0%로 백라이트를 끄고,
안내·수동 조작·경고·화재 대피 중에는 즉시 100%로 복원됩니다.
화재가 확정되면 `/fire_evac/alarm_active`가 해제될 때까지 빨간 LED를 유지하고
위험 부저를 기본 5초 간격으로 반복합니다. 대피 이동이 끝나도 경보는 유지되며,
현장 확인 후 `fire_evac/reset_alarm`을 호출해야 LED와 부저가 꺼집니다.

로봇 이미지의 기존 `battery` 명령도 LCD를 직접 초기화하므로 LCD 상태 노드와
동시에 실행하면 안 됩니다. 빌드 후 아래 설치기를 한 번 실행하면 `battery`를
ROS 토픽만 읽는 안전한 명령으로 교체합니다. 기존 LCD 표시 명령은
`battery-lcd`로 보존하지만 LCD 상태 노드 실행 중에는 사용하지 마세요.

```bash
ros2 run mingky_bringup install_battery_command.sh
source ~/.bashrc
battery
```

`robot_id`의 숫자 접미사로 충전소를 선택합니다. 예를 들어 `pinky-02`는
`charging_station_2`를 사용합니다. 명시적으로 바꾸려면
`charging_waypoint:=charging_station_1`을 전달합니다.

로봇 베이스가 다른 장치에서 이미 실행 중이면 `start_robot_base:=false`를
사용합니다. `start_event_gateway` 기본값은 `false`라 운영 로봇의
`mingky-gateway.service`와 중복되지 않습니다. systemd가 없는 개발 장치에서만
`start_event_gateway:=true`로 실행합니다. 다른 맵을 쓸 때는 같은 맵에 대응하는
`map`, `map_name`, waypoint 파일을 함께 바꿔야 합니다.

### 저상 장애물 대응

목표를 취소한 뒤 직접 옆걸음을 수행하던 `sidestep` 실험 모드는 폐기했습니다.
이 모드는 Nav2가 이미 만든 우회 경로까지 취소해 Waypoint 시험과 안내 주행을
오류 코드 `-10`으로 끝낼 수 있었기 때문입니다. 이전 배포 파일과의 호환성을 위해
`low_obstacle_mode` 이름은 남아 있지만 운영 launch, 관제 명령, ROS 파라미터
변경 모두 `disabled`만 허용합니다. `/etc/mingky/robot.env`에 과거의
`MINGKY_LOW_OBSTACLE_MODE=sidestep`이 남아 있어도 다시 활성화되지 않습니다.

대신 `low_obstacle_fusion_enabled:=true`가 기본 적용됩니다. 전방 초음파를
median 3개와 최근 3개 중 2개로 확인하고, 같은 부채꼴의 LiDAR보다 물체가 충분히
가까울 때만 `/low_obstacle/range`에 발행합니다. 확정 장애물은 로봇 footprint와
inflation 바깥인 최소 20cm에 투영해 local·global costmap에 함께 추가됩니다.
MPPI가 가까이 피하지 못하면 Smac2D가 복도 단위 우회 경로를 다시 만들며,
PGM/YAML 지도에는 저장되지 않습니다. 같은 거리에서 LiDAR도 물체를 보고 있으면
벽으로 판단해 저상 장애물로 확정하지 않습니다.
좌우 90° LiDAR 최솟값은 옆 벽 문맥을 관제에 설명하기 위한 진단값이며, 그
값만으로 실제 저상 장애물을 무시하지 않습니다. costmap 투영 거리는 실제
정지 판단과 분리되어 있고, 전진 접근 중에는 표식을
유지하고, 로봇이 옆·뒤로 10cm 이동하거나 20도 회전해 회피가 확인되면 1.5초
유예 후 과거 부채꼴을 local·global costmap에서 지웁니다. 재검출되면 삭제를
취소해 우회 경로가 출렁이지 않게 합니다. 같은 수명의 실시간
부채꼴이 엔지니어·의료진 3D 지도에도 표시됩니다.
15cm 이내에서는 전진 상한을 0.08m/s부터 4cm까지 거리에 비례해 낮추고,
저상 장애물 확정 후 4cm에서 전진 성분을 0으로 만듭니다. 회전과 후퇴는 기존
Nav2 판단을 유지합니다.
관제는 이 자동 판정 상태만 표시하며 알고리즘을 선택하지 않습니다.

20cm 미만 값은 footprint와 inflation 안쪽을 lethal cost로 만들어 모든
MPPI·Smac2D 복구 경로의 시작점을 막을 수 있으므로 costmap에는 안전한 최솟값
20cm로 투영해 넣습니다.
기존 cone을 지워 planner가 장애물을 놓치는 문제를 피하면서, 실제 초음파
거리로 전진 속도를 제한해 회전과 Adaptive Recovery 공간을 남깁니다.

센서 또는 융합 노드가 끊겨도 기존 LiDAR costmap을 `not current`로 만들지
않습니다. 대신 `/low_obstacle/state`가 `STALE_RANGE` 또는 `STALE_LIDAR`를
보고합니다. 실로봇 비교 시험에서만 다음과 같이 끌 수 있습니다.

```bash
ros2 launch mingky_bringup mingky_system.launch.xml \
  low_obstacle_fusion_enabled:=false
```

카메라가 없는 개발 PC 또는 Nav2 단독 시험에서는 QR Reader를 끕니다. USB
카메라로 QR을 읽을 때는 소스만 바꿉니다.

```bash
ros2 launch mingky_bringup mingky_system.launch.xml start_qr_reader:=false
ros2 launch mingky_bringup mingky_system.launch.xml qr_source:=usb
```

Nav2의 `cmd_vel_smoothed`와 원격 조작의 `cmd_vel_teleop`은 `twist_mux`에서
중재된 뒤 저상 장애물 감독을 거쳐 `cmd_vel_safety_input`으로 연결됩니다.
실제 `/cmd_vel`은 기존 비상정지 안전 게이트만 발행합니다. 일반 운영에서
`pinky_navigation bringup_launch.xml`을 직접 실행하면 이 연결을 우회하므로
통합 launch를 사용하세요.

원격 조작의 `teleop_bridge`는 `mingky-teleop-bridge.service`,
`mode_manager`와 `teleop_limiter`는 `fg-teleop.service`가 상시 실행합니다.
통합 launch는 이 노드를 중복 실행하지 않고 `twist_mux`만 소유합니다.

로봇 설치 스크립트는 통합 launch를 `mingky-system.service`로 등록합니다.
엔지니어 시스템 관리 화면(`/engineer/system`)에서 실제 systemd 상태를 확인하고 가동·중지·재시작할
수 있습니다. 이벤트 gateway, 배터리 publisher와 원격 조작 수신기는 이 유닛
밖의 상시 서비스이므로 통합 시스템이 중지돼도 관제 명령과 상태 보고는
유지됩니다. 활성 환자 안내 중에는 중지·재시작 명령을 서버와 로봇 양쪽에서
거부합니다.

같은 화면의 `주행 속도 설정`은 Nav2 `FollowPath.desired_linear_vel`을
0.05–0.25m/s 범위에서 0.01m/s 단위로 조절합니다. 기본 목표속도는 0.20m/s이고
`velocity_smoother`의 기존 0.25m/s 상한은 그대로 유지됩니다. 환자 안내·화재
경보·위치 재탐색 중이거나 자동 주행 모드가 아니면 변경을 거부하며, 통합
시스템을 재시작하면 기본값으로 돌아갑니다.

## 환자 거리 기반 안내

`start_patient_follow:=true`를 주면 후방 카메라의 환자 QR·YOLO로
안내 속도를 조절합니다. 기본 임계값은 0.15m 감속, 0.30m 대기이며
YOLO는 13cm 인형 높이와 카메라 보정값으로 절대거리를 근사합니다.
QR은 환자 ID와 YOLO 거리를 다시 보정하는 기준으로 사용합니다.

환자가 멀어지면 Guide Manager가 현재 Nav2 목표를 정상 취소해
Adaptive Recovery가 실행되지 않게 하고, 환자가 복귀하면 같은 Waypoint를
다시 전송합니다. 카메라·QR·추적 heartbeat가 끊겨도 대기로 전환합니다.

현재 시험을 위해 기본값이 `true`입니다. 운영 로봇은
`/etc/mingky/robot.env`에서 다음 값을 설정합니다.

```dotenv
MINGKY_PATIENT_FOLLOW_ENABLED=true
MINGKY_PATIENT_FOLLOW_INFER_SERVER_URL=http://<GPU-PC-IP>:5001/infer
MINGKY_PATIENT_FOLLOW_SLOW_DISTANCE_M=0.15
MINGKY_PATIENT_FOLLOW_STOP_DISTANCE_M=0.30
MINGKY_PATIENT_FOLLOW_TRACKING_GRACE_SEC=2.0
MINGKY_PATIENT_FOLLOW_INITIAL_ACQUIRE_GRACE_SEC=4.0
MINGKY_PATIENT_FOLLOW_INITIAL_ACQUIRE_DISTANCE_M=0.30
MINGKY_PATIENT_FOLLOW_TARGET_HEIGHT_M=0.13
MINGKY_PATIENT_FOLLOW_SLOW_SPEED_PERCENT=35.0
```

YOLO를 쓰지 않으면 URL을 비워 QR 거리 단독으로 실행할 수 있습니다.

같은 화면의 `AMCL 위치 재탐색`은 엔지니어가 명시적으로 실행합니다. 의료진은
선택한 로봇 화면의 `로봇 위치 다시 찾기`로 같은 기능을 실행할 수 있습니다. 활성 안내
세션 전체와 Waypoint 시험 주행 중에는 시작할 수 없고, 재탐색이 실행 중일 때도
새 안내·시험 주행을 시작할 수 없습니다.

재탐색은 충전소 좌표나 AMCL particle의 단순 수렴을 정답으로 사용하지 않습니다.
현재 `/map`과 `/scan`으로 전역 위치 후보 Top-K를 만들고, 1등 점수와 2등과의
차이가 모두 충분한지 확인합니다. 후보가 비슷하면 앞뒤의 안전한 방향으로 5cm씩,
최대 15cm만 움직이며 새 scan으로 후보를 제거합니다. 같은 후보가 연속 확인된
경우에만 `/initialpose`를 발행하고 AMCL은 이후 추적을 담당합니다.

고정 초기 위치가 없으면 `map→odom`이 생기기 전에는 Nav2 planner가 active가
될 수 없습니다. 재탐색은 이 상태에서도 AMCL·map·scan·odom만으로 실행되며,
검증된 위치를 적용한 다음 중단됐던 navigation lifecycle을 자동으로 초기화하고
다시 활성화합니다.

AMCL 인수 확인은 정지 상태에서도 도착하는 첫 새 particle을 기준으로 합니다.
LiDAR 후보는 앞 단계에서 두 scan으로 이미 확인했으므로 AMCL particle의 두 번째
자발 갱신을 요구하지 않으며, 위치 퍼짐 15cm·seed 차이 20cm·방향 20도 안이면
주행 가능한 초기 위치로 인정합니다.

대칭 복도처럼 짧게 움직여도 구분할 수 없는 경우에는 잘못된 위치를 선택하지 않고
`ambiguous_candidates`로 실패합니다. 그 밖에 `no_map`, `no_scan`, `stale_scan`,
`no_candidates`, `probe_blocked`, `timeout`, `amcl_seed_rejected`로 원인을 구분합니다.

## QR 안내 상태머신 테스트

QR을 인식하면 Guide Manager는 세션을 `patient_confirmed` 상태로 저장하고
`session.ready` 이벤트를 발행한 뒤 관제 출발 명령을 기다립니다. 의료진 화면에서
로봇을 자동 주행 모드로 전환하고 **안내 시작**을 누르면 현재 세션 ID가
HTTP 명령 큐와 Event Gateway를 거쳐 Guide Manager로 전달됩니다.

아래 스크립트는 대시보드 없이 ROS 배관만 점검할 때 사용합니다.

```bash
ros2 topic echo --once /guide_manager/state
ros2 run mingky_bringup start_guidance_test.sh <session_id>
```

명령의 `session_id`가 현재 확인된 세션과 같을 때만 첫 검사실 waypoint로
주행합니다. 이미 출발한 세션, 배터리 부족·비상정지 상태, 등록되지 않은
검사실은 거부합니다.

출발 후에는 다음 순서를 자동으로 반복합니다.

1. 현재 검사실의 `goal` waypoint로 이동
2. 임상 도착을 기록하고 같은 검사실의 `waiting` waypoint로 이동
3. `in_room + waiting`에서 동일 환자 QR을 기다림
4. QR을 다시 읽으면 현재 단계를 완료하고 다음 검사실로 출발
5. 마지막 검사실이면 세션을 `completed`로 종료

처음 QR은 관제에서 활성화(arming)한 로봇만 인식합니다. 검사 완료
QR은 활성 세션의 waiting 상태에서만 스캔 창이 자동으로 열리므로
별도 arming이 필요하지 않습니다. 다른 환자·세션 QR은 거부합니다.

QR 카메라와 백엔드 없이 첫 출발 배관만 시험하려면 먼저 가짜 세션을 한 번
발행할 수 있습니다. 완료 QR 반복 흐름은 백엔드의 활성 세션 응답까지
포함하므로 통합 환경에서 테스트합니다.

```bash
ros2 topic pub --once /qr_reader_node/session_start \
  mingky_interfaces/msg/SessionStart \
  "{session_id: 9001, patient_id: test-patient, current_step_order: 1, visit_names: ['X-ray']}"

ros2 run mingky_bringup start_guidance_test.sh 9001
```

두 번째 명령은 실제 Nav2 목표를 전송합니다. 실로봇에서는 Nav2 localization과
초기 위치 설정을 마치고, 로봇 주변과 비상정지 동작을 확인한 뒤 실행하세요.

## 후방 USB 카메라

Pinky에 V4L2 드라이버를 설치합니다.

```bash
sudo apt install ros-jazzy-v4l2-camera
```

후방 카메라는 번호가 바뀌는 `/dev/videoN` 대신 장치의 고정 by-id 경로를
사용합니다. 기본 영상은 QR 거리 측정과 환자 YOLO 추론을 함께 지원하는
`640x480 bgr8` 컬러이며 로봇 내부의
다음 토픽으로 발행됩니다.

```text
/rear_camera/image_raw
/rear_camera/camera_info
/rear_qr/observation
```

실행과 확인:

```bash
ros2 launch mingky_bringup rear_camera.launch.py
ros2 topic hz /rear_camera/image_raw
ros2 topic echo /rear_camera/camera_info --once
ros2 topic echo /rear_qr/observation
```

다른 장치를 시험할 때만 launch 인자로 덮어씁니다.

```bash
ros2 launch mingky_bringup rear_camera.launch.py video_device:=/dev/video8
```

QR 거리 측정만 단독 시험해 대역폭을 줄여야 할 때는
`output_encoding:=mono8`으로 덮어쓸 수 있습니다.

`camera_info_url`은 캘리브레이션 전까지 비워 둡니다. 영상 메시지의 frame은
`rear_camera_optical_frame`이며 URDF의 `rear_camera_link` 아래에 등록됩니다.

## Waypoint 측정 준비

Pinky에서 `pinky_bringup`이 실행 중이어야 합니다.

기본 설정은 로봇이 공유기(`mingky`)에 붙어 있는 상태입니다.

| 항목 | 기본값 |
| --- | --- |
| Pinky IP | `192.168.0.21` (pinky1) |
| ROS Domain ID | `21` |
| Wi-Fi SSID 검사 | 하지 않음 |

SSID 를 강제하지 않는 이유는 관제컴퓨터처럼 유선으로 붙는 경우도 있기
때문입니다. **실제로 `ping` 이 닿는지만 봅니다.**

pinky2 로 작업하거나 AP 모드로 직접 붙는 경우는 환경변수로 바꿉니다.

```bash
# pinky2
PINKY_IP=192.168.0.22 PINKY_DOMAIN_ID=20 ros2 run mingky_bringup run_waypoint_teleop.sh

# AP 모드로 직접 접속했을 때
PINKY_IP=192.168.4.1 PINKY_SSID=pinky_6294 ros2 run mingky_bringup run_waypoint_teleop.sh
```

프로젝트를 빌드하고 환경을 불러옵니다.

```bash
cd <mingky_care_pro>
colcon build --packages-select mingky_bringup
source install/setup.bash
```

## Localization, RViz, teleop 실행

```bash
ros2 run mingky_bringup run_waypoint_teleop.sh
```

실행하면 `map/` 아래의 지도 목록이 표시됩니다. 선택한 `map_name.yaml`에 대해
waypoint는 `config/waypoints/map_name_waypoints.yaml`으로 관리됩니다.

이 명령은 전체 Nav2 주행 스택 대신 다음 구성요소만 실행합니다.

- `map_server`
- AMCL localization
- RViz
- `teleop_twist_keyboard`

RViz가 열리면 `2D Pose Estimate`로 로봇의 실제 위치와 방향을 먼저 지정합니다.
그다음 teleop으로 로봇을 waypoint 위치까지 이동하고 정지합니다.

## Waypoint 저장

실행 세션을 유지한 채 다른 터미널에서 프로젝트 환경을 불러오고 현재 위치를
저장합니다.

```bash
source <mingky_care_pro>/install/setup.bash
ros2 run mingky_bringup capture_waypoint.sh <waypoint_name>
```

예시:

```bash
ros2 run mingky_bringup capture_waypoint.sh reception_goal
```

좌표는 측정 세션에서 선택한 지도 전용 파일
`config/waypoints/<map_name>_waypoints.yaml`에 `x`, `y`, `yaw` 형식으로
추가됩니다. 같은 이름이 이미 존재하면 기존 좌표를 덮어쓰지 않고 중단합니다.

## 일반 Nav2 실주행 테스트 (RViz 직접 목표 지정)

저장 waypoint 없이, 지도 위 원하는 위치를 직접 클릭해 Nav2 주행을 시험할 때
사용합니다.

```bash
ros2 run mingky_bringup run_nav2_manual_test.sh
```

지도 선택 후 RViz에서 다음 순서로 진행합니다.

1. `2D Pose Estimate`로 로봇의 실제 초기 위치와 방향을 지정합니다.
2. 터미널에서 Enter를 눌러 `map → base_footprint` TF 연결을 확인합니다.
3. RViz의 `Nav2 Goal` 도구로 지도 위 목표 위치를 클릭하고, 드래그하여
   도착 방향을 지정합니다.

목표를 자유롭게 바꿔가며 costmap, 경로, 주행 동작을 확인할 수 있습니다.
이 스크립트도 `map/` 바로 아래의 지도만 선택 목록에 표시합니다.

## 저장된 waypoint Nav2 실주행 테스트

Pinky에서 `pinky_bringup`이 실행 중인 상태에서 Nav2와 RViz를 실행합니다.

```bash
ros2 run mingky_bringup run_nav2_waypoint_test.sh
```

RViz가 열리면 `2D Pose Estimate`로 실제 로봇의 초기 위치와 방향을 지정하고,
터미널의 한국어 메뉴에서 이동할 waypoint를 선택합니다. 주행이 성공하면 다시
메뉴가 표시되므로 가까운 두 waypoint를 연속으로 선택해 tolerance별 동작을
비교할 수 있습니다.

도착 허용 오차와 costmap 값은 스크립트에서 변경하지 않습니다. 설정 파일을
수정하거나 실행 중 RQT에서 조정한 다음 동일한 스크립트로 주행을 비교합니다.

```bash
ros2 run rqt_reconfigure rqt_reconfigure
```

실행 시 주행할 지도를 선택하면 대응하는
`config/waypoints/<map_name>_waypoints.yaml`을 자동 사용합니다.

```bash
ros2 run mingky_bringup run_nav2_waypoint_test.sh
```

스크립트는 이전에 성공한 waypoint와 새 목표 사이의 저장 좌표 거리 및 방향
차이를 계산합니다. 두 값이 현재 tolerance 안쪽이면 즉시 성공 가능성을, 위치만
안쪽이면 제자리 회전 가능성을 주행 전에 경고합니다. 테스트 중에는 Nav2와
teleop이 동시에 속도 명령을 보내지 않도록 teleop을 실행하지 않습니다.

## 환경 변수

기본값을 변경해야 할 때 다음 환경 변수를 사용할 수 있습니다.

- `PINKY_IP`: Pinky IP, 기본값 `192.168.0.21`
- `PINKY_SSID`: 지정하면 그 SSID 에 연결됐는지 검사한다. 기본값 없음(검사 안 함)
- `PINKY_DOMAIN_ID`: ROS Domain ID, 기본값 `21`
- `MINGKY_REPO`: 자동 탐색 대신 사용할 저장소 루트 경로

## 맵

기준 맵은 `map/yun_map_highres_clean.yaml` 입니다.

```
192 x 147 px   resolution 0.025   origin (-1.818, -1.529)
범위 x[-1.818, 2.982]  y[-1.529, 2.146]   = 4.80 x 3.68 m
```

**맵과 waypoint 는 같은 패키지에 둡니다.** 좌표가 맵에 종속적이라 따로
관리하면 어느 맵 기준인지 알 수 없게 되고, 맵을 교체했을 때 조용히
어긋납니다.

이전 맵(`map/archive/yun_map.yaml`, res 0.05)에서 넘어오면서
`origin` 이 `(-0.169, -1.847)` → `(-1.818, -1.529)` 로 바뀌어 **기존 waypoint
23개가 전부 무효**가 되었습니다. 참고용으로
`config/waypoints/archive/yun_map_waypoints.yaml` 에 남겼습니다.

`map/` 바로 아래에는 현재 선택 가능한 지도만 둡니다. 이전 측정본과 편집 전
원본은 `map/archive/`에 보관하며, waypoint도 대응하는 `config/waypoints/archive/`
에 함께 둡니다. waypoint 측정·주행 스크립트는 `archive/`를 지도 선택 목록에
표시하지 않습니다.

## Waypoint 검증

```bash
ros2 run mingky_bringup check_waypoints.py
```

두 가지를 검사합니다.

**1. 차체와 벽 사이 거리** — waypoint의 yaw로 회전한 실제 12×12cm
footprint 외곽부터 점유 셀까지의 여유를 계산합니다. 정적 지도에서 차체가
점유 셀과 **겹칠 때만 차단**하고, **0cm 초과 2cm 미만은 경고**합니다.
관제의 Waypoint Check도 같은 기준을
사용합니다. 경고 상태는 좁은 위치 시험을 위해 주행할 수 있지만 현장에서
충돌 여유를 확인해야 합니다.

**2. waypoint 사이 간격** — 두 지점이 `xy_goal_tolerance` 지름보다 가까우면,
한쪽에 서 있는 상태에서 다른 쪽으로 보내도 이미 도착 조건을 만족해
**로봇이 움직이지 않고 즉시 성공을 반환합니다.**

### 찍기 전에 확인하기

측정한 뒤에 틀린 걸 알면 다시 찍어야 합니다. **찍기 전에 현재 자리가
쓸 만한지 먼저 보세요.**

```bash
ros2 run mingky_bringup check_waypoints.py --probe
```

```
현재 위치  x=1.300  y=-1.350  yaw=0.000
차체-벽 여유 0.152m  ○ 좋습니다.
가장 가까운 기존 waypoint  reception_goal  0.641m  ○

여기서 찍어도 됩니다.
```

차체-벽 여유와 **이미 찍은 waypoint 와의 간격**을 함께 봅니다.
너무 가까우면 두 지점을 구분하지 못해 로봇이 움직이지 않습니다.

좌표를 직접 넣어 확인할 수도 있습니다.

```bash
ros2 run mingky_bringup check_waypoints.py --at 1.30 -1.35
```

```bash
# 다른 맵으로 검사
ros2 run mingky_bringup check_waypoints.py --map <경로>/yun_map.yaml

# 파라미터를 바꿨다면 같이 넘긴다
ros2 run mingky_bringup check_waypoints.py --tolerance 0.04 \
  --minimum-clearance 0.0 --margin 0.02 --padding 0.01
```

도달 불가나 맵 밖 waypoint 가 있으면 종료 코드 `1` 을 돌려줍니다.

### 맵을 바꿨다면 반드시 돌리세요

**좌표는 맵에 종속적입니다.** `origin` 이나 `resolution` 이 바뀌면 기존
waypoint 가 전혀 다른 물리 위치를 가리킵니다. 맵 밖으로 나가면 에러라도
나지만, 안쪽에 남으면 조용히 틀립니다.

그래서 맵과 waypoint 는 같은 패키지에 두고 함께 버전 관리해야 합니다.
새 맵을 채택하면 `mingky_bringup/map/` 에 넣으세요.

### 재측정 기준

| 항목 | 값 |
| --- | --- |
| 차체-벽 차단 기준 | **0.01 m 미만** |
| 차체-벽 경고 기준 | **0.08 m 미만** |
| `_goal` ↔ `_waiting` 간격 | **0.08 m** 이상 (나눌 경우) |
| 충전소 | 도킹 지점이 아니라 **앞 0.3 m** |

충전소 접점은 벽에 닿아야 해서 planner 가 구조적으로 도달할 수 없습니다.
Nav2 로 앞까지 간 뒤 마지막 접근은 별도 동작으로 처리해야 합니다.

## Foxglove Bridge

Nav2 디버깅용 실시간 시각화입니다.

```bash
sudo apt install ros-jazzy-foxglove-bridge
ros2 launch mingky_bringup foxglove.launch.py
```

**로봇마다 도메인이 달라(pinky1=21, pinky2=20) 관제 한 곳에서 두 대를 동시에
볼 수 없습니다.** 로봇마다 하나씩 띄우고 Studio 에서 접속을 갈아탑니다.

| 로봇 | 접속 주소 |
| --- | --- |
| pinky1 | `ws://192.168.0.21:8765` |
| pinky2 | `ws://192.168.0.22:8765` |

### 주행 디버깅에 띄울 패널

| 패널 | 토픽 | 무엇을 보나 |
| --- | --- | --- |
| Map | `/map` | 맵이 실제와 맞나 |
| Costmap | `/global_costmap/costmap` | **팽창이 통로를 막나** |
| Path | `/plan`, `/local_plan` | 경로가 목표까지 그려지나 |
| LaserScan | `/scan` | 라이다가 맵과 겹치나 |
| Pose | `/amcl_pose`, `/particle_cloud` | **위치추정이 발산하나** |
| Plot | `/cmd_vel` | 직진이 0 인데 회전만 있나 |
| Parameters | `controller_server` 등 | 재시작 없이 튜닝 |

**Parameters 패널로 재시작 없이 값을 바꿀 수 있습니다.** 다만 Nav2 는
라이프사이클 노드라 일부는 `configure` 때만 읽습니다. `read_only` 여부는
이렇게 확인합니다.

```bash
ros2 param describe /controller_server general_goal_checker.xy_goal_tolerance
```

런타임 변경은 재시작하면 날아갑니다. **좋아진 값은 `nav2_params.yaml` 에
반영해서 커밋하세요.**
