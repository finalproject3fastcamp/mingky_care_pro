# mingky_low_obstacle

전방 초음파가 감지하지만 2D LiDAR 평면에는 보이지 않는 낮은 물체를 Nav2의
로컬 회피에 보태는 ROS 2 패키지입니다. 초음파만으로 물체의 정확한 좌우 위치를
만들지 않고, 센서가 실제로 제공하는 부채꼴 범위만 표현합니다.

## 처리 흐름

1. `/us_sensor/range`를 median 3개로 정리합니다.
2. 초음파 부채꼴과 겹치는 `/scan` 점을 TF 기준으로 비교합니다.
3. `초음파는 가깝고 LiDAR는 멀다`는 증거가 최근 5개 중 3개일 때만 낮은
   장애물로 확정합니다.
4. 확정된 `sensor_msgs/Range`를 `/low_obstacle/range`로 발행합니다.
5. Nav2 local costmap의 `RangeSensorLayer`와 MPPI가 일반 장애물처럼 회피합니다.
6. 15cm 이내에서는 전진 속도만 0.08m/s로 제한하고, 7cm 이내가 반복되면
   전진만 막습니다. 회전과 후퇴 명령은 변경하지 않습니다.

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
확정된 15cm/7cm 장애물은 센서가 잠시 끊겨도 마지막 전진 제한을 유지합니다.

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
