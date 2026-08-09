# mingky_battery_guard

배터리 저전압을 판정하고 실제 모터 명령 앞에서 비상정지를 강제하는 ROS 2
패키지입니다. 상태 전이, 세션 종료, 충전소 복귀 이벤트는
`mingky_guide_manager`가 담당합니다.

## 운영 실행

실제 운행에서는 개별 노드 대신 통합 launch를 사용합니다.

```bash
ros2 launch mingky_bringup mingky_system.launch.xml \
  robot_id:=pinky-01 backend_url:=http://192.168.0.10:8000
```

이 launch는 Nav2 출력을 `cmd_vel_safety_input`으로 보내고, 안전 게이트만 실제
`cmd_vel`을 발행하게 합니다. `robot_id=pinky-01`은 기본적으로
`charging_station_1`, `pinky-02`는 `charging_station_2` waypoint를 사용합니다.
다른 위치가 필요하면 `charging_waypoint:=이름`으로 지정합니다.

배터리 판정만 시험할 때는 다음을 사용합니다.

```bash
ros2 launch mingky_battery_guard battery_guard.launch.xml use_buzzer:=false
```

## Battery Guard

`battery/voltage`를 우선 사용하고, 전압을 한 번도 받지 못했을 때만
`battery/percent`를 사용합니다. 중앙값 필터와 연속 표본 확인으로 모터 부하에
따른 순간 전압 강하를 걸러냅니다.

| 파라미터 | 기본값 | 설명 |
|---|---:|---|
| `threshold_percent` | `40.0` | 저전압 진입 기준 |
| `confirm_count` | `3` | 연속 저전압 확인 횟수 |
| `median_samples` | `3` | 중앙값 필터 크기 |
| `rearm_percent` | `60.0` | 저전압 해제 기준 |
| `trend_samples` | `5` | 충전 추세 표본 수 |
| `trend_rise` | `2.0` | 충전으로 보는 상승폭(%p) |
| `use_buzzer` | `true` | 로봇 부저 사용 여부 |

판정 결과는 latched `battery/low` (`Bool`)로 발행합니다. `true`를 받은 Guide
Manager는 활성 세션을 `session.ended(battery)`로 닫고 로봇별 충전 waypoint로
복귀합니다. 충전 복귀는 진료 도착과 분리된 `dock.return_*` 이벤트를 사용합니다.

## Emergency Stop / Safety Gate

`emergency_stop`은 다음 경로의 단일 출력 게이트입니다.

```text
Nav2 / teleop -> cmd_vel_safety_input -> emergency_stop -> cmd_vel -> motor
```

- `/emergency_stop` (`Bool=true`): 운영자 비상정지
- `/emergency_stop/obstacle` (`Bool=true`): 장애물 비상정지
- `/emergency_stop/release` (`Trigger`): 명시적 해제
- `/emergency_stop/state` (`Bool`, latched): 현재 정지 상태
- `/emergency_stop/reason` (`String`, latched): `operator` 또는 `obstacle`

비상정지는 즉시 0 속도를 발행하고 Nav2 목표를 취소합니다. 상태는 기본적으로
`~/.mingky/emergency_stop.state`에 저장되므로 프로세스 재시작으로 풀리지
않습니다. 해제 뒤 취소된 안내 목표는 자동 재개하지 않습니다.

안전 게이트는 입력이 `command_timeout`(기본 0.5초) 동안 끊겨도 0을 발행합니다.
또한 `pinky_bringup` 모터 드라이버에도 같은 워치독이 있어 게이트 프로세스 자체가
죽은 경우 마지막 RPM을 계속 유지하지 않습니다.

## 테스트

ROS 2 Jazzy 환경에서 실행합니다.

```bash
colcon test --base-paths pinky mingky_ros \
  --packages-select mingky_battery_guard mingky_guide_manager pinky_bringup
colcon test-result --verbose
```

실기 전에는 `ros2 topic info /cmd_vel --verbose`에서 발행자가 안전 게이트 하나인지
확인하고, 바퀴를 띄운 상태에서 비상정지와 프로세스 종료 워치독을 시험하세요.
