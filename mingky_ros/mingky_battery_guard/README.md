# mingky_battery_guard

Mingky Care 배터리 감시 패키지. 저전압이면 부저로 알리고 충전소로 복귀시킨다.

**배터리가 기준치 이하로 떨어지면 → 부저 3번 → Nav2로 충전소 복귀.**

> **처음 보시나요?** 아래 순서로 읽으면 됩니다.
> 1. **ROS_DOMAIN_ID** (바로 아래) — 이거 안 맞으면 토픽이 하나도 안 보입니다
> 2. **실행** — 일단 돌려보기
> 3. **점검 기록** (맨 아래) — **코드를 고치기 전에 꼭 읽으세요.**
>    되돌리면 안 되는 부분과 그 이유가 적혀 있습니다

---

## ⚠️ 먼저 읽을 것 — ROS_DOMAIN_ID

이 패키지는 **도메인 번호를 설정하지 않습니다.** 기기마다 할당된 번호가 다르므로
각자 환경에 맞게 쓰세요.

```bash
echo $ROS_DOMAIN_ID          # 로봇과 PC가 같은 번호여야 통신됩니다
```

PC와 로봇의 번호가 다르면 토픽이 하나도 안 보입니다. 자기 기기에 할당된 번호를
확인하고 쓰세요. 로봇 쪽 번호는 `~/.bashrc`에 들어 있습니다.

---

## 무엇이 들어있나

| 실행 파일 | ROS 필요 | 하는 일 |
|---|---|---|
| `scripts/battery_beep_standalone.py` | ❌ | 배터리 직접 읽어서 부저만. **가장 간단** |
| `battery_guard` | ✅ | 토픽 구독 → 부저 + **Nav2 충전소 복귀** |
| `emergency_stop` | ✅ | 비상정지. 관제가 풀어줄 때까지 유지 |
| `fake_battery` | ✅ | 로봇 없이 로직 테스트용 |
| `scripts/motor_load.py` | ❌ | 테스트용. 모터를 돌려 전압을 일부러 떨어뜨림 |

처음이라면 **`battery_beep_standalone.py`부터** 쓰세요. ROS도, 도메인도, bringup도
필요 없어서 로봇에 올려놓고 실행하면 바로 됩니다.

---

## 요구사항

- Ubuntu 24.04 / ROS 2 Jazzy
- 로봇: `pinkylib`, `/home/pinky/ap/battery_buzzer.py` (핑키 기본 이미지에 포함)
- Nav2 복귀 기능을 쓰려면: Nav2 실행 중 + 맵 로드됨

---

## 설치

```bash
cd ~/mingky_care_pro
colcon build --symlink-install --base-paths pinky mingky_ros \
  --packages-up-to mingky_battery_guard
source install/setup.bash
```

---

## 실행

### 1. 부저만 (ROS 없이) — 로봇에서

```bash
python3 scripts/battery_beep_standalone.py
```

배터리를 10초마다 직접 읽어서 40% 이하가 되면 부저 3번 울리고 종료합니다.
설정은 파일 맨 위 상수를 고치면 됩니다 (`THRESHOLD`, `POLL_SEC`, `CONFIRM`).

### 2. 부저만 (ROS) — 로봇에서

```bash
ros2 run mingky_battery_guard battery_guard --ros-args -p use_nav2:=false
```

`/battery/voltage` (또는 `/battery/percent`) 토픽이 필요하므로 로봇에서
bringup(또는 `battery_publisher`)이 돌고 있어야 합니다.

```bash
ros2 run pinky_bringup battery_publisher
```

### 3. 부저 + 충전소 복귀 — 로봇에서

```bash
ros2 launch mingky_battery_guard battery_guard.launch.xml \
  threshold_percent:=40.0 \
  dock_x:=1.75 dock_y:=-0.50 dock_yaw:=0.0
```

> **부저는 로봇의 GPIO를 쓰므로 이 노드는 로봇 위에서 실행해야 합니다.**
> PC에서 복귀 기능만 쓰려면 `use_buzzer:=false`.

---

## 파라미터

| 이름 | 기본값 | 설명 |
|---|---|---|
| `threshold_percent` | `40.0` | 이 % 이하면 발동 |
| `confirm_count` | `3` | 연속 몇 번 낮아야 인정할지 |
| `dock_x` / `dock_y` / `dock_yaw` | `0.0` | **충전소 좌표. 반드시 실제 값으로 바꿀 것** |
| `dock_frame` | `map` | 좌표계 |
| `use_nav2` | `true` | false면 부저만 |
| `use_buzzer` | `true` | PC에서 돌릴 땐 false |
| `buzzer_script` | `/home/pinky/ap/battery_buzzer.py` | 부저 스크립트 경로 |
| `buzzer_level` | `danger` | `danger`=784Hz 3번, `warn`=550Hz 2번 |
| `rearm_percent` | `60.0` | 이 % 이상 충전되면 다시 감시 시작 |
| `trend_samples` | `5` | 충전 여부를 볼 표본 수 |
| `trend_rise` | `2.0` | 최근 절반이 예전 절반보다 이만큼(%p) 높아야 충전 중 |
| `median_samples` | `3` | 중앙값 필터 창. `1`이면 끔 |

---

## 구독 토픽 — 전압이 1차 소스입니다

| 토픽 | 역할 |
|---|---|
| `battery/voltage` | **1차**. 이걸 받으면 전압으로 직접 판단 |
| `battery/percent` | **예비**. 전압을 한 번도 못 받았을 때만 사용 |

퍼센트는 파생값입니다.

```python
percent = (V - 6.8) / (7.6 - 6.8) * 100
```

0.8V를 100칸에 매핑해서 **1% = 8mV**이고, 6.8V 아래·7.6V 위는 전부 0%/100%로
뭉개집니다. 6.77V와 6.5V가 똑같이 "0%"로 보여서 얼마나 위험한지 알 수 없습니다.

그리고 퍼센트를 만들어주는 노드가 죽어도 **전압만으로 감시가 계속됩니다.**
(DB의 `robot_battery_log.battery_percent`가 NULL을 허용하는 것과 같은 이유입니다.)

---

## 발행하는 이벤트

팀 표준대로 **`/events` 토픽에 `mingky_interfaces/msg/Event`** 로 발행합니다.
게이트웨이(`mingky_event_gateway`)가 받아 관제 서버로 넘깁니다.

`event_code` 는 `config/event_codes.yaml` 에 **등록된 것만** 씁니다. 발행 전에
`EventPublisher` 가 검증하고, 수집 서버가 한 번 더 검증합니다.

| 상황 | `event_code` | `payload` |
|---|---|---|
| 기준치 도달 | `robot.battery_low` | `{percent: int}` |
| 충전소로 출발 | `nav.goal_sent` | `{visit_name: "charging_dock"}` |
| 충전소 도착 | `nav.goal_succeeded` | `{visit_name: "charging_dock"}` |
| 복귀 실패 | `nav.goal_aborted` | `{visit_name: "charging_dock", error_code: int}` |
| 비상정지 | `robot.paused` | `{reason: "operator"\|"obstacle"}` |

> 복귀 실패 전용 코드가 `event_codes.yaml` 에 없어서 `nav.goal_aborted` 를
> 재사용합니다. 실제로 복귀 목표가 중단된 것이라 의미도 맞습니다. 충전소로 간
> 것임은 `visit_name` 으로 구분합니다. 전용 코드가 필요하면 **yaml 부터** 고치세요.

확인:

```bash
ros2 topic echo /events
```

전압은 이벤트에 싣지 않습니다. `robot.battery_low` 의 payload 형태를
`event_codes.yaml` 이 `{percent: int}` 로 정하고 있기 때문입니다.
판단에 쓴 전압과 원본은 **노드 로그**에 남습니다.

### `guide_manager` 와의 관계

`mingky_guide_manager` 도 `robot.battery_low` 를 발행하는 코드를 갖고 있습니다.
다만 그쪽은 `/batt_state`(`pinky_sensor_adc`)를 구독하는데 **그 노드를 띄우는
launch 파일이 저장소에 없어** 현재는 동작하지 않습니다.

임계값도 계층이 다릅니다.

| | 임계 | 성격 |
|---|---|---|
| `guide_manager` | 6.9V (12.5%) | 방전 직전 **안전선**. 로봇 기본 부저와 같은 값 |
| 이 패키지 | 40% (7.12V) | 여유 있게 복귀시키는 **운영선** |

둘 다 살아나면 같은 코드가 두 번 나갈 수 있습니다. 어느 쪽이 이 이벤트를
담당할지는 팀에서 정해야 합니다. 이 PR 은 `guide_manager` 를 건드리지 않습니다.

---

## 충전소 좌표 찾는 법

Nav2를 띄우고 로봇을 충전소에 놓은 뒤:

```bash
ros2 topic echo /amcl_pose --field pose.pose.position --once
```

나온 `x`, `y`를 `dock_x`, `dock_y`에 넣으세요.

---

## 로봇 없이 테스트

### ① 자동 — 이걸 먼저 돌리세요

```bash
colcon test --base-paths pinky mingky_ros --packages-select mingky_battery_guard
colcon test-result --verbose
```

```
Summary: 14 tests, 0 errors, 0 failures, 0 skipped
```

`test/test_battery_guard.py` 가 **발동 · 중복방지 · 재무장 · 중앙값 필터 ·
충전 오탐**을 전부 검사합니다. ROS 토픽 없이 콜백에 직접 값을 넣으므로
로봇도 Nav2도 필요 없습니다. **코드를 고쳤으면 이것부터 돌리세요.**

### ② 눈으로 확인 — 실제 토픽까지 태워서

터미널 1:
```bash
ros2 run mingky_battery_guard battery_guard --ros-args -p use_buzzer:=false -p use_nav2:=false
```

터미널 2:
```bash
ros2 run mingky_battery_guard fake_battery
```

가짜 배터리 값을 순서대로 흘려보내서 **발동 → 중복 방지 → 재무장 → 재발동**을
한 번에 확인합니다. 경보가 정확히 2회면 통과이고, 마지막에 결과가 찍힙니다.

```
[발행  9]  35.0% (7.080V)
  >>> [경보 수신] robot.battery_low {"percent":36}
...
===== 결과 =====
경보 2회 (예상 2회) -> 통과
```

> `fake_battery`가 만드는 가짜 전압은 **반드시 아래 변환식과 같아야 합니다.**
> 다르면 guard가 되돌려 계산한 퍼센트가 어긋나서, 통과처럼 보이면서 실제로는
> 엉뚱한 지점에서 발동합니다. (실제로 이 사고가 한 번 났습니다 — 아래 점검 기록 참조)

---

## 알아둘 것 — 퍼센트는 매우 민감합니다

`pinkylib/battery.py`의 계산식:

```python
percent = (V - 6.8) / (7.6 - 6.8) * 100     # 6.8V=0%, 7.6V=100%
```

전체 범위가 **0.8V뿐**이라 **1% = 8mV**입니다.
모터가 돌 때 전압이 0.1V만 처져도 **12%p가 순식간에 떨어져 보입니다.**

그래서 `confirm_count`로 연속 확인을 합니다. 기본값이 `3`이고, 주행이 잦으면
더 올려도 됩니다.

같은 이유로 **충전 감지도 처음/끝만 비교하면 안 됩니다.** 모터가 멈추며 전압이
원래대로 회복되는 것만으로 "오르는 중"이 되어 충전으로 오인하고, 그러면 저전압
경보가 영영 나가지 않습니다. 그래서 `trend_samples`개 표본 중 **최근 절반의
최솟값이 예전 절반의 최댓값보다 `trend_rise`(%p) 이상 높을 때만** 충전으로
봅니다. 회복은 기준선으로 돌아올 뿐 기준선을 넘지 못하므로 걸러집니다.

| 퍼센트 | 전압 |
|---|---|
| 100% | 7.60V |
| 40% | 7.12V |
| 12.5% | 6.90V |
| 0% | 6.80V |

### 값이 튀는 이유 — 보내주는 쪽에 필터가 없습니다

값을 만들어 보내주는 `pinky_bringup/battery_publisher.py` 는 5초마다 센서를
**딱 한 번** 읽어서 **그대로 발행합니다.** 평균도 중앙값도 없어서, 그 한 번이
튀면 튄 값이 그대로 넘어옵니다. 게다가 전압과 퍼센트가 별개 타이머라 같은
순간의 값도 아닙니다.

```python
self.percentage_timer = self.create_timer(5.0, self.percentage_callback)
self.voltage_timer    = self.create_timer(5.0, self.voltage_callback)   # 별개 타이머
```

여기에 위의 **1% = 8mV** 가 곱해지므로 작은 튐도 퍼센트로는 크게 보입니다.
그래서 이 패키지가 `median_samples` 로 한 번 거릅니다. 평균과 달리 중앙값은
튄 값 하나에 끌려가지 않고 그냥 버립니다.
**대가는 판단이 한 샘플(약 5초) 늦어지는 것**입니다.

걸러낸 흔적은 로그에 남습니다.

```
[INFO] 배터리 41.2% (7.130V) [원본 6.980V 걸러냄]
```

이 줄에서 두 값이 크게 벌어져 있으면 **측정이 튀고 있다는 신호**입니다.

### 기존 안전 부저와의 관계

로봇에는 `battery-buzzer.service`가 이미 상시 동작 중이며 **6.9V(12.5%)** 에서
울립니다. 이건 방전 직전 안전장치이고, 이 패키지의 40%는 여유 있게 미리
복귀시키는 운영 로직이라 **서로 충돌하지 않습니다.**

부저 GPIO를 직접 잡지 않고 기존 `battery_buzzer.py`를 `beep <level>` 인자로
호출하는 이유도 이것입니다. 그 스크립트는 짧게 울리고 GPIO를 반납하며,
이미 사용 중이면 종료코드 3으로 비켜줍니다.

---

## 비상정지 (`emergency_stop`)

```bash
ros2 run mingky_battery_guard emergency_stop
```

**입구가 둘이고 멈추는 방식이 다릅니다.**

| 토픽 | 방식 | 쓰는 곳 |
|---|---|---|
| `/emergency_stop` (`Bool=true`) | `decel_time` 동안 **부드럽게 감속** | 관제 빨간 버튼 |
| `/emergency_stop/obstacle` (`Bool=true`) | **급정지** (즉시 0) | 장애물 감지 |

사람이 누른 정지는 서서히 세우는 편이 안전합니다 (짐이 쏠리거나 로봇이 기울 수
있음). 반면 앞에 장애물이 있으면 감속할 여유가 없으므로 즉시 0을 때립니다.
감속 중에 장애물이 감지되면 급정지로 전환됩니다.

**해제는 서비스 호출로만 됩니다.**

```bash
ros2 service call /emergency_stop/release std_srvs/srv/Trigger
```

`Bool=false` 를 보내도 풀리지 않습니다. 조건이 사라졌다고 저절로 풀리면, 원인을
확인하기 전에 로봇이 다시 움직여서 위험하기 때문입니다.

### 왜 0을 계속 발행하는가

`pinky_bringup` 의 `twist_callback` 은 메시지를 받는 즉시 모터 RPM을 세팅하고
**워치독이 없습니다.** 즉 "명령을 끊는 것"으로는 안 멈추고 **마지막 속도로 계속
갑니다.** 게다가 Nav2의 `velocity_smoother` 가 `/cmd_vel` 로 20Hz로 쏘고 있으므로,
목표를 취소해 발행자를 없애고 그 위에 0을 덮어써야 확실히 섭니다. 그래서
`publish_rate` 기본값이 **50Hz** 입니다 (Nav2보다 빨라야 함).

### 파라미터

| 이름 | 기본값 | 설명 |
|---|---|---|
| `decel_time` | `1.0` | 0까지 줄이는 데 걸리는 초 (급정지는 무시) |
| `publish_rate` | `50.0` | 0 발행 주기. **Nav2(20Hz)보다 빨라야 함** |
| `blink_period` | `0.5` | LED 깜빡임 주기 |
| `use_led` | `true` | 빨강 깜빡임 |
| `cancel_nav2` | `true` | 정지 시 Nav2 목표 취소 |

### 발행 토픽

| 토픽 | 형식 | 내용 |
|---|---|---|
| `/emergency_stop/state` | `Bool`, latched | 관제 UI가 바로 묶어 쓸 단순 상태 |
| `/emergency_stop/event` | `String`, latched | `events` 테이블에 넣을 JSON |

```json
{"event": "EMERGENCY_STOP", "engaged": true, "reason": "obstacle"}
```

`reason` 은 `operator` / `obstacle` / 해제됐으면 `null` 입니다.

---


## 점검 기록 (2026-08-06)

전체 점검에서 13건을 고쳤습니다. **"동작은 하는데 결정적인 순간에 안 하는"**
종류가 셋 있었고, 로그만 봐서는 정상으로 보이던 것들입니다.
아래 세 가지는 왜 이렇게 짰는지 알아야 다시 망가뜨리지 않는 부분이라 남깁니다.

### ① 모터가 멈추는 것을 "충전 중"으로 착각했음

충전 감지 하드웨어가 없어서 전압 추세로 짐작하는데, 옛 코드는
**첫 값과 끝 값만** 비교했습니다.

```python
return (self.history[-1] - self.history[0]) >= self.trend_rise   # 옛 방식
```

모터가 멈추며 전압이 원래대로 회복되는 것이 "오르는 중"으로 읽혔습니다.
충전 중으로 판단하면 경보를 건너뛰므로, **주행 중에는 배터리가 아무리 낮아도
경보가 나가지 않았습니다.** 이 패키지의 존재 이유가 무너지는 결함입니다.

```python
return (min(samples[-n:]) - max(samples[:n])) >= self.trend_rise  # 새 방식
```

최근 표본 **전부**가 예전 표본 **전부**보다 높을 것을 요구합니다. 회복은
기준선으로 돌아올 뿐 넘지 못하므로 걸러지고, 진짜 충전은 계단처럼 올라가므로
통과합니다. `trend_rise` 도 1.0 → 2.0%p 로 올렸습니다.

> ⚠️ **이 판정식을 "간단하게" 되돌리지 마세요.** 처음/끝 비교로 돌아가는 순간
> 위 결함이 그대로 재발합니다. `test_recovery_after_load_is_not_mistaken_for_charging`
> 이 이 상황을 고정하고 있습니다.

### ② 완충해도 감시가 다시 켜지지 않았음

재무장 코드가 `if charging:` **블록 안에** 있었습니다. 충전이 끝나면 전압이
평평해져 `is_charging()` 이 False 가 되므로, 그 안으로 다시 들어올 수 없었습니다.
**배터리가 100%여도 영원히 무장 해제** 상태로 남습니다.

지금은 충전 감지와 **무관하게** `pct >= rearm_percent` 면 재무장합니다.
(`test_rearms_even_when_charging_has_finished`)

### ③ "가끔 배터리가 낮게 뜨는" 현상 — 필터가 없었음

원인은 이 패키지가 아니라 **값을 보내주는 쪽**이었습니다.
`pinky_bringup/battery_publisher.py` 는 5초마다 센서를 한 번 읽어 **그대로**
발행합니다. 평균도 중앙값도 없습니다.
자세한 내용은 위 **"값이 튀는 이유 — 보내주는 쪽에 필터가 없습니다"** 절 참조.

→ `median_samples`(기본 3) 중앙값 필터로 막습니다. **대가는 판단이 한
샘플(약 5초) 늦어지는 것**이고, 이 지연은 `test_median_filter_costs_one_sample_of_delay`
로 못 박아 두었습니다. 필요 없으면 `median_samples:=1` 로 끌 수 있습니다.

### 나머지 10건

| 문제 | 조치 |
|---|---|
| `fake_battery` 가 가짜 전압을 **비례식**으로 만들어 검증이 거짓 통과 | 역함수로 교체 + 합격 판정 추가 |
| `fake_battery` 가 발행자 없는 `battery_guard/state` 구독 | 제거 |
| Nav2 복귀 실패 시 로그만 남고 관제가 모름 | `nav.goal_aborted` 발행 + 도착까지 확인 |
| `emergency_stop` 이 타이머를 cancel 만 하고 destroy 안 함 | `destroy_timer()` 추가 |
| README 에만 있던 `dock_frame`, `buzzer_level` (넘기면 노드가 안 뜸) | 실제 파라미터로 추가 |
| `confirm_count` 기본값이 코드 3 / launch 2 / README 2 로 불일치 | 3 으로 통일 |
| launch 가 파라미터 절반만 노출 | 전체 노출 |
| `battery_beep` 이 `battery_guard` 의 옛 부분집합 (중복) | 제거, `use_nav2:=false` 로 안내 |
| `ament_flake8` import 순서 위반으로 `colcon test` 실패 | 정리 (0 errors) |

---

## 라이선스

Apache-2.0
