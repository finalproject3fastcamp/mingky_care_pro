# mingky_event_gateway

로봇 이벤트를 관제 서버로 전달하고, 생존 신호를 보냅니다.

```
/events 토픽 → 로컬 큐(SQLite) → HTTP POST /events        → 성공 시 큐에서 제거
(주기)       → 큐 없음          → HTTP POST .../heartbeat  → 실패하면 버림
/battery/*   → 최신 표본         → HTTP POST .../battery    → 2분 주기
```

## 왜 상태머신과 분리했나

`mingky_guide_manager` 안에 넣으면 서버가 느리거나 죽었을 때 재시도 루프가
상태 전이를 지연시킵니다. **로봇이 서버 때문에 멈추는 구조**가 됩니다.

같은 이유로 HTTP 호출을 ROS 콜백 안에서 하지 않습니다. 콜백은 큐에 쓰기만
하고(수 ms), 별도 스레드가 전송합니다.

## 왜 로컬 큐가 필요한가

ROS 토픽은 지나가면 끝입니다. 게이트웨이가 늦게 뜨거나 서버가 죽어 있으면
그동안의 이벤트를 통째로 잃습니다.

SQLite 를 쓰는 이유는 셋입니다.

- 파이썬 표준 라이브러리라 의존성이 안 늘어납니다
- 트랜잭션이 있어 "보냈다고 지웠는데 실제로는 실패" 가 안 생깁니다
- 파일이라 **로봇이 재부팅해도 남습니다**

이 큐가 있어야 `event_id` 를 UUID 로 둔 설계가 값을 합니다. 두절 동안 쌓아뒀다
몰아 보내고, 중복은 서버가 `ON CONFLICT (event_id) DO NOTHING` 으로 거릅니다.

## 실행

```bash
ros2 run mingky_event_gateway event_gateway --ros-args \
  -p backend_url:=http://192.168.0.10:8000
```

## 파라미터

| 이름 | 기본값 | 설명 |
| --- | --- | --- |
| `backend_url` | `http://192.168.0.10:8000` | 관제 서버 주소 |
| `queue_path` | `~/.mingky/event_queue.db` | 로컬 큐 파일 |
| `batch_size` | `100` | 한 번에 보낼 최대 건수 |
| `flush_interval_sec` | `2.0` | 평상시 전송 주기 |
| `http_timeout_sec` | `5.0` | HTTP 타임아웃 |
| `max_queue_rows` | `50000` | 큐 상한. 넘으면 오래된 것부터 버림 |
| `max_backoff_sec` | `60.0` | 재시도 백오프 상한 |
| `robot_id` | `pinky-01` | heartbeat·배터리 대상. `robots` 테이블에 있어야 함 |
| `heartbeat_interval_sec` | `5.0` | 생존 신호 주기. `0` 이면 보내지 않음 |
| `heartbeat_timeout_sec` | `2.0` | heartbeat HTTP 타임아웃 |
| `heartbeat_session_cancel_after_sec` | `30.0` | 안내 중 연속 실패 시 로컬 세션 취소·정지까지 기다릴 시간 |
| `battery_interval_sec` | `120.0` | 배터리 저장 주기. 첫 표본은 즉시 전송, `0`이면 끔 |

`max_queue_rows` 는 디스크가 차서 로봇이 멈추는 것보다 오래된 이벤트를
버리는 쪽이 낫다는 판단입니다.

## heartbeat 는 왜 큐를 안 타나

이벤트와 요구가 정반대이기 때문입니다.

| | 이벤트 | heartbeat |
| --- | --- | --- |
| 잃으면 | 기록이 사라짐 | 다음 주기가 곧 재시도 |
| 늦게 도착하면 | 문제없음 (`occurred_at` 이 있음) | **거짓말이 됨** |
| 그래서 | 큐에 쌓고 될 때까지 재전송 | 큐 없이 보내고 실패하면 버림 |

heartbeat 를 큐에 넣으면 두절 동안 쌓였다가 복구 순간 "10분 전 나
살아있었음" 이 한꺼번에 도착합니다. 서버가 두절을 판정할 수 없게 됩니다.

전송 스레드와도 분리되어 있습니다. 큐가 밀려 백오프 중일 때도 생존 신호는
계속 나가야 합니다.

`robot_id` 가 서버에 없으면 `404` 가 오고, 재시도해도 결과가 같으므로
ERROR 로그를 남깁니다. 그 경우 `robot_id` 파라미터와
`database/seeds/002_robots.sql` 을 확인하세요.

판정 임계값은 서버 쪽 설정입니다 (`backend/README.md`). 기본 15초라
5초 주기면 3회 연속 유실에 두절로 잡힙니다.

안내 중에는 별도의 30초 안전 임계값도 적용합니다. 연속 실패가 이 값을
넘으면 Guide Manager가 현재 안내를 종료하고 Emergency Stop이 Nav2 목표와
모터 출력을 정지합니다. 연결이 돌아와도 자동으로 주행을 재개하지 않습니다.

## 실패 처리

| 응답 | 동작 |
| --- | --- |
| 2xx | 큐에서 제거. 남은 게 있으면 곧바로 다음 배치 |
| 4xx (408·429 제외) | **폐기하고 ERROR 로그.** 재시도해도 결과가 같습니다 |
| 5xx · 네트워크 실패 | 큐에 남기고 지수 백오프로 재시도 |

4xx 를 폐기하는 이유는, 잘못된 이벤트 하나가 큐를 영원히 막으면 **그 뒤
이벤트가 전부 못 나가기** 때문입니다. 서버가 미등록 코드를 200 으로
받아주는 것과 같은 이유입니다 — 무한 재전송을 막는 게 우선입니다.

서버 응답의 `unknown_codes` 와 `rejected_updates` 는 로그로 남깁니다.

- `unknown_codes` → `config/event_codes.yaml` 갱신 누락
- `rejected_updates` → 로봇 시계가 어긋났을 가능성

## 검증

백엔드를 껐다 켜서 이벤트를 잃지 않는지 확인합니다.

```bash
# 1) 정상 상태 — 이벤트가 바로 DB 까지 간다
ros2 topic pub --once /guide_manager/start_session std_msgs/String "{data: 'p001'}"

# 2) 백엔드를 내리고 이벤트를 몇 건 발행 — 큐에 쌓인다
python3 -c "import sqlite3; print(sqlite3.connect('$HOME/.mingky/event_queue.db')
  .execute('SELECT count(*) FROM pending').fetchone()[0])"

# 3) 백엔드를 다시 올리면 백오프가 지난 뒤 자동으로 전송된다
```

로그에 `전송 실패, N초 뒤 재시도 (대기 M건)` 이 보이면 큐가 동작 중입니다.
