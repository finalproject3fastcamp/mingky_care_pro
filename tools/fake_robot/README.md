# 가짜 로봇 하네스

ROS 없이, 실기 없이 로봇을 흉내낸다. 로봇↔서버 인터페이스가 순수 HTTP 라서
가능하다 ([`monitoring-spec.md`](../../docs/monitoring-spec.md) §3.2 · §9.1).

의존성은 `PyYAML` 하나다. HTTP 는 stdlib `urllib` 을 쓴다 — 로봇 하나 흉내내려고
requirements 를 늘릴 이유가 없다.

## 쓰는 법

```bash
# 정본 대조만. 서버가 없어도 된다
python tools/fake_robot/fake_robot.py tools/fake_robot/scenarios/session_complete.yaml --check

# 실제 재생
python tools/fake_robot/fake_robot.py tools/fake_robot/scenarios/session_complete.yaml \
    --base-url http://localhost:8000
```

`--check` 는 CI 단위 잡에서도 돈다
([`backend/tests/test_fake_robot_scenarios.py`](../../backend/tests/test_fake_robot_scenarios.py)).

## 시나리오

| 파일 | 무엇을 재현하나 |
| --- | --- |
| `session_complete.yaml` | 정상 완주. arming 체인부터 충전소 복귀까지 |
| `session_with_intervention.yaml` | 완주했지만 사람이 손댄 세션. §1.1 판정이 갈리는 지점 |
| `type_mismatch.yaml` | 오배선 — 조제 스테이션이 주행 이벤트를 발행 |

```yaml
name: 세션 완주 (p001, 3단계)

robots:
  - id: pinky-01
    type: mobile
    battery_percent: 87
    voltage: 11.9        # 스키마가 0~12 로 제한한다

steps:
  - { robot: pinky-01, action: battery }
  - { robot: pinky-01, action: arm }
  - { robot: pinky-01, action: qr_scan, patient_id: p001, marker_id: 20 }
  - robot: pinky-01
    action: event
    code: nav.goal_succeeded
    payload: { visit_name: X-ray }
    wait: 0.5            # 이 스텝 전에 쉬는 시간
```

| action | 하는 일 |
| --- | --- |
| `battery` | `POST /robots/{id}/battery` |
| `arm` | `POST /robots/{id}/arm` |
| `qr_scan` | `POST /qr/scan`. 돌려받은 `session_id` 를 **이후 이벤트에 자동으로 단다** |
| `event` | `POST /events`. `level` 은 생략하면 정본에서 가져온다 |
| `order` | `POST /robots/{id}/orders`. `command` · `argument` · `actor` |

heartbeat 는 스텝이 아니라 백그라운드 스레드가 3초마다 계속 보낸다. 스텝으로
두면 매 시나리오가 heartbeat 로 뒤덮인다. 본문에는 `system_state` 를 싣는데,
기본값 `active` 가 아니면 서버가 제어 명령을 `robot system is …` 409 로 막는다.
로봇마다 `system_state: inactive` 로 덮어써서 그 거부 경로를 만들 수도 있다.

### `order` 는 로봇이 아니라 사람 쪽이다

```yaml
- robot: pinky-01
  action: order
  command: localize
  argument: run
  actor: 정민경        # 생략하면 X-Actor 없이 보낸다
```

하네스는 이미 `arm` 으로 대시보드 역할을 겸하고 있다 — 그것도 의료진이 누르는
버튼이다. 개입(§1.1)을 재현하려면 누르는 쪽이 필요하고, 그 호출을 테스트 코드에
흩어 놓으면 시나리오 파일만 봐서는 무슨 일이 벌어지는지 알 수 없다.

`actor` 를 생략하는 것은 실수가 아니라 검증 대상이다. 서버는 헤더가 없어도
거부하지 않고 익명으로 남겨야 한다 (`backend/app/actor.py`). 헤더 값은 UTF-8
바이트로 보낸다 — 브라우저와 같은 바이트라야 서버의 latin-1 복구 경로가 실제로
검증된다.

`command` 는 정본 대조 대상이 아니다. 이벤트 코드와 달리 명령 목록의 정본은
`backend/app/schemas.py` 의 `OrderIn` 인데, 하네스는 백엔드를 import 하지 않는다.
오타는 서버가 422 로 잡는다.

### 순서를 지켜야 하는 이유

arming 전제조건이 체인이다 (`backend/app/routers/robots.py`).

```
heartbeat 수신 → link_state 가 unknown 을 벗어남
  → 5분 이내 배터리 표본 ≥ 40%
  → POST /robots/{id}/arm
  → POST /qr/scan  (armed 가 아니면 거부)
```

단위 테스트는 전부 가짜 커넥션이라 이 순서를 한 번도 통과시켜본 적이 없다.
하네스가 검증하는 것이 정확히 이 배관이다.

## 정본 준수 검사

시나리오의 이벤트 코드를 [`config/event_codes.yaml`](../../config/event_codes.yaml)
과 대조한다.

- 정본에 있는 코드인가
- `level` 이 정본과 같은가
- **그 로봇 타입이 낼 수 있는 코드인가** (`robot_types`)
- 스텝이 참조하는 로봇이 `robots` 에 선언돼 있는가

문제가 있으면 첫 번째에서 멈추지 않고 전부 모아 출력한 뒤 종료 코드 1 을 낸다.

정본이 바뀌면 가짜 로봇이 먼저 깨진다. 그래서 하네스가 정본 준수 검사 역할을
겸한다.

일부러 오배선을 만드는 시나리오는 스텝에 `expect_mismatch: true` 를 달면 타입
검사만 건너뛴다. 코드 존재와 `level` 은 그대로 본다 — 오배선 시나리오라고 아무
코드나 쓸 수 있는 것은 아니다.

## 왜 mobile 만 흉내내는가

팔은 지금 관제에 보고하는 채널이 없다. §6.2 의 `arm.*` 가 미정의고,
[`backend/README.md`](../../backend/README.md) 가 적어둔 대로 OMX 는 관제 PC 에
USB 직결이라 **잃을 네트워크 링크가 없어** heartbeat 대상도 아니다. 지금 팔을
흉내내면 없는 규약을 지어내게 된다. `arm.*` 정본이 생기면(로드맵 6) 붙인다.

`type_mismatch.yaml` 은 예외다. 팔을 흉내내는 게 아니라 **팔을 잘못 배선했을 때**
서버가 어떻게 반응하는지를 본다.

## 알아둘 것

하네스가 끝나면 heartbeat 도 멈춘다. 15초 뒤 백엔드가 그 로봇에 `comm_lost` 를
찍는다(`HEARTBEAT_OFFLINE_AFTER_SEC`). 정상 동작이다 — 진짜 로봇은 안 멈추기
때문이다. 대시보드를 계속 켜두려면 하네스를 계속 돌려라.

`source_node` 는 `fake_robot` 으로 고정이다. 타임라인에서 가짜가 섞여 들어온
것을 알아볼 수 있어야 조사가 된다.
