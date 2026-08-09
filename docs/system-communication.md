# 시스템 통신

프론트·백엔드·로봇이 서로 어떻게 데이터를 주고받는지.

## 핵심 원칙

**프론트와 로봇은 직접 통신하지 않는다.** 백엔드가 유일한 매개다.
카메라 스트림 하나만 예외로 로봇 → 프론트 직접 흐름을 가진다.

## 채널 요약

| 발신                 | 수신         | 프로토콜                                 | 방향      | 지연              |
| -------------------- | ------------ | ---------------------------------------- | --------- | ----------------- |
| 로봇 (QR 노드)       | 백엔드       | HTTP POST `/qr/scan`                     | 요청·응답 | 수백 ms           |
| 로봇 (event_gateway) | 백엔드       | HTTP POST `/events` (배치)               | 발행      | 배치 주기 (수 초) |
| 로봇 (event_gateway) | 백엔드       | HTTP POST `/robots/{id}/battery`          | 발행      | 2분 주기          |
| 프론트               | 백엔드       | HTTP GET                                 | 폴링      | 최대 3초          |
| 로봇 (qr_reader)     | 프론트       | HTTP `multipart/x-mixed-replace` (MJPEG) | 스트림    | 실시간            |
| 로봇 노드 간         | 로봇 노드 간 | DDS / UDP                                | pub/sub   | 밀리초            |

전체 그림: [system-communication 다이어그램](./diagrams/system-communication.png)

## 채널 1 · 로봇 → 백엔드

### `POST /qr/scan` (요청·응답)

- 발신: [`mingky_qr_reader/qr_reader_node.py`](../mingky_ros/mingky_qr_reader/mingky_qr_reader/qr_reader_node.py) `_post_scan()`
- 수신: [`backend/app/routers/qr.py`](../backend/app/routers/qr.py) `POST /qr/scan`

환자가 QR 카드를 카메라에 보이는 순간 발사된다. 세션 생성 요청과 오늘 진료
일정 조회가 하나로 묶여 있다. 로봇이 응답을 받아야 `session_id` 와 방문
순서를 알 수 있어서 요청·응답 방식이다.

**이 응답이 특별한 이유:** 백엔드가 로봇으로 push 하는 채널이 따로 없다.
로봇이 알아야 할 세션 정보는 이 응답 하나에 실려 온다. 그래서 응답 본문을
파싱해 로봇 내부에 `SessionStart` 메시지로 흘려주는 배관이 필요하다
(`qr_reader_node.py:_publish_session_start`).

### `POST /events` (배치 발행)

- 발신: [`mingky_event_gateway/gateway_node.py`](../mingky_ros/mingky_event_gateway/mingky_event_gateway/gateway_node.py)
- 수신: [`backend/app/routers/events.py`](../backend/app/routers/events.py) `POST /events`

로봇에서 이벤트가 발생할 때마다 로컬 SQLite 큐 ([`queue_store.py`](../mingky_ros/mingky_event_gateway/mingky_event_gateway/queue_store.py))
에 쌓고, 배치가 차거나 타이머가 만료될 때 몰아서 전송한다.

큐를 거치는 이유는 네트워크가 끊긴 상태에서 로봇이 발행한 이벤트를 잃지
않기 위함이다. 복구 후 몰아 보내며 `event_id` (UUID) 로 중복 배제한다.

배터리 표본은 첫 측정값을 즉시 보내고 이후 2분 주기로 PostgreSQL
`robot_battery_log`에 저장한다. 최신값만 의미가 있으므로 이벤트 SQLite 큐에는
넣지 않고, 전송 실패 시 다음 주기의 최신 표본으로 대체한다.

## 채널 2 · 프론트 → 백엔드 (폴링)

의료진 대시보드가 [`usePolling`](../frontend/src/lib/usePolling.ts) 훅으로
3초 주기 GET 을 3곳에 던진다.

- `GET /api/sessions/active` — 환자 정보 카드 · 진행 상황 스텝
- `GET /api/robots` — 로봇 상태 카드의 배터리 (`session.robot_id` 매칭)
- `GET /api/events?limit=30` — 알림 영역(최근 10건 슬라이스) + 로봇 상태·현재 목적지 파생

**로봇 실시간 상태 API 는 별도로 두지 않는다.** 대신 이벤트 스트림에서
파생한다 ([`lib/derivedStatus.ts`](../frontend/src/lib/derivedStatus.ts)).

- `deriveCurrentDestination` — 세션의 최신 `nav.goal_sent.payload.visit_name`
- `deriveRobotState` — 우선순위 규칙 (완료 → 통신두절 → 배터리부족 → 일시정지 → 최신 `nav.*` → 환자 확인 → 대기)

한계: 이벤트 배치 전송 지연으로 몇 초 늦게 반영된다. 통신·배터리·정지는 각각
`comm_restored`, `battery_recovered`, `resumed`와 짝을 이루며 최신 상태 전이를
기준으로 해제된다. 실시간 push 채널(SSE 등)이 붙기 전까지의 임시 계층이다.

Vite dev 는 `/api` 접두사를 프록시로 벗겨 백엔드에 전달한다
([`frontend/vite.config.ts`](../frontend/vite.config.ts)).
배포 시엔 리버스 프록시나 CORS 미들웨어가 따로 필요하다.

**왜 폴링인가:** [`monitoring-spec.md`](./monitoring-spec.md) 2절 결정.
WebSocket / SSE 는 MVP 오버엔지니어링이라 판단했고, 관제 화면은 사람 반응
속도(초 단위)면 충분하다. 실시간 요구가 커지면 SSE 로 전환 예정.

의료진 화면은 `POST/DELETE /api/robots/{id}/arm`으로 로봇을 활성화하거나
취소한다. 세션 종료 · 단계 수동 완료 같은 쓰기 액션은 아직 UI가 없다.

## 채널 3 · 로봇 → 프론트 (MJPEG 직접)

- 어디서: [`qr_reader_node.py`](../mingky_ros/mingky_qr_reader/mingky_qr_reader/qr_reader_node.py) `_PreviewServer`
- 프로토콜: HTTP `multipart/x-mixed-replace; boundary=frame`

QR 노드가 로봇 안에서 Flask 내장 서버로 프레임을 밀어낸다. 브라우저는
`<img src="http://<로봇IP>:<preview_port>/stream">` 로 실시간 갱신되는
이미지처럼 렌더한다 ([`frontend/src/components/CameraStream.tsx`](../frontend/src/components/CameraStream.tsx)).

**왜 백엔드 안 거치나:**

- 영상 데이터가 커서 릴레이가 CPU·네트워크 낭비
- 저지연이 필요 (로봇 시야 실시간 모니터링)
- 백엔드는 상태·로직 담당이라 미디어와 관심사가 다르다

**한계:** 브라우저가 로봇 IP 에 도달 가능한 네트워크 안에서만 동작한다.
원격 접속 시엔 이 예외도 리버스 프록시 뒤로 넣어야 한다.

## 로봇 내부 통신 (ROS2 DDS)

로봇 안의 노드끼리는 백엔드를 거치지 않는다. Fast DDS Discovery Server 위에서
직접 pub/sub 한다 (`mingky_bringup` 실행 스크립트).

| 발신             | 토픽                                                 | 수신                    |
| ---------------- | ---------------------------------------------------- | ----------------------- |
| qr_reader_node   | `/qr_reader_node/session_start` (`SessionStart.msg`) | guide_manager           |
| guide_manager 등 | `/events` (`Event.msg`)                              | event_gateway           |
| guide_manager    | `/guide_manager/state` (`GuideState.msg`)            | (예정: LCD, 게이트웨이) |

지연은 밀리초 수준. UDP 기반이라 이론적으로 손실 가능성이 있지만 같은 로봇
안에서는 사실상 없다.

## 실제 시나리오

### QR 스캔 → 대시보드 반영

```
t=0.0s   환자가 QR 카드 대기
t=0.1s   qr_reader: QR 인식 → POST /qr/scan
t=0.3s   백엔드: INSERT guidance_sessions, INSERT session_steps → 200 응답
t=0.3s   qr_reader: SessionStart 발행 (ROS2 토픽)
t=0.3s   guide_manager: 수신 · 저장 · 로그
              (백엔드에는 이미 저장됨. 프론트는 다음 폴링에 봄)
t≤3.0s   프론트: GET /api/sessions/active → 카드 리렌더
```

프론트 지연 최대 3초. 이는 폴링 주기의 특성이지 통신 성능 문제가 아니다.

### 로봇 도착 이벤트 → 대시보드 반영

```
t=0.0s   guide_manager: nav.goal_succeeded 이벤트 발행 (ROS2)
t=0.0s   event_gateway: SQLite 큐에 추가
t≤배치주기 event_gateway: POST /events
         백엔드 ingest.py: events INSERT + session_steps.arrived_at 갱신
t≤3.0s   프론트: GET /api/events?limit=30 → 알림 영역 + 파생 상태 갱신
```

총 지연 = 배치 주기 + 폴링 주기. 시연 스케일에선 몇 초 안쪽.

## 왜 이 구조인가

### 직접 통신 안 하는 이유

- 프론트가 로봇 IP 를 알 필요가 없다 → 로봇 교체·재배치·다중 로봇에도
  프론트 코드는 무영향
- 상태 정본이 백엔드/DB 한 곳에 있어야 다중 뷰 (의료진 · 엔지니어 · LCD)
  가 일관됨
- 인증·권한을 붙일 때 한 지점만 관리하면 됨

### MJPEG 만 직접 예외인 이유

- 영상은 대역폭 큰 스트림. 백엔드 릴레이는 낭비
- 응용 로직이 필요 없는 순수 미디어라 매개할 이유가 없음
- 배포 환경(원격) 이 되면 이 예외도 리버스 프록시 뒤로 넣게 될 것

### 로봇 내부에 ROS2 쓰는 이유

- Nav2 · 센서 · 액션 등 기존 로보틱스 생태계 그대로 활용
- DDS 는 로컬 pub/sub 이 저지연 · 자동 발견

## 관련 문서

- [`monitoring-spec.md`](./monitoring-spec.md) 5절 — 원 결정
- [`diagrams/qr-scan-sequence.excalidraw`](./diagrams/qr-scan-sequence.excalidraw) — QR 스캔 시퀀스
- [`diagrams/software-architecture.excalidraw`](./diagrams/software-architecture.excalidraw) — 소프트웨어 아키텍처
