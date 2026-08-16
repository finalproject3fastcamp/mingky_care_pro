# 관제 · 운영 설계서

Pinky(mobile) 2대 + OMX(manipulator) 2대로 구성된 이종 군집의 관제·운영 설계.

이 문서는 **구현된 것과 설계된 것을 구분해서** 적는다. 표에 `TODO` 가 붙은 항목은
아직 코드가 없다. 구현 세부는 각 파트 README 를 따르고, 여기에는 "왜 그렇게
했는가" 와 "무엇이 갈라지는가" 만 남긴다.

관련 문서 — [`system-communication.md`](system-communication.md)(통신 원칙) ·
[`infra-setup.md`](infra-setup.md)(인프라) · [`robot-onboarding.md`](robot-onboarding.md)(온보딩) ·
[`qr-scan-flow.md`](qr-scan-flow.md)(QR 입력) ·
[`../config/event_codes.yaml`](../config/event_codes.yaml)(이벤트 정본)

---

## 1. 목표 — SLO

> **환자 안내 세션의 90% 가 수동 개입 없이 완주한다.**

이 문서의 모든 결정은 이 한 문장에서 나온다. 어떤 알림이 사람을 깨울 자격이
있는지, 어떤 지표를 대시보드 맨 위에 둘지, 배포를 멈출 시점이 언제인지가 전부
여기서 갈린다. 지표를 먼저 늘리면 계기판만 화려하고 아무도 안 보는 상태가 된다.

### 1.1 측정 정의

측정 방법이 없는 SLO 는 구호다. 한 세션이 **성공**이려면 아래를 모두 만족한다.

| 조건 | 판정 |
| --- | --- |
| 정상 종료 | `session.ended` 의 사유가 실패·취소가 아님 |
| teleop 없음 | 세션 구간에 teleop 점유 기록 없음 |
| 수동 정지 없음 | `robot.paused`(수동) · `robot.estop_engaged` 없음 |
| 관리자 개입 없음 | 세션 구간에 `system_stop` · `system_restart` · `localize` order 없음 |

`nav.stuck` 이나 `nav.goal_aborted` 는 **실패로 치지 않는다.** 로봇이 스스로
복구해 완주했으면 성공이다. 사람이 손을 댔는가만 본다.

판정에 쓰는 개입 기록은 `control_audit` 이다(§7.2). 위 네 조건 중 아래 둘은
`events`, 위 둘은 그 표에서 나온다.

> **주의** — 관리자 개입 셋 중 `system_stop` · `system_restart` 는 진행 중인
> 세션이 있으면 백엔드가 409 로 **거부한다**(`routers/orders.py`). 세션 구간에
> 실제로 들어올 수 있는 관리 명령은 `localize` 뿐이다. 판정 집합에는 셋을 다
> 남겨두되(가드가 풀리거나 세션 시작과 겹치는 경우 대비), 완주율이 예상보다
> 높게 나오면 판정 로직보다 이 사실을 먼저 의심한다.

### 1.2 오차 예산과 판정 창

90% 는 10세션 중 1회 실패를 허용한다는 뜻이다. 다만 이 규모에서는 표본이 작아
하루치로 판정하면 잡음에 흔들린다. **직전 50세션 이동창**으로 본다.

오차 예산을 다 쓰면(직전 50세션 중 6회 이상 개입) 기능 추가를 멈추고 원인부터
잡는다. 이 규칙이 없으면 SLO 는 벽에 붙은 숫자로 끝난다.

---

## 2. 설계 원칙

1. **정본은 하나다.** 이벤트는 `config/event_codes.yaml`, 스키마는
   `database/migrations/`. 두 곳에 같은 사실을 적지 않는다.
2. **휘발성 상태는 영속화하지 않는다.** 3~5초마다 덮어쓰는 값(배터리, CPU,
   arming)은 메모리에 둔다. DB 에는 **판정 결과**(events)만 남는다.
3. **공통 코어는 로봇 타입과 무관하게 강제하고, 갈라지는 것은 한 곳에만 몰아넣는다.**
   이종 군집이 무너지는 방식은 둘 다 흔하다 — 하나의 스키마에 우겨넣어 절반이
   항상 `NULL` 이거나, 완전히 따로 만들어 관제 시스템을 두 벌 유지하거나.
4. **관측은 사람이 볼 때만 존재한다.** 대시보드에만 있고 아무도 안 보는 지표는
   없는 것과 같다. 심각한 사건은 화면 밖으로 나가야 한다.
5. **전제는 문서가 아니라 코드가 강제한다.** 주석에만 있는 제약은 반드시 깨진다.
   사람이 바뀌거나 6개월 뒤 본인이 잊는다. §9.2 참조.

---

## 3. 시스템 구성

### 3.1 배치

```
[로봇 4대]                    [클라우드 관제 서버]              [브라우저]
 pinky-01 ─┐                   nginx :443
 pinky-02 ─┤ SSH 역터널  ───▶   ├─ /api/      → backend:8000 (FastAPI)
 omx-01   ─┤                   ├─ /camera/…  → MJPEG 터널 포트
 omx-02   ─┘                   ├─ /fg/<token>/… → Foxglove bridge
                               └─ /          → frontend (React SPA)
                                     │
                               PostgreSQL
```

로봇은 병원 내부망에 있고 공인 IP 가 없다. 로봇이 **바깥으로 거는** SSH 역터널
하나로 통신을 모은다(`deploy/cloud/10-tunnel-keepalive.conf`). 관제 서버가
로봇에 접속하는 구조가 아니라 그 반대이고, 방화벽·NAT 를 건드리지 않는다.

### 3.2 통신 경로

| 흐름 | 방식 | 근거 |
| --- | --- | --- |
| 이벤트 (로봇→서버) | `POST /events` 배치 | 두절 대비. 게이트웨이가 SQLite 에 쌓았다가 몰아 보낸다 |
| 생존 신호 | `POST /robots/{id}/heartbeat` | **큐를 타지 않는다.** 실패하면 버린다 (§3.3) |
| 명령 (서버→로봇) | 로봇이 `GET /orders/next` 폴링 + ack | 역터널 방향과 일치. 서버가 로봇을 부르지 않는다 |
| 상태 갱신 (서버→화면) | 폴링 3~5초 | 사람 반응 속도에 충분. WebSocket 은 MVP 에 과했다 |
| teleop | WebSocket | 유일한 실시간 요구. 조작자↔로봇 양방향 |
| 카메라 | MJPEG, 로봇별·방향별 터널 포트 | WebRTC 는 시그널링 비용이 값에 안 맞았다 |
| ROS 내부 디버깅 | Foxglove bridge (토큰 경로) | 원시 토픽이 필요할 때만. 상시 경로가 아니다 |

**이 인터페이스가 순수 HTTP 라는 점이 중요하다.** 로봇을 흉내내는 데 ROS 가
필요 없다는 뜻이고, §9.1 의 가짜 로봇 하네스가 여기서 나온다.

### 3.3 두절 판정

두절은 "아무것도 안 오는 상태" 라서 도착하는 데이터로는 감지할 수 없다.
마지막 수신 시각을 기억하고 주기적으로 경과를 잰다(`backend/app/heartbeat.py`).

heartbeat 를 이벤트 큐에 넣으면 안 된다. 두절 중 heartbeat 가 쌓였다가 복구
순간 "10분 전 나 살아있었음" 이 한꺼번에 도착하면서 신호의 의미가 사라진다.

> **제약** — `heartbeat` · `arming` · `orders` 는 인메모리다. **uvicorn 워커 1개
> 전제**이며, 늘리면 오탐이 조용히 쌓인다. 확장이 필요하면 저장소만 PostgreSQL
> 로 옮긴다. 판정 로직은 `touch()`/`snapshot()` 뒤에 가려져 있다.
> 이 전제는 코드가 강제해야 한다 — §9.2.

---

## 4. 로봇 모델 — 이종 군집

### 4.1 구성

| robot_id | robot_type | 이동성 | domain_id | 역할 |
| --- | --- | --- | --- | --- |
| pinky-01 · pinky-02 | `mobile` | 자율주행 | 필수 (21, 20) | 환자 안내 |
| omx-01 · omx-02 | `manipulator` | 고정 | `NULL` | 모방학습 기반 조제 |

`003_sessions_and_events.sql:23` 의
`CHECK (robot_type <> 'mobile' OR domain_id IS NOT NULL)` 가 이 구분을 DB 에서
이미 강제한다. 애플리케이션 층은 §6.1 의 `robot_types` 검증으로 이 구분을
따라잡았다.

### 4.2 명명 충돌 — 정리됨

저장소에서 `arm` 이 두 뜻으로 쓰였다.

| 표기 | 뜻 | 위치 |
| --- | --- | --- |
| `POST /robots/{id}/arm`, `arming.py`, `activation.*` | 의료진이 "이 로봇을 지금 쓰겠다" 고 **활성화** | 백엔드·프론트 전역 |
| `robot_type = 'arm'` | **로봇팔**(OMX) | DB 스키마·시드 |

팔에 arming 을 붙이는 순간 `arm robot arming` 같은 식별 불가능한 이름이 나온다.
활성화 쪽을 `assignment` 로 바꾸는 선택지도 있었으나 백엔드·프론트 전역에
걸쳐 있어 영향 범위가 훨씬 넓었다.

**`robot_type = 'arm'` → `'manipulator'` 로 정리했다** —
`010_robot_type_manipulator.sql`, 시드 2행. 프론트는 `=== 'mobile'` 로만
거르고 있어 손댈 곳이 없었다. 003 은 고치지 않았다: 적용된 마이그레이션은
그 DB 가 실제로 무엇을 거쳤는지에 대한 기록이고, 004~009 가 전제하는 상태다.

> 이 절을 고칠 때 `arm` 을 일괄 치환하지 않는다. `arming` 의 `arm` 과 §6.2 의
> `arm.*` 이벤트 접두사까지 같이 바뀌면서 이 절이 자기 자신을 지운다.
> 실제로 한 번 그렇게 깨졌다.

`activation.*` 는 `mobile` 전용으로 남는다. `routers/robots.py` 가
`robot_type != 'mobile'` 인 로봇의 arming 을 이미 거부한다.

### 4.3 공통 코어와 타입별 확장

| 축 | 공통 (타입 무관) | mobile 전용 | manipulator 전용 |
| --- | --- | --- | --- |
| 생존 | heartbeat, `robot.comm_lost/restored` | — | 시리얼(U2D2) 응답 |
| 형상 | git SHA, 부팅 경과 | 맵 파일 해시 | **정책 체크포인트 · 데이터셋 revision** |
| 리소스 | CPU, 디스크 | Wi-Fi RSSI, 배터리 | 서보 온도 · 전류 |
| 진행 | `session.*`, 이벤트 스트림 | `nav.*` `dock.*` `localize.*` `activation.*` | `arm.*` (미정의) |
| 제어 | orders + ack, 감사 로그 | system start/stop, teleop | 홈 복귀, 재시도 |
| SLI | — | goal 성공률, stuck 빈도 | 사이클 타임, pick 성공률 |

이 표가 `config/event_codes.yaml` 의 `robot_types` 정본이다. 어느 쪽을 고치든
다른 쪽을 같이 본다 — `backend/tests/test_ingest.py` 가 공통 축(`session.*`,
`robot.comm_*`)과 mobile 전용 축(`activation.*`)을 양쪽으로 잠가 둔다.

공통 열은 구현돼 있다. `heartbeat.py` · `orders.py` · `registry.py` 는 타입을
몰라도 되며, 그대로 둔다.

### 4.4 팔에서 성격 자체가 다른 것

표면적인 지표 차이보다 이쪽이 중요하다.

**버전의 의미가 다르다.** mobile 의 "무엇이 돌고 있나" 는 git SHA 로 끝나지만,
팔은 **코드 SHA + 정책 체크포인트 + 학습 데이터셋 revision** 세 개다. 어제 되던
pick 이 오늘 안 되는 원인은 코드가 아니라 체크포인트를 바꿔 낀 것인 경우가 많다.

**실패가 확률적이다.** nav 실패는 대개 결정론적 버그지만 모방학습 pick 은 원래
90% 근처다. 20회 중 2회 실패는 정상 범위이고, 여기에 mobile 과 같은 임계 알림을
걸면 알림 전체가 무의미해진다. 팔의 회귀 판정은 **직전 N회 이동평균이 기준선
신뢰구간을 벗어날 때** 로 잡는다.

**롤백 대상이 다르다.** mobile 은 코드를 되돌리고, 팔은 체크포인트를 되돌리는
일이 훨씬 잦다. 배포 단위에서 정책 아티팩트를 코드와 분리해 버전을 매긴다.

**Dynamixel 이 공짜로 주는 신호가 있다.** 서보가 온도·전류·전압·하드웨어 에러
비트를 직접 보고한다. mobile 에 없는 진짜 예지보전 신호다 — 특정 조인트 온도가
회차마다 올라가면 그리퍼 마모나 과부하 자세다.

**원격 개입의 어휘가 다르다.** 팔에 조이스틱을 주는 것은 위험하고 쓸모도 적다.
팔의 개입은 이산 명령이다 — 홈 복귀, 실패한 pick 재시도, 트레이 비우기,
리더-팔로워 재캘리브레이션. teleop WebSocket 이 아니라 `orders` 큐를 쓴다.

---

## 5. 데이터 평면 — 네 종류를 구분한다

| 종류 | 예 | 저장소 | 보존 | 상태 |
| --- | --- | --- | --- | --- |
| **이벤트** (불연속) | `nav.stuck`, `session.ended` | `events` (JSONB payload) | 영구 | 구현됨 |
| **상태** (휘발성) | 배터리, CPU, arming, 링크 | 메모리 | 없음 | 구현됨 |
| **메트릭** (연속) | 시간당 goal 성공률, 서보 온도 추이 | 1분 롤업 테이블 | 90일 | **TODO** |
| **로그** (원문) | journald, rosbag | 로봇 로컬 | 트리거 덤프만 | **TODO** |

메트릭이 지금 비어 있다. 상태는 메모리에만 있다가 사라지므로 "요즘 나빠지고
있나" 를 볼 수 없다. 원칙 2 와 충돌하지 않는다 — 원본이 아니라 **집계값만**
남기기 때문이다. 로봇 4대 규모에서는 Prometheus 보다 PostgreSQL 롤업 테이블
하나가 싸다(§10).

---

## 6. 이벤트 코드 정본

현재 42개. 영역별로 `qr` `patient` `fire` `nav` `waypoint` `localize` `dock`
`session` `activation` `robot` `system`.

### 6.1 타입 제한 — `robot_types`

이전 정본에는 "누가 이 코드를 낼 수 있는가" 가 없었다. omx-01 이
`nav.goal_sent` 를 보내면 배선이 잘못된 것인데 그대로 적재됐다.

각 코드에 `robot_types: [mobile]` / `[manipulator]` / `[mobile, manipulator]` 를
붙이고 `ingest` 에서 로봇의 `robot_type` 과 대조한다. 분류 기준은 §4.3 표다.

> **주의** — `applies_to` 는 이미 다른 뜻으로 쓰이고 있다("이 이벤트가 갱신하는
> DB 컬럼"). 재사용하면 안 된다.

위반 시 처리는 미등록 코드와 같은 원칙을 따른다 — **거부하지 않고 적재한 뒤**
경고 이벤트(`system.robot_type_mismatch`)를 추가 발행하고 `IngestResult.
type_mismatches` 로 게이트웨이에도 돌려준다. 기록을 잃지 않으면서 오배선이
대시보드와 응답 양쪽에 드러난다.

**단, 상태 갱신은 건너뛴다.** "거부하지 않는다" 는 이벤트 레코드 얘기지 판정이
아니다. `nav.goal_succeeded` 는 `session_steps.arrived_at` 을 찍으므로, 오배선을
그대로 적용하면 조제 로봇 하나가 환자의 안내 단계를 진행시킨다 — §6.1 이 막으려던
바로 그 시나리오다. 판정은 `_apply_state_safely` 보다 **먼저** 온다.

분류를 몰라서 판정할 수 없는 경우 — `robots` 에 없는 로봇 — 는 오배선이 아니다.
등록 누락이라는 다른 문제이므로 경고하지 않는다.

### 6.2 `arm.*` 신설 — 팔은 현재 아무것도 보고하지 않는다

정본에 팔 전용 코드가 **하나도 없다.** 조제 로봇 2대가 관제 시스템에 보이지
않는다는 뜻이다. 최소 집합:

| 코드 | level | payload |
| --- | --- | --- |
| `arm.cycle_started` | info | `order_id, medication_id` |
| `arm.pick_succeeded` | info | `medication_id, attempt, duration_ms` |
| `arm.pick_failed` | warning | `medication_id, attempt, reason` |
| `arm.place_succeeded` | info | `tray_slot` |
| `arm.cycle_completed` | info | `order_id, duration_ms` |
| `arm.cycle_aborted` | error | `order_id, reason` |
| `arm.servo_fault` | error | `joint, fault_bits, temp_c` |
| `arm.policy_loaded` | info | `checkpoint_id, dataset_revision` |
| `arm.homing_required` | warning | `reason` |

`arm.pick_failed` 는 `warning` 이다 — 확률적 실패는 정상 동작이므로 `error` 로
올리면 안 된다(§4.4). `error` 는 사이클 포기와 서보 결함에만 쓴다.

---

## 7. 대시보드

### 7.1 의료진 대시보드 (`/medical`) — 구현됨

환자 정보, 진행 스테퍼, 로봇 상태·배터리, 전방 카메라 프리뷰, 알림.
로봇별 URL(`/medical/:robotId`)을 가져 새로고침·탭 분리가 동작한다.

### 7.2 엔지니어 대시보드 (`/engineer`)

| 탭 | 현재 | 추가 계획 |
| --- | --- | --- |
| `events` | 타임라인, 레벨·노드 필터, 미등록 코드 패널 | 세션 리플레이 (지도 + 이벤트 마커) |
| `system` | systemd 유닛 제어, AMCL 재탐색, CPU·큐 적체 | **토픽 주기(Hz) 감시**, 형상 패널 |
| `waypoints` | waypoint 관리·테스트 | — |
| `cameras` | MJPEG 프리뷰 | — |
| `fleet` | **SLO 현황**, 오차 예산, 실패 세션, 4대 요약, 최근 개입 | 선행 지표 SLI 집계 |
| `manipulator` | 없음 | 조제 사이클, 서보 상태, 정책 버전 |

**SLO 현황이 맨 위에 온다.** 직전 50세션 완주율과 오차 예산 잔량. 나머지 지표는
그 숫자가 나빠졌을 때 원인을 찾는 도구다.

구현됨 — `GET /slo/completion`(`app/slo.py`) · `GET /control-audit` ·
`routes/FleetDashboard.tsx`. 화면은 판정을 다시 하지 않고 서버가 계산한 값을
그대로 그린다. 두 곳에서 세면 화면과 API 가 다른 완주율을 말하는 날이 온다.

화면이 **구분해서 그려야 하는 세 쌍**이 있다. 셋 다 한쪽으로 뭉개면 사람이
잘못 판단한다.

| 왼쪽 | 오른쪽 | 왜 다른가 |
| --- | --- | --- |
| 표본 없음 (`completion_rate: null`) | 완주율 0% | 하나는 안내, 하나는 비상 |
| 예산 잔량 0 | 예산 소진 | 잔량 0 은 아직 목표 안이다 (§1.2) |
| 기록 안 함 | 익명 기록 | 후자는 명령은 갔고 이름만 없다 |

**토픽 주기 감시** — 스펙에 "주기 이상 여부" 를 적어놓고 미구현이다. 현재
`system` 탭은 systemd 유닛 상태만 본다. 실전 장애 모드는 **유닛은 active 인데
`/scan` 이 안 나오는 것**이다. 라이다 USB 가 죽어도 노드는 살아있다.

구현은 §3.3 의 두절 판정과 같은 구조다. 감시 노드가 `/scan` `/odom`
`/amcl_pose` `/cmd_vel` 을 구독해 콜백에서 마지막 수신 시각만 갱신하고,
heartbeat payload 에 `{topic: age_sec}` 로 실어 보낸다. 도착하는 데이터가
아니라 **경과 시간**으로 판정한다. `ros2 topic hz` 를 서브프로세스로 돌리지 않는다.

**형상 패널** — `schemas.py` 에 노드별 `commit` 이 있는데 "이 로봇이 지금 무슨
커밋·무슨 맵·무슨 정책으로 도는가" 를 한눈에 보는 곳이 없다. 데모가 어제는
됐는데 오늘 안 되는 원인의 대부분이 여기다. 4대의 SHA 가 서로 다르면 경고한다.

**SLI 집계** — 이벤트 코드가 이미 정의돼 있으므로 SQL 집계만 얹으면 된다.

| 지표 | 계산 | 타입 | SLO 관계 |
| --- | --- | --- | --- |
| **세션 완주율** | §1.1 판정 | 공통 | **SLO 지표** |
| 주행 성공률 | `nav.goal_succeeded / (succeeded + aborted)` | mobile | 선행 지표 |
| 끼임 빈도 | `nav.stuck` / 세션 | mobile | 선행 지표 |
| QR 성공률 | `qr.scan_ok / (ok + failed)` | mobile | 선행 지표 |
| 구간 이동 시간 p50/p95 | `goal_sent → goal_succeeded` 간격 | mobile | 진단용 |
| pick 성공률 | `arm.pick_succeeded / (succeeded + failed)` | manipulator | 선행 지표 |
| 사이클 타임 p50/p95 | `arm.cycle_completed.duration_ms` | manipulator | 진단용 |

선행 지표는 SLO 가 깨지기 **전에** 움직인다. 주행 성공률이 3일에 걸쳐 98%→85%
로 내려가면 완주율이 곧 따라 내려온다 — 바퀴나 맵을 보라는 신호다.

**감사 로그** — 구현됨. `control_audit`(`database/migrations/011_control_audit.sql`)
에 제어 명령과 teleop 점유가 실행자와 함께 남는다. 병원 도메인에서는 기능이
아니라 요건이고, §1.1 의 "수동 개입 없음" 판정도 여기서 나온다.

- **전달** — HTTP 는 `X-Actor` 헤더, teleop 은 `?actor=` 쿼리(브라우저
  WebSocket 에는 커스텀 헤더를 실을 수 없다). 정규화는 `backend/app/actor.py`
  한 곳을 지난다.
- **인증이 아니다** — 로그인 계층이 없으므로 자기신고다. `actor_source` 가
  그 한계를 기록한다(`header` = 자기신고, `absent` = 미제출).
- **누락은 거부하지 않는다** — 헤더가 없어도 명령은 나가고 익명으로 남는다.
  막으면 프론트 버그 하나로 비상정지를 못 누르게 되고, 거부는 정직한 누락만
  막고 위조는 못 막는다. 대신 **드러낸다** — `fleet` 탭이 익명 비율을 띄운다.
- **기록은 넓게, 판정은 좁게** — `action` 은 들어온 명령을 전부 남기고, SLO
  판정에 쓰는 것은 `control_audit.INTERVENTION_ACTIONS` 뿐이다.
- **효과보다 먼저 기록한다** — §1.1 이 "order 없음" 으로 판정하므로 기록의
  주어는 로봇의 행동이 아니라 사람의 판단이다. 순서를 뒤집으면 실행됐는데
  기록이 없는 창이 생겨 SLO 가 실제보다 좋아 보인다. 단 적재 실패가 제어를
  막지는 않는다(fail-open) — 그 대가로 DB 장애 중의 개입은 판정에서 빠진다.

남은 것은 "최근 수동 개입" 목록을 `fleet` 탭에 얹는 것뿐이다.

### 7.3 타입별 분기 규칙

- **백엔드** — 공통 필드는 평평하게, 타입별 지표는 `detail JSONB` 한 칸에 담는다.
  배터리·RSSI·서보 온도를 전부 컬럼으로 만들면 절반이 항상 `NULL` 이고, 세 번째
  로봇 종류가 오면 또 마이그레이션한다. 조회는 `detail->>'servo_temp_max'` 로 된다.
- **프론트** — `robot_type` 을 discriminant 로 하는 discriminated union
  (`type Robot = MobileRobot | ArmRobot`). 런타임 `if` 분기로는 "팔에 배터리
  카드를 렌더" 하는 실수가 안 잡히지만, 이러면 컴파일 타임에 잡힌다.
- **선택기** — 로봇 선택기는 4대를 모두 보여주고 제어 패널만 타입에 따라 바꾼다.
  현재 `SystemDashboard` 는 `filter(robot_type === 'mobile')`, `CameraDashboard`
  는 `pinky-` 접두사로 팔을 화면에서 아예 뺀다. 카메라는 팔에 없으니 타당하지만,
  `system` 탭에서 빠진 것은 **팔이 관제 대상이 아니라는 뜻**이라 고쳐야 한다.

---

## 8. 운영 (RobOps)

### 8.1 배포 · CI

OTA 보다 CI 가 병목이다. 순서대로 한다.

1. **CI** — GitHub Actions 에서 `colcon build` + `colcon test` + backend `pytest`
   (13개 있음) + `npm run build`. PR 게이트로 건다.
2. **릴리스 단위** — 로봇에서 `git pull` 은 롤백이 안 된다. 태그된 릴리스를
   `/opt/mingky/releases/<sha>/` 에 풀고 `current` 심볼릭 링크를 바꾼다. 롤백이
   링크 되돌리기 한 번이 된다.
3. **정책 아티팩트는 별도 채널** — 팔의 체크포인트는 코드와 수명주기가 다르다
   (§4.4). 따로 버전을 매기고 따로 롤백한다.
4. **카나리** — 4대 중 1대 먼저, 세션 완주 확인 후 나머지. 동시 배포 금지.
5. **잠금** — 환자 안내 중인 로봇에는 배포하지 않는다. `active_session_id` 로
   이미 판정 가능하다. `SystemDashboard` 가 재시작을 막는 것과 같은 조건을
   배포 스크립트가 API 로 물어본다.
6. **오차 예산 게이트** — 예산을 다 썼으면(§1.2) 기능 배포를 멈춘다.

### 8.2 로그

- **텍스트** — systemd 유닛이므로 journald 에 이미 다 있다. `journalctl -u
  mingky-*` 로 시작하고, 필요해지면 관제 서버로 포워딩한다. Loki 는 이 규모에 과하다.
- **rosbag** — 상시 기록은 SD카드를 죽인다. **트리거 기반 링 버퍼**를 쓴다.
  최근 60초를 메모리에 물고 있다가 `nav.stuck` · `nav.goal_aborted` ·
  `arm.cycle_aborted` 에서만 디스크로 덤프한다. 실패 순간 30초가 정상 주행
  10시간보다 가치 있다. 덤프 경로를 이벤트 payload 에 넣으면 대시보드에서 바로 열린다.

### 8.3 원격 개입

teleop WebSocket 은 있고, 빠진 것은 안전 장치다.

- **watchdog** — 입력이 300ms 이상 끊기면 로봇이 스스로 정지한다. 네트워크가
  죽었는데 마지막 `cmd_vel` 이 유지되면 병원 복도에서 로봇이 계속 간다.
- **배타적 점유** — 두 명이 동시에 잡으면 안 된다. 토큰 하나로 잠근다.
- **인계 프로토콜** — 자율→수동→자율 복귀 시 Nav2 에 현재 위치를 다시 물린다.
  여기서 로컬라이제이션이 깨지는 경우가 많다.
- **왕복 지연 표시** — 200ms 를 넘으면 조작자가 알아야 한다.
- **팔은 teleop 이 아니라 orders** — §4.4.

모든 개입은 SLO 위반으로 집계된다(§1.1). 그 선행 조건이던 감사 로그는 섰다
(§7.2) — teleop 은 조작자 소켓이 붙고 끊길 때마다 `teleop_attach` ·
`teleop_detach` 로 남는다. 판정은 attach 만 본다. 점유했다는 사실이 근거이고
detach 는 구간 길이를 알고 싶을 때 쓰는 부가 정보다.

### 8.4 인시던트 대응

규모가 작으므로 도구보다 약속이 중요하다.

1. **알림 라우팅** — `robot.comm_lost` · `robot.battery_low` ·
   `robot.estop_engaged` · `fire.detected` · `arm.servo_fault` 는 화면 밖으로
   나간다. Slack/Discord 웹훅 하나면 된다.
2. **심각도 분리** — "즉시 사람 호출" 등급은 손에 꼽게 유지한다. 알림이 흔해지면
   무시된다. 확률적 실패(`arm.pick_failed`)는 절대 이 등급에 넣지 않는다.
3. **런북** — 증상별 1페이지. `nav2-debugging.md` 가 이미 그 형태다. 우선
   `comm_lost` · 끼임 · QR 실패 · pick 연속 실패 네 가지.
4. **사후 기록** — 5줄 회고: 무엇이 보였나 / 왜 늦게 알았나 / 무슨 계기판이
   있었으면 즉시 알았나. 세 번째 질문의 답이 다음 스프린트의 대시보드 항목이 된다.

---

## 9. 개발 생산성

생산성은 **피드백 루프 길이**로 환원된다. 코드를 고치고 그게 맞는지 아는 데
걸리는 시간. 로보틱스에서 이 루프를 늘리는 범인은 거의 항상 하드웨어 접근
경합이다 — 6명이 로봇 4대를 나눠 쓴다.

### 9.1 가짜 로봇 하네스 — 구현됨

**[`tools/fake_robot/`](../tools/fake_robot/)** — 쓰는 법은 그쪽 README 에 있다.

로봇↔서버 인터페이스가 순수 HTTP 다(§3.2). **로봇을 흉내내는 데 ROS 가 전혀
필요 없다.** 파이썬 스크립트가 heartbeat 를 보내고, 시나리오를 읽어
`session.started → nav.goal_sent → nav.goal_succeeded → …` 를 시간 지연과 함께
뿌리면 대시보드는 진짜 로봇과 구분하지 못한다.

여기서 나오는 것:

- 프론트·백엔드 담당자가 **로봇 대기 없이** 개발한다.
  6명 중 절반이 하드웨어 큐에서 빠진다
- 실패 경로를 마음대로 만든다. `comm_lost` 를 보려고 Wi-Fi 를 끊을 필요가 없다.
  오배선(`scenarios/type_mismatch.yaml`)처럼 실기로는 게이트웨이를 잘못 물리거나
  `robot_id` 를 오타내야 나오는 것도 한 줄로 재현된다
- 그대로 **통합 테스트 픽스처**가 된다 — `backend/tests/e2e/` 가 이걸 쓴다 (§8.1)
- **`arm.*` 를 팔 실기 없이 먼저 설계·검증**할 수 있다 (§6.2)
- 발표용 데모 모드가 공짜로 따라온다

시나리오는 YAML 로 두고 `event_codes.yaml` 을 참조한다. 코드가 정본에 있는지,
`level` 이 맞는지, **그 로봇 타입이 낼 수 있는 코드인지**(§6.1)까지 대조한다.
정본이 바뀌면 가짜 로봇이 먼저 깨지므로 정본 준수 검사 역할까지 한다.
`backend/tests/test_fake_robot_scenarios.py` 가 그 '먼저 깨진다' 를 CI 로 옮긴다.

**mobile 만 흉내낸다.** 팔은 지금 관제에 보고하는 채널이 없다 — §6.2 의 `arm.*`
가 미정의고, OMX 는 관제 PC 에 USB 직결이라 잃을 네트워크 링크가 없어 heartbeat
대상도 아니다(§4.3). 지금 흉내내면 없는 규약을 지어내게 된다. `arm.*` 정본이
생기면(로드맵 6) 붙인다.

### 9.2 전제를 코드가 강제하게 (원칙 5)

이 저장소는 주석과 문서의 "왜" 가 유난히 좋다. 그런데 문서에만 있는 전제는
반드시 깨진다.

가장 위험한 것 — **uvicorn 워커 1개 전제**가 `heartbeat.py` · `arming.py` ·
`orders.py` 세 곳에 걸려 있고 주석으로만 존재했다. 누가 성능을 이유로
`--workers 4` 를 주면 에러 하나 없이 오탐만 쌓인다. `comm_lost` 가 멀쩡한 로봇에
찍히고, arming 이 간헐적으로 안 보이고, 원인 추적에 며칠 간다.

`main.py` 의 `_claim_single_instance()` 가 기동 시 **PostgreSQL advisory lock**
을 잡는다(`pg_try_advisory_lock`). 못 잡으면 `sys.exit(3)`.

**워커 수를 세지 않는다.** `--workers N` `--workers=N` `WEB_CONCURRENCY` 로
형태가 갈려서 argv 를 읽는 방식은 그때마다 구멍이 하나씩 생긴다. 파일 락도 아니다
— 컨테이너마다 `/tmp` 가 따로라 레플리카를 못 잡는다. DB 는 모든 인스턴스가
반드시 공유하는 유일한 지점이므로, 같은 호스트든 다른 호스트든 둘째부터 걸린다.

다중 워커에서는 startup 실패를 uvicorn supervisor 가 치명적으로 보고 전체를
내린다(`Child process failed to start, stopping the parent process`). 자식만
죽고 supervisor 가 무한히 되살리는 상태 — 포트는 잡혀 있는데 아무도 요청을 못
받고 헬스체크에는 정상으로 보이는 상태 — 는 생기지 않는다.

> **advisory lock 은 프로세스가 아니라 DB 세션에 붙는다.** DB 재시작, 네트워크
> 블립, `idle_session_timeout`, failover, 앞단 pgbouncer 로 세션이 끊기면 락만
> 조용히 사라지고 프로세스는 계속 요청을 받는다. 그 틈에 두 번째 인스턴스가
> 정상 기동한다 — `pg_terminate_backend` 한 번으로 재현된다.
>
> 그래서 기동 시 한 번 잡는 것으로 끝내지 않고 `_hold_single_instance()` 가
> 10초마다 보유를 확인하고, 잃었으면 다시 잡는다. 그 사이 남이 가져갔으면 늦게
> 안 쪽이 자신에게 SIGTERM 을 보내고 물러난다.
>
> 확인은 `is_closed()` 로 하지 않는다. 서버가 세션을 끊어도 asyncpg 는 써보기
> 전까지 모르고, 게다가 풀이 죽은 프록시를 회수한 뒤에는 `is_closed()` 호출
> 자체가 예외를 던진다. 그 예외가 워치독 태스크를 조용히 죽인 적이 있다.

| 전제 | 지금 | 강제 방법 |
| --- | --- | --- |
| 인스턴스 1개 | **강제됨** | DB advisory lock + 주기 확인 (§9.2) |
| 이벤트 코드 ↔ robot_type | **강제됨** | `robot_types` 검증 (§6.1) |
| 마이그레이션 적용 여부 | **강제됨** | `schema_migrations` + `deploy.sh migrate` |
| 이벤트 정본 ↔ `Event.msg` ↔ DB 일치 | 사람이 세 곳을 같이 고침 | CI 대조 스크립트 |
| 로봇 4대 SHA 동일 | 확인 안 함 | 형상 패널 경고 |
| 안내 중 배포 금지 | 사람이 조심 | 배포 스크립트가 API 조회 |

### 9.3 toil 제거

반복 수작업을 목록화하고 하나씩 없앤다.

| toil | 상태 |
| --- | --- |
| 계정별 `ROS_DOMAIN_ID` 세팅 | **해결됨** — `mingky-adduser`, `ros-domain.sh` |
| 로봇 SSH 접속 | 부분 — 역터널로 경로는 정리됨 |
| 배포 | 한 명령으로 만든다 |
| 4대 로그 확인 | journald 를 한 화면에 모으는 래퍼 하나 |
| 개발 환경 구축 | 컨테이너 이미지 하나. LeRobot 의존성 때문에 신규 인원 온보딩이 반나절 이상 걸린다. 그대로 CI 환경이 된다 |

`mingky-adduser` 와 `ros-domain.sh` 는 이미 이 방식으로 해결한 사례다. 나머지에
같은 패턴을 적용한다.

---

## 10. 비목표

이 규모에서 **하지 않는다.** 관측 도구가 관측 대상보다 복잡해지는 것은 흔한
실패다.

| 항목 | 대신 쓰는 것 |
| --- | --- |
| Prometheus · Grafana | PostgreSQL 롤업 테이블 + 기존 대시보드 |
| Loki · ELK | journald + 트리거 rosbag |
| Kubernetes | Docker Compose |
| Redis | 인메모리 (워커 1개 전제) |
| WebRTC | MJPEG 터널 |
| 전면 WebSocket 전환 | 폴링 3~5초, teleop 만 예외 |

로봇 대수가 두 자리로 가거나 워커를 늘려야 할 때 이 표를 다시 연다.

---

## 11. 상태 정의

```
대기 → QR 인식 → 환자 확인 → 안내중 → 도착 → 검사중 → 완료
                                        ↓
                                   다음 목적지 / 종료
```

예외 — `QR 인식 실패` · `경로 이탈` · `통신 두절` · `배터리 부족` · `일시정지`
(수동 개입) · `화재 대피`.

팔은 별도 상태를 갖는다 — `유휴 → 지시 수신 → 조제중 → 완료`, 예외는
`pick 실패(재시도)` · `사이클 포기` · `홈 복귀 필요` · `서보 결함`.

---

## 12. 결정 기록

| 항목 | 선택 | 근거 |
| --- | --- | --- |
| SLO | 세션 90% 무개입 완주 | 팀이 합의한 단일 성공 기준 |
| 판정 창 | 직전 50세션 이동창 | 표본이 작아 일별 판정은 잡음에 흔들림 |
| 프론트 | React + Vite + TS | 팀 익숙도, SSR 불필요, HMR |
| 백엔드 | FastAPI | ROS·ML 과 같은 Python 생태계, OpenAPI 자동화 |
| 상태 갱신 | 폴링 3~5초 | 사람 반응 속도에 충분 |
| teleop | WebSocket | 유일한 실시간 요구 |
| 카메라 | MJPEG + 터널 프록시 | WebRTC 시그널링 비용이 값에 안 맞음 |
| 로봇→서버 통신 | SSH 역터널 | 공인 IP·방화벽 예외 없이 성립 |
| 휘발성 상태 | 인메모리 | 워커 1개 전제, 영속 이력은 events 로 남음 |
| 미등록 이벤트 | 적재 후 경고 발행 | 기록을 잃지 않으면서 누락이 드러남 |
| QR 페이로드 | `patient_id` 평문 | 사내망 데모 우선. 서명 토큰은 보류 |

---

## 13. 미결정

- 메트릭 롤업 주기·보존기간 (1분/90일은 초안)
- 알림 채널 (Slack vs Discord)
- QR 서명 토큰 전환 시점
- 팔 SLI 기준선 — 초기 N회 실측이 필요하다
- 오차 예산 소진 시 "기능 배포 중단" 을 실제로 강제할지, 합의로 둘지

---

## 14. 로드맵

| # | 항목 | 근거 |
| --- | --- | --- |
| ~~1~~ | ~~**가짜 로봇 하네스** (§9.1)~~ | **완료** — `tools/fake_robot/` |
| ~~2~~ | ~~워커 1개 가드 (§9.2)~~ | **완료** — DB advisory lock + 주기 재확인. `test_worker_guard.py` |
| ~~3~~ | ~~`robot_types` 메타 + ingest 검증 (§6.1)~~ | **완료** — `010` 명명 정리 포함. `test_ingest.py` |
| ~~8~~ | ~~CI 게이트 (§8.1)~~ | **완료** — `unit` · `e2e` 두 잡. `backend/tests/e2e/` |
| ~~4~~ | ~~감사 로그 (actor) (§7.2)~~ | **완료** — `control_audit`(`011`) · `X-Actor` · teleop 점유. `test_control_audit.py` |
| ~~5~~ | ~~세션 완주율 집계 + `fleet` 탭~~ | **완료** — `/slo/completion` · `fleet` 탭. 선행 지표 SLI 는 남음 |
| 6 | `arm.*` 이벤트 코드 + 팔 게이트웨이 연결 (§6.2) | 조제 로봇 2대가 관제에 보이지 않는다. 하네스가 있으니 실기 없이 진행 |
| 7 | `system` 탭 타입 분기 + 팔 패널 (§7.3) | 팔을 관제 안으로 |
| 9 | 토픽 주기(Hz) 감시 (§7.2) | 못 잡는 장애 모드를 잡음 |
| 10 | 형상 패널 (SHA · 맵 해시 · 체크포인트) | 재현성 문제의 대부분 |
| 11 | 서보 온도·전류 수집 (§4.4) | 유일한 실질 예지보전 신호 |
| 12 | 알림 웹훅 (§8.4) | 원칙 4 |

**SLO 는 이제 측정된다.** 4번(감사 로그)과 5번(완주율 + `fleet` 탭)이 섰다.
남은 것은 §7.2 표의 선행 지표 SLI — 주행 성공률·끼임 빈도·QR 성공률이다.
그것들은 SLO 가 깨지기 **전에** 움직이므로, 완주율이 실제로 내려가는 것을
한 번 관측한 뒤에 붙이는 편이 기준선을 잡기 쉽다.

1번이 섰으므로 6번은 팔 실기 없이 진행할 수 있고, 8번은 1번 위에 그대로
얹혔다. 개입이 낀 세션(`session_with_intervention.yaml`)도 하네스로 재현되므로
5번의 판정 로직은 실기 없이 검증된다.
