# mingky_low_obstacle

전방 초음파가 감지하지만 2D LiDAR 평면에는 보이지 않는 낮은 물체를 Nav2의
로컬 회피에 보태는 ROS 2 패키지입니다. 초음파만으로 물체의 정확한 좌우 위치를
만들지 않고, 센서가 실제로 제공하는 부채꼴 범위만 표현합니다.

## 처리 흐름

1. `/us_sensor/range`를 median 3개로 정리합니다.
2. 초음파 부채꼴과 겹치는 `/scan` 점을 TF 기준으로 비교합니다. 좌우 90°
   LiDAR 최솟값도 벽 문맥 진단값으로 남기지만, 이 값 하나로 실제 저상
   장애물을 없다고 판정하지 않습니다.
3. `초음파는 가깝고 LiDAR는 멀다`는 증거가 최근 5개 중 3개일 때만 낮은
   장애물로 확정합니다.
4. 확정된 `sensor_msgs/Range`를 `/low_obstacle/range`로 발행합니다.
5. Nav2 local costmap의 `RangeSensorLayer`와 MPPI가 가까운 회피를 수행하고,
   global costmap의 임시 레이어를 본 Smac2D가 필요하면 전체 우회 경로를
   다시 만듭니다.
6. 15cm 이내에서는 전진 속도만 0.08m/s로 제한하고, **3-of-5 저상 장애물
   확정 이후** 7cm 이내가 반복되면 전진만 막습니다. 회전과 후퇴 명령은
   변경하지 않습니다.

일반 벽처럼 초음파와 LiDAR가 비슷한 거리를 함께 보고 있으면 낮은 장애물로
확정하지 않습니다. 따라서 벽 옆을 따라가는 것만으로 전진을 막지 않으며,
벽 모서리의 일시적인 초음파 반사도 확정 전에는 속도 명령에 개입하지 않습니다.

10cm 미만의 초근접값은 RangeSensorLayer에 장애물 셀로 넣지 않고 최대 거리값을
발행해 기존 cone을 지웁니다. 이 거리에서 셀을 찍으면 padded footprint 바로
옆이 lethal cost가 되어 MPPI의 회전·후퇴 복구까지 막기 때문입니다. 별도의
전진 속도 게이트는 그대로 유지하므로 가까운 실제 장애물 쪽으로 전진하지는
않으며, Nav2는 회전과 Adaptive Recovery를 계속 시도할 수 있습니다.

global costmap 반영도 같은 `/low_obstacle/range`를 사용합니다. 이는 PGM/YAML
지도를 수정하는 승격이 아니라 실행 중인 임시 비용이며, 장애물이 사라져
최대 거리값이 들어오면 local·global 레이어에서 함께 지워집니다.
센서 한 프레임 누락으로 경로와 표시가 깜빡이지 않도록 배포 설정인 10Hz 초음파
기준 2회 연속 미검출(약 0.2초) 후 지웁니다.
또한 로봇이 최초 등록 자세에서 10cm 이동하거나 20도 회전하면 회피 전 센서
자세로 쌓인 부채꼴을 즉시 삭제합니다. 실제 장애물이 계속 보이면 현재 자세에서
새 관측으로 다시 등록되므로 동적 물체나 이미 지나간 물체가 지도에 고정되지
않습니다. 속도 제한 히스테리시스는 지도 관측 수명과 별도로 유지합니다.

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
- `FORWARD_BLOCKED`: 7cm 이내 전진 차단
- `STALE_RANGE`, `STALE_LIDAR`: 입력이 오래됨
- `DISABLED`: launch 파라미터로 비활성화됨

센서가 끊기면 기존 LiDAR Nav2는 유지하고 상태만 stale로 보고합니다. 이미
확정된 15cm/7cm 장애물은 센서가 잠시 끊겨도 마지막 전진 제한을 유지하지만,
로봇이 회피 이동을 마친 과거 지도 표시는 별도 수명 규칙으로 삭제합니다.

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
