# mingky_event_gateway

로봇 이벤트를 관제 서버로 전달합니다.

```
/events 토픽 → 로컬 큐(SQLite) → HTTP POST /events → 성공 시 큐에서 제거
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

`max_queue_rows` 는 디스크가 차서 로봇이 멈추는 것보다 오래된 이벤트를
버리는 쪽이 낫다는 판단입니다.

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
