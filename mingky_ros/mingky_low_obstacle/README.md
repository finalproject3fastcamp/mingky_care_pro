# mingky_low_obstacle

전방 초음파가 감지하지만 2D LiDAR 평면에는 보이지 않는 낮은 물체를 Nav2의
로컬 회피에 보태는 ROS 2 패키지입니다. 초음파만으로 물체의 정확한 좌우 위치를
만들지 않고, 센서가 실제로 제공하는 부채꼴 범위만 표현합니다.

## 처리 흐름

1. `/us_sensor/range`를 median 3개로 정리합니다.
2. 초음파 부채꼴과 겹치는 `/scan` 점을 TF 기준으로 비교합니다. 좌우 90°
   LiDAR 최솟값도 벽 문맥 진단값으로 남기지만, 이 값 하나로 실제 저상
   장애물을 없다고 판정하지 않습니다.
3. `초음파는 가깝고 LiDAR는 멀다`는 증거가 최근 3개 중 2개일 때만 낮은
   장애물로 확정합니다.
4. 확정된 `sensor_msgs/Range`를 `/low_obstacle/range`로 발행합니다.
5. Nav2 local costmap의 `RangeSensorLayer`와 MPPI가 가까운 회피를 수행하고,
   global costmap의 임시 레이어를 본 Smac2D가 필요하면 전체 우회 경로를
   다시 만듭니다.
6. 15cm 이내에서는 전진 상한을 0.08m/s부터 거리에 비례해 연속적으로 낮추고,
   **2-of-3 저상 장애물 확정 이후** 4cm에서 전진만 막습니다. 회전과 후퇴
   명령은 변경하지 않습니다.

일반 벽처럼 초음파와 LiDAR가 비슷한 거리를 함께 보고 있으면 낮은 장애물로
확정하지 않습니다. 따라서 벽 옆을 따라가는 것만으로 전진을 막지 않으며,
벽 모서리의 일시적인 초음파 반사도 확정 전에는 속도 명령에 개입하지 않습니다.

20cm 미만의 근접값은 실제 거리 그대로 찍지 않고 padded footprint와 local
inflation 바깥인 20cm로 투영해 표시합니다. 실제 거리 그대로 찍으면 MPPI와
Smac2D의 시작 자세가 충돌로 판정되지만, 최대 거리로 지워 버리면 Smac2D가
장애물을 보지 못해 우회 경로를 만들 수 없기 때문입니다. 별도의 속도 게이트는
실제 초음파 거리를 사용합니다.

확정 상태에서는 이 임시 Range를 계속 발행합니다. Nav2 costmap이 lifecycle
재활성화되거나 저상 장애물 노드보다 늦게 구독을 시작해도 최초 한 번의 표식을
놓치지 않도록 하기 위함입니다.

global costmap 반영도 같은 `/low_obstacle/range`를 사용합니다. 이는 PGM/YAML
지도를 수정하는 승격이 아니라 현재 자동주행 작업에만 존재하는 임시 비용입니다.
센서 한 프레임 누락으로 경로와 표시가 깜빡이지 않도록 배포 설정인 10Hz 초음파
기준 2회 연속 미검출(약 0.2초)을 먼저 확인합니다.

표식의 실제 수명은 시간이 아니라 **주행 작업 단위**입니다. 안내는 다음 목적지
도착·취소·최종 실패까지, Waypoint Test는 해당 시험주행 종료까지 감지한 표식을
지우지 않습니다. 로봇이 회전해 초음파 시야에서 잠깐 사라져도 우회 경로가 다시
원래 장애물 쪽으로 펴지지 않게 하기 위함입니다. 작업이 끝나면 local/global
costmap을 한 번 초기화해 그 작업에서 누적한 저상 장애물 표식을 제거합니다.
주행 중이 아닐 때는 costmap 표식을 새로 만들지 않지만, 초음파 상태의 관제 전송과
4cm 전진 안전 차단은 항상 유지됩니다.

`/low_obstacle/observation`은 확정 상태, 추정 거리, 실제 초음파 FOV를 JSON으로
발행합니다. Teleop WebSocket이 이 작은 메시지만 실시간 전달하고 3D 지도는
정확한 점 대신 전방 부채꼴을 표시합니다. 과거 관측이 만료되면 표시도 즉시
사라집니다.

기본 임계값은 센서값이 미터 단위로 안정적으로 들어온다는 전제의 보수적인
초기값입니다. ADC 물리 보정을 대신하지 않으므로 실기 로그를 보고
`detect_distance_m`, `lidar_margin_m`, `slow_distance_m`, `stop_distance_m` 순으로
조정해야 합니다.

## 상태

`/low_obstacle/state`는 transient-local QoS 문자열 토픽입니다.

- `CLEAR`: 낮은 장애물 증거 없음
- `UNCERTAIN`: 반복 확인 중
- `CONFIRMED`: local costmap에 반영됨
- `SLOW`: 15cm 이내 전진 감속
- `FORWARD_BLOCKED`: 4cm 이내 전진 차단
- `STALE_RANGE`, `STALE_LIDAR`: 입력이 오래됨
- `DISABLED`: launch 파라미터로 비활성화됨

센서가 끊기면 기존 LiDAR Nav2는 유지하고 상태만 stale로 보고합니다. 이미
확정된 15cm/4cm 장애물은 센서가 잠시 끊겨도 마지막 전진 제한을 유지하며,
과거 지도 표시는 현재 안내 구간 또는 Waypoint Test가 끝날 때 삭제합니다.

## 실행과 확인

통합 launch에서 기본 실행됩니다.

```bash
ros2 launch mingky_bringup mingky_system.launch.xml
ros2 topic echo /low_obstacle/state --qos-durability transient_local --once
ros2 topic echo /low_obstacle/range
```

비교 시험에서는 다음과 같이 끌 수 있습니다.

```bash
ros2 launch mingky_bringup mingky_system.launch.xml \
  low_obstacle_fusion_enabled:=false
```
