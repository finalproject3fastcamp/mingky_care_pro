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
`battery/percent`를 사용합니다. 중앙값 필터와 표본 확인으로 모터 부하에 따른
순간 전압 강하를 걸러냅니다.

| 파라미터 | 기본값 | 설명 |
|---|---:|---|
| `threshold_percent` | `40.0` | 저전압 진입 기준 |
| `confirm_count` | `3` | 창 안에서 저전압이어야 하는 횟수 |
| `confirm_window` | `6` | 그 창의 크기 (최근 몇 표본) |
| `critical_voltage` | `6.80` | 이 전압 이하는 확인 없이 즉시 발동 |
| `median_samples` | `3` | 중앙값 필터 크기 |
| `rearm_percent` | `60.0` | 저전압 해제 기준 |
| `trend_samples` | `5` | 충전 추세 표본 수 |
| `trend_rise` | `2.0` | 충전으로 보는 상승폭(%p) |
| `use_buzzer` | `true` | 로봇 부저 사용 여부 |

### 판정은 '연속'이 아니라 '창 안의 횟수'입니다

방전이 진행된 배터리는 **주행하면 처지고 멈추면 회복**하기를 반복합니다.
연속 카운터를 쓰면 기준치 위를 한 번 볼 때마다 0 으로 초기화되어, 70% ↔ 8%
를 왕복하는 동안 경보가 **한 번도 나가지 않습니다.** 그래서 최근
`confirm_window` 개 표본 중 `confirm_count` 개가 낮으면 발동합니다.

같은 이유로 **해제도 표본 하나로는 되지 않습니다.** 창이 전부 기준치 위여야
풀립니다. 회복 하나로 풀어주면 발동과 해제를 반복하며 관제에 이벤트 폭풍을
만들고 충전소 복귀도 계속 취소·재시도됩니다.

### 위험선은 확인을 기다리지 않습니다

`critical_voltage`(기본 6.80V)는 로봇 기본 부저(`battery-buzzer.service`)의
danger 선과 같은 값입니다. 여기까지 내려간 전압은 충전 중이든 표본이
부족하든 **즉시** 발동합니다. 단발 ADC 오류로 오작동하지 않도록 중앙값 필터
창 안에서 2회 이상일 때만 인정합니다.

판정에는 중앙값을 쓰지만 **최저 전압은 `battery/voltage_min`(`Float32`,
latched)으로 따로 발행**합니다. 중앙값은 최저값을 버리므로, 실제로는 6.75V
까지 처져 기본 부저가 울리는데 관제 화면은 31% 로 보이는 일이 있었습니다.

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
