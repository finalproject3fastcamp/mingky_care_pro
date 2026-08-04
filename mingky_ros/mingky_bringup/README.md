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

## 환경 변수

기본값을 변경해야 할 때 다음 환경 변수를 사용할 수 있습니다.

- `PINKY_IP`: Pinky IP, 기본값 `192.168.4.1`
- `PINKY_SSID`: Pinky Wi-Fi SSID, 기본값 `pinky_6294`
- `PINKY_DOMAIN_ID`: ROS Domain ID, 기본값 `21`
- `MAP_PATH`: 지도 YAML 경로
- `WAYPOINT_FILE`: waypoint 출력 YAML 경로
- `MINGKY_REPO`: 자동 탐색 대신 사용할 저장소 루트 경로
