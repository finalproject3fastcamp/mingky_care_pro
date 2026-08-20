# Nav2 주행 디버깅

주행이 안 될 때 **레이어별로 좁혀 들어갑니다.** 로그를 통째로 읽는 건
마지막 수단입니다.

## 0. 실패를 먼저 반으로 가른다

Nav2 결과 코드는 백의 자리로 원인이 갈립니다.

| 코드 | 의미 | 볼 곳 |
| --- | --- | --- |
| **1xx** | 경로 계획 실패 | 코스트맵 · 맵 · 시작/목표 지점 |
| **2xx** | 경로 추종 실패 | 컨트롤러 · TF · 진척도 |

이 코드는 이미 DB 에 쌓입니다.

```bash
curl -s "localhost:8000/events?code_prefix=nav.&min_level=error"
```

```json
{"event_code":"nav.goal_aborted",
 "payload":{"visit_name":"CT","error_code":103}}
```

정확한 코드 표는 버전마다 다릅니다.

```bash
ros2 interface show nav2_msgs/action/NavigateToPose | grep -A40 error_code
```

## 1. 증상 → 원인

| 증상 | 유력 원인 | 확인 |
| --- | --- | --- |
| **출발하자마자 좌우로 돌다 멈춤** | 전방이 막혀 직진 0. 인플레이션 과다 | `/cmd_vel` 의 `linear.x` 가 0 인데 `angular.z` 만 부호가 바뀌나 |
| **경로가 목표 앞까지만 그려짐** | **목표가 도달 불가 셀에 있음.** NavFn `tolerance` 가 근처까지만 계획 | `check_waypoints.py` |
| **벽 근처에서 후진·회전 반복** | 위와 같은 원인. recovery(`spin`/`backup`) 발동 | `/rosout` 에 `failed to make progress` |
| **계속 기어감** | `cost_scaling_dist` 가 맵 크기에 비해 큼 | `/cmd_vel` 이 `min_approach_linear_velocity` 근처 |
| **`reached` 만 반복, 안 움직임** | **목표가 이미 tolerance 안**. 즉시 성공 반환 | goal 전후 `/amcl_pose` 비교 |
| **엉뚱한 방 앞에서 도착** | `xy_goal_tolerance` 가 방 폭보다 큼 | `check_waypoints.py` 간격 검사 |
| **커브를 크게 돌아 벽에 붙음** | `lookahead` 가 맵 크기에 비해 큼 | `/local_plan` 모양 |
| **위치가 갑자기 튐** | 가속도 과다로 휠 슬립 → odom 오차 | `/particle_cloud` 퍼짐 |
| **첫 주행만 이상함** | `initial_pose` 가 실제 출발 위치와 다름 | 시작 직후 `/amcl_pose` |
| **무선·BLE 가 불안정, AP 모드로 폴백** | **저전압.** Pi 5 는 전압이 처지면 무선부터 죽는다 | 배터리 전압 |

마지막 줄을 먼저 확인하세요. 원인 불명 증상의 상당수가 배터리입니다.

## 2. 레이어별 진단 순서

### TF — 여기가 끊기면 나머지는 볼 필요가 없다

```bash
ros2 run tf2_ros tf2_echo map base_footprint
ros2 run tf2_tools view_frames
```

`map → odom → base_footprint → base_link` 가 이어져야 합니다.
`Could not find a connection` 이면 AMCL 이 `map → odom` 을 안 내고 있습니다.

### AMCL — Nav2 문제의 절반

Foxglove 에서 `/particle_cloud` 와 `/amcl_pose` 를 봅니다.

> **토픽 이름 주의.** Nav2 Jazzy 는 `nav2_msgs/ParticleCloud` 타입으로
> `/particle_cloud` 에 냅니다. 옛 이름 `/particlecloud`
> (`geometry_msgs/PoseArray`) 도 `ros2 topic list` 에는 보이지만
> **발행자가 0** 입니다. 그쪽을 열면 아무것도 안 나와 AMCL 이 죽은
> 것처럼 보입니다.

| 보이는 것 | 판단 |
| --- | --- |
| 파티클이 로봇 주변에 뭉침 | 정상 |
| 넓게 퍼짐 | 발산. 초기 위치 오류이거나 라이다가 맵과 안 맞음 |
| 로봇이 벽 안에 있음 | **어느 방향이든 막혔다고 판단한다** |

### 코스트맵

`/global_costmap/costmap` 을 띄웁니다.

- 벽 위치가 실제와 맞나
- **팽창이 통로를 다 먹고 있나** ← 가장 흔한 원인
- 없는 곳에 장애물이 있나 (라이다 노이즈)

### 플래너

```bash
ros2 topic echo /plan --once | head -20
```

비어 있으면 1xx 실패입니다. 목표가 점유 셀인지 확인하세요.

### 컨트롤러

```bash
ros2 topic hz /cmd_vel
```

한 줄로 읽기:

```bash
python3 - <<'EOF'
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
rclpy.init(); n = Node('watch')
n.create_subscription(Twist, '/cmd_vel',
    lambda m: print(f"lin={m.linear.x:+.3f} ang={m.angular.z:+.3f} "
                    + '#'*int(abs(m.angular.z)*20)), 10)
rclpy.spin(n)
EOF
```

**echo 를 먼저 켜고 goal 을 보내세요.** 토픽은 지나가면 끝입니다.

`velocity_smoother` 가 끼어 있어 토픽 이름이 다를 수 있습니다.

```bash
ros2 topic list | grep cmd_vel
```

### 도착 판정

goal 전후로 `/amcl_pose` 를 비교합니다. 좌표가 그대로면 **안 움직이고 성공만
반환한 것**입니다.

## 3. 원인 확정용 임시 조치

```bash
ros2 param set /controller_server FollowPath.use_collision_detection false
```

이걸 끄고 직진하면 **코스트맵/인플레이션 문제로 확정**됩니다.
안전장치이므로 **확인 후 반드시 되돌리세요.**

## 4. 스케일 불일치 — 이 프로젝트의 고유 문제

`nav2_params.yaml` 은 TurtleBot3 기본값에서 출발했습니다.
**로봇이 1/4 크기, 맵이 1/10 크기인데 파라미터는 원본 스케일입니다.**

```
로봇 footprint   0.12 × 0.12 m   (내접 반경 0.06)
맵               2.0 × 3.4 m
방 폭            약 0.44 m
waypoint 최소 간격  0.127 m
```

| 파라미터 | 기본 | 이 환경 | 왜 |
| --- | --- | --- | --- |
| `inflation_radius` | 0.15 | **0.10** | 로봇 반폭 0.06. 양쪽에서 0.30m 를 먹으면 0.44m 통로가 막힌다 |
| `cost_scaling_dist` | 0.6 | **0.20** | 맵 폭이 2.0m 다. 벽에서 0.6m 안이면 전 구역이 감속 대상 |
| `required_movement_radius` | 0.5 | **0.10** | 실제 이동이 0.15~0.26m 다. **정상 주행이 끼임으로 판정된다** |
| `lookahead_dist` | 0.6 | **0.25** | 맵 폭의 30%. 카롯이 코너 너머에 놓인다 |
| `max_lookahead_dist` | 0.9 | **0.40** | 맵 폭의 45% |
| `xy_goal_tolerance` | 0.25 | **0.04** | 목적지를 구분하면서 중심 4cm 이내를 도착으로 인정한다 |
| `yaw_goal_tolerance` | 0.25 | **0.174533** | 목표 각도 오차 10도 이내를 도착으로 인정한다 |
| `amcl update_min_d` | 0.15 | **0.05** | 2m 맵에서 15cm 는 너무 성기다 |
| `amcl laser_max_range` | 100.0 | **12.0** | RPLIDAR C1 사양에 맞춘다 |
| `amcl transform_tolerance` | 1.0 | **0.3** | 컨트롤러는 0.2 인데 AMCL 만 1.0 이라 일관성도 없다 |
| `velocity_smoother max_accel` | 2.5 | **0.6** | 최고 0.25m/s 인데 0.1초 만에 최고속. 휠 슬립 → odom 오차 |

### 세 곳을 반드시 같이 바꿔야 하는 값

```yaml
controller_server.FollowPath.inflation_cost_scaling_factor
local_costmap.inflation_layer.cost_scaling_factor
global_costmap.inflation_layer.cost_scaling_factor
```

RPP 는 이 값으로 코스트를 거꾸로 풀어 "장애물까지 거리" 를 계산합니다.
인플레이션 레이어와 다르면 **거리를 잘못 계산해 엉뚱하게 감속합니다.**

### 일관성이 깨진 곳

| 항목 | 문제 |
| --- | --- |
| `bt_navigator.robot_base_frame` | 여기만 `base_link`. 나머지는 전부 `base_footprint` |
| `footprint_padding` | `global_costmap` 에만 있다. 플래너와 컨트롤러가 로봇 크기를 다르게 본다 |
| `voxel_layer` (local) | 2D 라이다인데 3D 복셀. Pi 5 에서 CPU 낭비. `obstacle_layer` 로 충분 |
| `local_costmap 3×3 m` | **맵 전체(2.0×3.4)보다 크다** |

## 5. 런타임 튜닝

재빌드 없이 바꿀 수 있는 것들입니다.

```bash
ros2 param set /controller_server FollowPath.cost_scaling_dist 0.2
ros2 param set /controller_server FollowPath.lookahead_dist 0.25
ros2 param set /controller_server progress_checker.required_movement_radius 0.1
ros2 param set /controller_server general_goal_checker.xy_goal_tolerance 0.04
ros2 param set /controller_server general_goal_checker.yaw_goal_tolerance 0.174533
ros2 param set /global_costmap/global_costmap inflation_layer.inflation_radius 0.10
ros2 param set /local_costmap/local_costmap  inflation_layer.inflation_radius 0.10
```

바뀌는지 미리 확인:

```bash
ros2 param describe /controller_server general_goal_checker.xy_goal_tolerance
```

`read_only: true` 면 재시작이 필요합니다. Nav2 는 라이프사이클 노드라
일부는 `configure` 때만 읽습니다.

**하나씩 바꾸면서 `/cmd_vel` 을 보세요.** 한꺼번에 바꾸면 뭐가 효과였는지
알 수 없습니다.

**런타임 변경은 재시작하면 날아갑니다.** 좋아진 값은 반드시
`nav2_params.yaml` 에 반영해 커밋하세요.

## 6. waypoint 검증

```bash
ros2 run mingky_bringup check_waypoints.py
```

자세한 내용은 [`mingky_ros/mingky_bringup/README.md`](../mingky_ros/mingky_bringup/README.md)
를 참고하세요.

**맵을 바꿨으면 반드시 돌려야 합니다.** 좌표는 맵에 종속적이라
`origin` 이나 `resolution` 이 바뀌면 기존 waypoint 가 전혀 다른 물리 위치를
가리킵니다. 맵 밖으로 나가면 에러라도 나지만 안쪽에 남으면 조용히 틀립니다.

## 7. rosbag — 재현이 핵심

실패는 대개 간헐적이라 실시간으로 못 잡습니다.

```bash
ros2 bag record -o fail_$(date +%H%M%S) \
  /scan /tf /tf_static /odom /amcl_pose /particle_cloud \
  /plan /local_plan /cmd_vel /rosout \
  /global_costmap/costmap /local_costmap/costmap
```

카메라는 뺐습니다. 용량이 크고 주행 디버깅에는 불필요합니다.

```bash
ros2 bag play fail_143022
```

Foxglove 로 보면서 실패 순간을 몇 번이고 다시 볼 수 있습니다.

## 8. 로그 레벨

기본 로그는 조용합니다. 재시작 없이 올릴 수 있습니다.

```bash
ros2 service call /controller_server/set_logger_levels \
  rcl_interfaces/srv/SetLoggerLevels \
  "{levels: [{name: 'controller_server', level: 10}]}"
```

`10=DEBUG` `20=INFO` `30=WARN`.
**끝나면 20 으로 되돌리세요.** DEBUG 는 Pi 5 에 부담입니다.
