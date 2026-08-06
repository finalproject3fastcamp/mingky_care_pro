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
- `POST /qr/scan` — QR 스캔 → 안내 세션 시작 + 오늘 진료 일정 조회
- `POST /events` — 로봇 이벤트 배치 적재
- `GET /events` — 이벤트 타임라인 (필터 · 페이지네이션)
- `GET /sessions/active` — 진행 중인 안내 목록
- `GET /sessions/{id}` — 세션 상세 (끝난 세션 포함)
- `GET /robots` — 로봇 목록 + 최근 배터리 + 활성 세션
- `GET /patients/{patient_id}/photo` — 환자 프로필 사진 (`image/*`, private 캐시)
- `GET /docs` — OpenAPI 문서

### 예시

```bash
curl -X POST http://localhost:8000/qr/scan \
  -H 'Content-Type: application/json' \
  -d '{"patient_id":"p001","robot_id":"pinky-01","marker_id":20}'
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
{ "received": 1, "inserted": 1, "duplicates": 0, "state_updates": 1,
  "unknown_codes": [], "rejected_updates": [] }
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


## QR 스캔과 세션

`POST /qr/scan` 만 요청-응답 방식입니다. 로봇이 `session_id` 를 즉시 받아야
이후 발행하는 모든 이벤트에 달고 다닐 수 있는데, 이벤트는 단방향이라 응답을
줄 수 없기 때문입니다. 상태를 바꾸는 경로는 여전히 하나이고, 세션 생성도
여기서만 일어납니다.

스캔 시 두 가지가 만들어집니다.

1. `guidance_sessions` 행 — `robot_id` 가 필요해서 요청에 함께 받습니다
2. `session_steps` — `examination_steps`(마스터)를 **복사**합니다

두 번째가 중요합니다. 마스터를 조회 때마다 조인하면 나중에 검사 순서가
바뀔 때 **과거 안내 기록의 일정까지 소급해서 달라집니다.**

### 응답 코드

| 코드 | 상황 |
| --- | --- |
| `200` | 세션 생성. **같은 로봇의 재스캔이면 기존 세션을 그대로 반환** |
| `404` | 등록되지 않은 `patient_id` |
| `409` | 다른 로봇이 이 환자를 안내 중이거나, 이 로봇이 다른 환자를 안내 중 |
| `422` | `robot_id` 누락, `marker_id` 범위 밖 등 |

재스캔이 `200` 인 것은 의도입니다. 로봇이 재부팅해도 같은 세션으로
복귀할 수 있어야 합니다.

`409` 는 `003` 의 부분 유니크 인덱스가 어차피 막지만, 미리 걸러야 원인이
드러납니다. 걸러내지 않으면 `INSERT` 가 터져 `500` 이 나갑니다.

### 제약 위반과 `rejected_updates`

시계가 어긋난 로봇이 `session.ended` 를 세션 시작보다 앞선 시각으로 보내면
`003` 의 `CHECK (ended_at >= started_at)` 에 걸립니다.

이때 배치 전체를 실패시키면 게이트웨이가 같은 배치를 무한히 재전송합니다.
그래서 상태 갱신을 **세이브포인트** 안에서 실행하고, 위반이 나면 이벤트는
남긴 채 갱신만 건너뛴 뒤 응답의 `rejected_updates` 로 알립니다.
제약 자체는 완화하지 않습니다 — 그게 시계 이상을 잡아주는 안전망입니다.


## 조회 API

### `GET /sessions/active`

의료진 화면(`/medical`)이 주기적으로 읽습니다. 진행 중인 세션과 각 세션의
단계를 함께 돌려줍니다.

**`age` 는 컬럼이 아닙니다.** `002` 에서 지웠고 `birth_date` 에서 계산합니다.

```sql
date_part('year', age(p.birth_date))::int AS age
```

저장해두면 시간이 지나면서 생년월일과 갈라집니다. 실제로 초기 시드에
`age = 50` 으로 박혀 있던 환자가 계산해보니 49세였습니다.

세션마다 단계를 따로 조회하면 N+1 이 되므로, 활성 세션의 단계를 한 번에
가져와 파이썬에서 나눕니다.

### `GET /events`

엔지니어 화면(`/engineer`)의 타임라인입니다.

| 파라미터 | 설명 |
| --- | --- |
| `robot_id` | 로봇별 |
| `session_id` | 세션별 |
| `min_level` | `info` \| `warning` \| `error`. **그 이상만** 남깁니다 |
| `code_prefix` | 접두사. 예: `nav.` `session.` |
| `source_node` | 발행 노드별 |
| `since` / `until` | 발생 시각 구간 |
| `limit` / `offset` | 기본 100, 최대 500 |

정렬은 `occurred_at DESC` 입니다. **도착 순서가 아니라 발생 순서**입니다 —
두절 후 몰아 들어온 이벤트가 화면 맨 위로 올라오면 타임라인이 거짓이 됩니다.

`min_level` 은 텍스트 컬럼이라 그대로 비교할 수 없어, 쿼리에서 순서를
부여해 비교합니다.

### `GET /robots`

배터리는 **저장된 마지막 표본**이지 실시간 값이 아닙니다. 화면에 띄우는
실시간 상태는 DB 에 저장하지 않기로 했습니다 — 3~5초마다 덮어쓰는 값을
영속화할 이유가 없습니다. 여기서는 2분 주기로 쌓이는 `robot_battery_log`
의 최신 행을 씁니다.

활성 세션 조인이 행을 늘리지 않는 것은 `003` 의 부분 유니크 인덱스가
로봇당 활성 세션을 하나로 보장하기 때문입니다.
