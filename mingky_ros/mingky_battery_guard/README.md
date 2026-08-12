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
| `low_voltage` | `7.12` | 저전압 진입 기준(V) |
| `confirm_count` | `3` | 창 안에서 저전압이어야 하는 횟수 |
| `confirm_window` | `6` | 그 창의 크기 (최근 몇 표본) |
| `critical_voltage` | `6.80` | 이 전압 이하는 확인 없이 즉시 발동 |
| `critical_count` | `2` | 위험선을 몇 번 봐야 인정하나 |
| `median_samples` | `3` | 중앙값 필터 크기 |
| `rearm_voltage` | `7.28` | 저전압 해제 기준(V) |
| `trend_samples` | `5` | 충전 추세 표본 수 |
| `trend_rise_volt` | `0.05` | 충전으로 보는 상승폭(V) |
| `use_buzzer` | `true` | 로봇 부저 사용 여부 |

### 임계값은 퍼센트가 아니라 전압입니다

퍼센트는 전압의 선형 변환(`(V-6.8)/0.8*100`)이라 6.8~7.6V 안에서는 전압
판정과 결과가 같습니다. 문제는 **그 밖을 전부 0%/100% 로 뭉갠다**는 것입니다.
상수가 되면 차이가 사라지고, 차이로 만든 판정이 모두 죽습니다.

- **충전 감지**: 7.9→8.3V 상승이 `100→100` 으로 들어와 영영 "충전 아님"
- **위험 판정**: 6.7V 와 6.3V 가 똑같이 `0%`

그래서 판정은 전압으로 하고, 퍼센트는 화면·DB 표시용으로만 씁니다. 나중에
실측 방전 곡선으로 변환식을 고쳐도 현장에서 맞춰 둔 임계값이 흔들리지
않는다는 이점도 따라옵니다.

> `trend_rise_volt` 를 0.016V(구 `2.0%p` 의 등가) 수준으로 낮추지 마십시오.
> ADC 1LSB 가 약 2mV 라 노이즈와 정체 구간까지 충전으로 읽혀 경보가 막힙니다.

### 판정은 '연속'이 아니라 '창 안의 횟수'입니다

방전이 진행된 배터리는 **주행하면 처지고 멈추면 회복**하기를 반복합니다.
연속 카운터를 쓰면 기준치 위를 한 번 볼 때마다 0 으로 초기화되어, 7.36V ↔
6.86V 를 왕복하는 동안 경보가 **한 번도 나가지 않습니다.** 그래서 최근
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
- `/emergency_stop/communication` (`Bool=true`): 장기 관제 통신 두절 정지
- `/emergency_stop/release` (`Trigger`): 명시적 해제
- `/emergency_stop/state` (`Bool`, latched): 현재 정지 상태
- `/emergency_stop/reason` (`String`, latched): `operator` 또는 `obstacle`

비상정지는 즉시 0 속도를 발행하고 Nav2 목표를 취소합니다. 상태는 기본적으로
`~/.mingky/emergency_stop.state`에 저장되므로 프로세스 재시작으로 풀리지
않습니다. 해제 뒤 취소된 안내 목표는 자동 재개하지 않습니다.

안전 게이트는 입력이 `command_timeout`(기본 0.5초) 동안 끊겨도 0을 발행합니다.
또한 `pinky_bringup` 모터 드라이버에도 같은 워치독이 있어 게이트 프로세스 자체가
죽은 경우 마지막 RPM을 계속 유지하지 않습니다.

## 실기 검증 — 발행자가 하나인지 먼저 확인하세요

`battery_publisher`는 I2C 장치 `0x08`을 직접 읽습니다. **이 노드가 두 개 뜨면
전압이 실제보다 낮게 측정되고, 그 값으로 저전압 경보가 나갑니다.**

`0x08`은 "커맨드 바이트로 채널 선택 → 변환 → 2바이트 읽기" 구조인데 선택된
채널을 하나만 기억합니다. write와 read 사이에 다른 프로세스가 채널을 바꾸면
엉뚱한 채널 값을 배터리 값으로 읽습니다. **통신은 성공하고 예외도 나지 않아
호출한 쪽에서 알아챌 방법이 없습니다.**

실측(2026-08-12, pinky2, 각 40표본):

| 조건 | 중앙값 | 범위 | 이상치 |
|---|---|---|---|
| 리더 1개 | 8.168V | 37mV | 0/40 |
| 리더 2개 | **7.771V** | **3.22V** | 26/40 |
| 되돌린 뒤 | 8.166V | 26mV | 0/40 |

되돌렸을 때 2mV 오차로 복귀했습니다. 오염은 값을 **낮추는 방향으로만**
작용하며, 중앙값 자체가 이동하므로 필터로는 걸러지지 않습니다.

### 검증 전 확인

```bash
ros2 topic info /battery/voltage
# Publisher count: 1  이어야 합니다
```

`fake_battery`로 시나리오를 넣을 때는 실측 발행자를 반드시 내리세요.

```bash
sudo systemctl stop mingky-battery-pub
ros2 topic info /battery/voltage      # Publisher count: 0
```

끝나면 되돌립니다. 관제는 배터리 값이 없으면 로봇 선택을 막습니다.

```bash
sudo systemctl start mingky-battery-pub
```

### 중복이 생기는 경로

```
mingky-battery-pub.service                      상시
pinky_bringup/launch/bringup_robot.launch.xml   주행 스택
```

저장소의 `bringup_robot.launch.xml`에는 `start_battery_publisher` 스위치가
있고 `mingky_system.launch.xml`이 `false`로 넘깁니다. **상류(pinky_pro) 원본
런치에는 이 스위치가 없어 조건 없이 띄웁니다.** 원본 런치를 직접 쓰면 중복이
생깁니다.

`battery_publisher`는 기동 3초 뒤 다른 발행자를 확인하고, 있으면 오류를
남기고 종료합니다. 이 동작은 `exit_on_duplicate:=false`로 끌 수 있습니다.

## 손으로 돌리는 스크립트 (`scripts/`)

launch 그래프 밖에서 직접 실행하는 도구입니다. **로봇 위에서** 돌립니다.

| 스크립트 | 하는 일 |
|---|---|
| `battery_beep_standalone.py` | 기준 전압 이하면 부저 3번 울리고 종료 |
| `motor_load.py` | 모터에 부하를 걸어 전압 강하를 재현 |

```bash
source ~/mingky_care_pro/install/local_setup.bash
python3 scripts/battery_beep_standalone.py
```

### 전압은 토픽에서 받습니다 — I2C 를 직접 열지 않습니다

예전에는 `pinkylib.Battery` 로 I2C 를 직접 읽었습니다. **그러면 상시 실행되는
`adc_reader` 와 리더가 둘이 되어 양쪽 값이 함께 망가집니다.** 실측으로 중앙값이
`0.397V` 낮아지고 산포가 `37mV → 3.22V` 로 벌어졌습니다.

`adc_reader` 는 `flock` 으로 자기 임계 구역을 지키지만 `pinkylib` 은 잠금을
쓰지 않아 맞물리지 않습니다. 그래서 잠금을 흉내 내는 대신 **`battery/voltage`
를 구독**합니다 (`battery_source.VoltageSource`).

> 그래서 이 스크립트들은 **ROS 환경이 필요합니다.** `source` 를 먼저 하세요.
> `motor_load.py` 는 `pinkylib.Motor` 를 그대로 씁니다. 모터는 다른 장치라
> 충돌하지 않습니다.

**멈춘 값은 읽기 실패보다 위험합니다.** 발행이 끊겨도 마지막 값은 변수에 남아
정상처럼 보이기 때문입니다. `stale_after_sec`(기본 16초, 발행 주기 5초의 3배)
이 지난 값은 없는 것으로 봅니다.

값이 안 오면 스크립트가 원인을 안내합니다 — 발행자가 0개인지, 2개 이상인지에
따라 다른 메시지가 나옵니다.

## 테스트

ROS 2 Jazzy 환경에서 실행합니다.

```bash
colcon test --base-paths pinky mingky_ros \
  --packages-select mingky_battery_guard mingky_guide_manager pinky_bringup
colcon test-result --verbose
```

실기 전에는 `ros2 topic info /cmd_vel --verbose`에서 발행자가 안전 게이트 하나인지
확인하고, 바퀴를 띄운 상태에서 비상정지와 프로세스 종료 워치독을 시험하세요.
