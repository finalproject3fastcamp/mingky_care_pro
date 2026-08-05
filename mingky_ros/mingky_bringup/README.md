# mingky_bringup

Mingky Care 프로젝트의 통합 실행 설정과 병원 waypoint를 관리하는 ROS 2 패키지입니다.

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
PINKY_IP=192.168.0.22 PINKY_DOMAIN_ID=22 ros2 run mingky_bringup run_waypoint_teleop.sh

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
ros2 run mingky_bringup run_nav2_waypoint_test.sh \
  --xy 0.25 --yaw 0.25 --inflation 0.15
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

장애물 팽창 영역이 통로를 막는지 비교하려면 `--inflation`으로 global/local
costmap의 팽창 반경을 함께 지정합니다. 현재 기본값은 `0.15m`이며 실제 로봇의
footprint와 안전 여유를 고려해 우선 `0.13m`까지만 줄여 시험합니다.

```bash
ros2 run mingky_bringup run_nav2_waypoint_test.sh \
  --xy 0.15 --yaw 0.25 --inflation 0.13
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
- `MAP_PATH`: 지도 YAML 경로 (기본 `map/yun_map_highres_clean.yaml`)
- `WAYPOINT_FILE`: waypoint 출력 YAML 경로
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

이전 맵(`pinky_map/pinky_6294/yun_map.yaml`, res 0.05)에서 넘어오면서
`origin` 이 `(-0.169, -1.847)` → `(-1.818, -1.529)` 로 바뀌어 **기존 waypoint
23개가 전부 무효**가 되었습니다. 참고용으로
`config/hospital_waypoints.legacy-yun_map.yaml` 에 남겼습니다.

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

**로봇마다 도메인이 달라(pinky1=21, pinky2=22) 관제 한 곳에서 두 대를 동시에
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
| Pose | `/amcl_pose`, `/particlecloud` | **위치추정이 발산하나** |
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
