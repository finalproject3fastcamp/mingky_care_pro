# mingky_bringup

Mingky Care 프로젝트의 통합 실행 설정과 병원 waypoint를 관리하는 ROS 2 패키지입니다.

## Waypoint 측정 준비

Pinky에서 `pinky_bringup`이 실행 중이어야 합니다. PC는 `pinky_6294` Wi-Fi에
연결되어 있어야 하며, 기본 설정은 Pinky IP `192.168.4.1`, ROS Domain ID
`21`입니다.

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

좌표는 `config/hospital_waypoints.yaml`에 `x`, `y`, `yaw` 형식으로 추가됩니다.
같은 이름이 이미 존재하면 기존 좌표를 덮어쓰지 않고 중단합니다.

## 저장된 waypoint Nav2 실주행 테스트

Pinky에서 `pinky_bringup`이 실행 중인 상태에서 위치·방향 도착 허용 오차를
지정해 Nav2와 RViz를 실행합니다.

```bash
ros2 run mingky_bringup run_nav2_waypoint_test.sh --xy 0.25 --yaw 0.25
```

RViz가 열리면 `2D Pose Estimate`로 실제 로봇의 초기 위치와 방향을 지정하고,
터미널의 한국어 메뉴에서 이동할 waypoint를 선택합니다. 주행이 성공하면 다시
메뉴가 표시되므로 가까운 두 waypoint를 연속으로 선택해 tolerance별 동작을
비교할 수 있습니다.

예를 들어 접수처와 수납 창구를 `0.25m`, `0.15m`, `0.10m`로 비교하려면 각
설정으로 스크립트를 다시 실행합니다.

```bash
ros2 run mingky_bringup run_nav2_waypoint_test.sh --xy 0.15 --yaw 0.25
ros2 run mingky_bringup run_nav2_waypoint_test.sh --xy 0.10 --yaw 0.25
```

스크립트는 이전에 성공한 waypoint와 새 목표 사이의 저장 좌표 거리 및 방향
차이를 계산합니다. 두 값이 현재 tolerance 안쪽이면 즉시 성공 가능성을, 위치만
안쪽이면 제자리 회전 가능성을 주행 전에 경고합니다. 테스트 중에는 Nav2와
teleop이 동시에 속도 명령을 보내지 않도록 teleop을 실행하지 않습니다.

## 환경 변수

기본값을 변경해야 할 때 다음 환경 변수를 사용할 수 있습니다.

- `PINKY_IP`: Pinky IP, 기본값 `192.168.4.1`
- `PINKY_SSID`: Pinky Wi-Fi SSID, 기본값 `pinky_6294`
- `PINKY_DOMAIN_ID`: ROS Domain ID, 기본값 `21`
- `MAP_PATH`: 지도 YAML 경로
- `WAYPOINT_FILE`: waypoint 출력 YAML 경로
- `MINGKY_REPO`: 자동 탐색 대신 사용할 저장소 루트 경로
