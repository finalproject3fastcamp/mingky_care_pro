# mingky_teleop

사람이 로봇을 모는 경로의 안전장치와, 주행 제어권을 정하는 모드 관리입니다.

```
/mode/set → mode_manager ─┬→ /mode              요청 모드 (주기 재발행)
                           ├→ /emergency_stop    안전 게이트를 건다
                           ├→ /emergency_stop/release (서비스) 해제
                           └→ /events            클라우드 타임라인

Foxglove Teleop → cmd_vel_teleop_raw
                   → teleop_limiter (모드 확인 → 상한) ─┬→ cmd_vel_teleop
                                                        └→ /teleop_limiter/applied_mode
                   → twist_mux (우선순위 100) → cmd_vel → 모터
```

## 모드

| 모드 | `/cmd_vel` 의 주인 | 전환 |
| --- | --- | --- |
| `auto` | Nav2 | 기본값 |
| `manual` | 텔레옵 | 명시적으로 켜야 한다 |
| `estop` | 아무도 아님 (정지) | 걸어 잠긴다. 명시적으로 풀어야 한다 |

```bash
ros2 topic pub --once /mode/set std_msgs/msg/String "{data: 'manual'}"
ros2 topic echo /mode        # 현재 모드
ros2 topic echo /teleop_limiter/applied_mode  # limiter 가 실제 적용한 모드
```

관제에서는 하향 명령으로 바꿉니다.

```bash
curl -X POST https://mingkycarepro.site/api/robots/pinky-01/orders \
  -H "Content-Type: application/json" -d '{"command":"set_mode","argument":"manual"}'
```

### 왜 서버가 아니라 로봇이 모드를 갖고 있나

arming(`backend/app/arming.py`)은 백엔드가 소유하고 로봇이 폴링합니다. 몇 초
끊겨도 QR 을 안 읽을 뿐이라 그래도 됩니다. **모드는 다릅니다.** 통신이 끊긴
동안에도 로봇은 지금 누구 명령을 들어야 하는지 알아야 하고, estop 이 걸렸다면
연결과 무관하게 계속 걸려 있어야 합니다. 서버가 소유하면 **두절이 곧 안전
상태의 소실**이 됩니다.

그래서 서버는 요청만 하고(`set_mode`), 판단과 보관은 로봇이 합니다.
서버는 `robot.mode_changed` 이벤트로 요청 모드를 보고, 실시간 조작 연결에서는
limiter 적용 상태를 따로 확인합니다. 두 값이 일정 시간 다르면
`robot.mode_mismatch`, 다시 같아지면 `robot.mode_recovered`가 남습니다.

`/mode`와 적용 상태는 모두 반복 발행합니다. 노드가 재시작되거나 DDS 메시지 한
번을 놓쳐도 다음 주기에 자동으로 복구됩니다. 관제 조작 패드는 최근 limiter
상태가 `manual`이라고 확인된 경우에만 열립니다.

### estop 은 안전 게이트에 위임합니다

`mingky_battery_guard` 의 `emergency_stop` 이 이미 단일 게이트입니다. 정지
상태를 **파일에 남겨 프로세스가 재시작돼도 유지**하고, LED 점멸과 Nav2 목표
취소까지 합니다. 여기서 따로 만들면 정지 경로가 둘이 되고, 둘 중 약한 쪽이
먼저 풀립니다.

이 노드는 **모드라는 개념만 소유**하고 정지는 게이트에 맡깁니다.
`emergency_stop/state` 가 정본이며, `/mode` 는 그것을 모드 어휘로 옮긴
표현입니다. 저전압 복귀나 장애물로 게이트가 걸려도 모드가 따라갑니다.


## 실행

```bash
ros2 launch mingky_bringup teleop.launch.py
ros2 launch mingky_bringup teleop.launch.py max_linear:=0.1 max_angular:=0.4
```

**이 launch 를 띄우지 않으면 텔레옵이 동작하지 않습니다.** `cmd_vel_teleop` 에
발행자가 없어 twist_mux 가 그 입력을 후보로 올리지 않습니다. 조작을 쓰려는
사람이 명시적으로 켜는 구조를 의도한 것입니다. 늘 켜 두면 같은 망에 있는
누구나 언제든 로봇을 몰 수 있습니다.

twist_mux 는 따로 띄웁니다. 중재기는 자율주행에도 필요해 수명이 다릅니다.

## 왜 로봇 쪽에서 자르나

Foxglove Teleop 패널에도 속도 설정이 있지만 **조작자가 언제든 바꿀 수 있습니다.**
로봇에서 한 번 더 자르지 않으면 상한이 아니라 권고입니다.

원격 조작에는 왕복 지연이 있습니다. 클라우드 경유 실측이 260~340ms 라 누른 뒤
로봇이 반응하기까지 시간이 걸리고, 조작자는 반응이 없다고 느껴 더 밀어 넣기
쉽습니다. 속도가 낮으면 그 사이 이동 거리가 짧아 회복할 여지가 생깁니다.

| 파라미터 | 기본값 | 뜻 |
| --- | --- | --- |
| `max_linear` | `0.15` | 직진 속도 상한 [m/s] |
| `max_angular` | `0.6` | 회전 속도 상한 [rad/s] |

기본값은 보수적으로 잡은 잠정값입니다. 실측 지연과 현장 통로 폭을 보고 조정하세요.

## 멈추는 책임은 여기 없습니다

이 노드는 **크기만 자릅니다.** 명령이 끊겼을 때 로봇을 세우는 것은 두 겹입니다.

| 겹 | 하는 일 |
| --- | --- |
| twist_mux `timeout` | 조용해진 입력을 후보에서 뺀다 (1.0s) |
| `pinky_bringup` 워치독 | 명령이 끊기면 모터를 세운다 (1.0s) |

자르는 일과 세우는 일을 한 노드에 두면 이 노드가 죽었을 때 둘 다 사라집니다.

## 비상정지

걸어 잠깁니다. 명시적으로 풀기 전에는 유지되고, **로봇을 재부팅해도 걸린
채로 남습니다** (게이트가 파일에 기록).

```bash
ros2 topic pub --once /emergency_stop std_msgs/msg/Bool "{data: true}"
ros2 service call /emergency_stop/release std_srvs/srv/Trigger
```

텔레옵 중이라면 **조작을 놓는 것만으로도 멈춥니다.** 발행이 끊기면 워치독이
1초 안에 모터를 세웁니다.


## 검증

로봇을 움직이지 않고 토픽만으로 확인할 수 있습니다. 모터 드라이버(`pinky_bringup`)를
띄우지 않으면 `/cmd_vel` 에 구독자가 없어 로봇은 가만히 있습니다.

```bash
ros2 launch mingky_bringup twist_mux.launch.py
ros2 launch mingky_bringup teleop.launch.py

# 상한을 넘겨 보낸다
ros2 topic pub -r 10 /cmd_vel_teleop_raw geometry_msgs/msg/Twist "{linear: {x: 5.0}}"
ros2 topic echo /cmd_vel --field linear.x     # 0.15 로 잘려 나와야 한다
```
