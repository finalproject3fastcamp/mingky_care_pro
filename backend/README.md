# Backend

Mingky Care 관제 시스템의 FastAPI 백엔드.

## 스택

- Python 3.12
- FastAPI
- asyncpg (PostgreSQL)

기술 스택 배경은 [`../docs/monitoring-spec.md`](../docs/monitoring-spec.md) 2장 참고.

## 실행

DB는 먼저 [`../database/`](../database/) 에서 `.env` 를 만들고 `docker compose up -d` 로 띄워둔다.
백엔드는 별도 `.env` 를 두지 않고 `../database/.env` 를 읽어 `DATABASE_URL` 을 조립한다.

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

기본 접속: <http://localhost:8000>

- `GET /health` — 헬스체크. 로드된 이벤트 코드 수도 함께 반환
- `POST /qr/scan` — QR 스캔 → 오늘 진료 일정 조회
- `POST /events` — 로봇 이벤트 배치 적재
- `GET /docs` — OpenAPI 문서

### 예시

```bash
curl -X POST http://localhost:8000/qr/scan \
  -H 'Content-Type: application/json' \
  -d '{"patient_id":"p001"}'
```

## 환경 변수

`../database/.env` 에서 로드한다. 아래 4개는 필수, `POSTGRES_HOST` 는 옵션(기본 `localhost`).

| 키 | 설명 |
| --- | --- |
| `POSTGRES_USER` | DB 사용자 |
| `POSTGRES_PASSWORD` | DB 비밀번호 |
| `POSTGRES_DB` | DB 이름 |
| `POSTGRES_PORT` | DB 포트 |
| `POSTGRES_HOST` | DB 호스트 (기본 `localhost`) |

## 디렉터리

```
app/
├── main.py        FastAPI 앱 · lifespan · 라우터 등록
├── config.py      환경 변수 로딩
├── db.py          asyncpg 풀 관리
├── schemas.py     pydantic 모델
├── event_codes.py config/event_codes.yaml 로드와 검증
├── registry.py    이벤트 코드 정본을 앱 전체에서 공유
├── ingest.py      이벤트 적재와 상태 갱신
└── routers/       엔드포인트별 라우터
```


## 이벤트 수집

### 정본은 `config/event_codes.yaml`

발행 가능한 `event_code` 목록과 `payload` 형태를 그 파일이 정의합니다.
서버는 기동 시 읽고, 없거나 깨졌으면 **뜨지 않습니다.**
검증 없이 기동하면 미등록 코드가 조용히 쌓여 그쪽이 더 나쁩니다.

경로는 `EVENT_CODES_FILE` 환경 변수로 바꿀 수 있고,
기본값은 저장소 루트의 `config/event_codes.yaml` 입니다.

### `POST /events`

게이트웨이가 네트워크 두절 동안 쌓아둔 이벤트를 몰아 보내므로 배열로 받습니다.

```json
[
  {
    "event_id": "6727f46d-d385-4982-8170-26f322719eeb",
    "robot_id": "pinky-01",
    "session_id": 3,
    "occurred_at": "2026-08-04T09:01:42Z",
    "level": "info",
    "event_code": "nav.goal_succeeded",
    "source_node": "guide_manager",
    "payload": { "visit_name": "X-ray" }
  }
]
```

```json
{ "received": 1, "inserted": 1, "duplicates": 0,
  "state_updates": 1, "unknown_codes": [] }
```

### 적재 규칙

`config/event_codes.yaml` 3절의 규칙을 `app/ingest.py` 가 구현합니다.

1. **배치를 `occurred_at` 순으로 정렬해 적용합니다.**
   두절 후 몰아 보내면 도착 순서가 발생 순서와 다를 수 있습니다.
   `session_steps` 의 `CHECK (completed_at IS NULL OR arrived_at IS NOT NULL)`
   가 순서 오류를 거부하는데, 이 제약은 안전망이므로 완화하지 않습니다.

2. **적재와 상태 갱신이 같은 트랜잭션입니다.**
   따로 커밋되면 이벤트만 남고 상태가 안 바뀌는 구멍이 생깁니다.

3. **재전송에 멱등합니다.**
   `ON CONFLICT (event_id) DO NOTHING` 으로 중복 적재를 막고, 새로 들어온
   이벤트에 대해서만 상태를 갱신합니다. `UPDATE` 에도 `IS NULL` 조건을 걸어
   두 겹으로 막습니다.

4. **미등록 코드를 거부하지 않습니다.**
   그대로 적재한 뒤 `system.unknown_event_code` 를 추가로 남기고, 응답의
   `unknown_codes` 로 알립니다. HTTP 는 200 입니다 — 거부하면 게이트웨이가
   같은 배치를 무한히 재전송하게 됩니다.

`nav.goal_succeeded` 는 `payload.visit_name` 으로 단계를 찾지 않습니다.
한 세션에서 같은 장소를 두 번 방문할 수 있어(진료실 초진·판독) 이름만으로는
어느 단계인지 결정되지 않기 때문에, **아직 도착하지 않은 가장 이른 단계**를
현재 단계로 봅니다.
