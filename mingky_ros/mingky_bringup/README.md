# mingky_bringup

Mingky Care 프로젝트의 통합 실행 설정과 병원 waypoint를 관리하는 ROS 2 패키지입니다.

## 운영 통합 실행

실제 로봇 베이스, Nav2, 속도 명령 중재기, 배터리 감시, 비상정지 안전 게이트,
Guide Manager와 Navigation Manager를 한 번에 실행합니다. 배터리 publisher, 이벤트 gateway와 원격
조작 bridge·모드 관리자·속도 제한기는 로봇 설치 시 등록한 systemd 상시
서비스를 사용합니다.

```bash
ros2 launch mingky_bringup mingky_system.launch.xml \
  robot_id:=pinky-01 backend_url:=https://mingkycarepro.site/api
```

실로봇 기본값은 CSI QR 카메라와 LiDAR 적응형 복구를 함께 실행합니다. QR
Reader는 인식 후 백엔드에서 받은 세션을 Guide Manager로 전달하며, 일반
주행이 실패하면 적응형 복구가 안전한 임시 탈출 지점을 찾아 원래 목표를 다시
시도합니다. 전역 경로 계획기는 검증된 `navfn`을 그대로 사용합니다.

Guide Manager는 환자 세션과 검사실 순서를 관리합니다. 엔지니어 화면의 저장
Waypoint·임시 좌표 시험 주행은 별도 Navigation Manager가 담당합니다. 시험
주행은 한 번에 하나만 허용하며 환자 안내, 저전압 또는 비상정지 상태에서는
시작하지 않습니다. 시험 중 환자 세션이 확인되면 시험 목표를 취소합니다.

`robot_id`의 숫자 접미사로 충전소를 선택합니다. 예를 들어 `pinky-02`는
`charging_station_2`를 사용합니다. 명시적으로 바꾸려면
`charging_waypoint:=charging_station_1`을 전달합니다.

로봇 베이스가 다른 장치에서 이미 실행 중이면 `start_robot_base:=false`를
사용합니다. `start_event_gateway` 기본값은 `false`라 운영 로봇의
`mingky-gateway.service`와 중복되지 않습니다. systemd가 없는 개발 장치에서만
`start_event_gateway:=true`로 실행합니다. 다른 맵을 쓸 때는 같은 맵에 대응하는
`map`, `map_name`, waypoint 파일을 함께 바꿔야 합니다.

카메라가 없는 개발 PC 또는 Nav2 단독 시험에서는 QR Reader를 끕니다. USB
카메라로 QR을 읽을 때는 소스만 바꿉니다.

```bash
ros2 launch mingky_bringup mingky_system.launch.xml start_qr_reader:=false
ros2 launch mingky_bringup mingky_system.launch.xml qr_source:=usb
```

Nav2의 `cmd_vel_smoothed`와 원격 조작의 `cmd_vel_teleop`은 `twist_mux`에서
중재된 뒤 `cmd_vel_safety_input`으로 연결됩니다. 실제 `/cmd_vel`은 안전
게이트만 발행합니다. 일반 운영에서 `pinky_navigation bringup_launch.xml`을 직접
실행하면 이 연결을 우회하므로 통합 launch를 사용하세요.

원격 조작의 `teleop_bridge`는 `mingky-teleop-bridge.service`,
`mode_manager`와 `teleop_limiter`는 `fg-teleop.service`가 상시 실행합니다.
통합 launch는 이 노드를 중복 실행하지 않고 `twist_mux`만 소유합니다.

## QR 안내 상태머신 테스트

QR을 인식하면 Guide Manager는 세션을 `patient_confirmed` 상태로 저장하고
관제 출발 명령을 기다립니다. 관제 시작 버튼이 연결되기 전에는 현재 세션 ID를
확인한 뒤 테스트 명령으로 동일한 출발 신호를 보낼 수 있습니다.

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
사용합니다. 기본 영상은 ArUco 처리에 맞춘 `640x480 mono8`이며 로봇 내부의
다음 토픽으로 발행됩니다.

```text
/rear_camera/image_raw
/rear_camera/camera_info
```

실행과 확인:

```bash
ros2 launch mingky_bringup rear_camera.launch.py
ros2 topic hz /rear_camera/image_raw
ros2 topic echo /rear_camera/camera_info --once
```

다른 장치를 시험할 때만 launch 인자로 덮어씁니다.

```bash
ros2 launch mingky_bringup rear_camera.launch.py video_device:=/dev/video8
```

컬러 영상 확인이 필요하면 `output_encoding:=bgr8`을 추가합니다.

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

**1. 벽까지 거리** — Nav2 는 로봇 내접 반경 + `footprint_padding` 안쪽 셀을
통과 불가로 봅니다. 그보다 벽에 가까운 waypoint 는 planner 가 도달할 수
없는데, NavFn 의 `tolerance` 가 "근처까지만" 경로를 그려주기 때문에
**에러 없이 조용히 엉뚱한 곳에서 멈춥니다.**

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
현재 위치  x=1.300  y=-1.350
벽까지     0.212m  ○ 좋습니다.
가장 가까운 기존 waypoint  reception_goal  0.641m  ○

여기서 찍어도 됩니다.
```

벽까지 거리와 **이미 찍은 waypoint 와의 간격**을 함께 봅니다.
너무 가까우면 두 지점을 구분하지 못해 로봇이 움직이지 않습니다.

좌표를 직접 넣어 확인할 수도 있습니다.

```bash
ros2 run mingky_bringup check_waypoints.py --at 1.30 -1.35
```

```bash
# 다른 맵으로 검사
ros2 run mingky_bringup check_waypoints.py --map <경로>/yun_map.yaml

# 파라미터를 바꿨다면 같이 넘긴다
ros2 run mingky_bringup check_waypoints.py --tolerance 0.15 --padding 0.0
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
| 벽에서 최소 | **0.15 m** |
| `_goal` ↔ `_waiting` 간격 | **0.30 m** 이상 (나눌 경우) |
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
