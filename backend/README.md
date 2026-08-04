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

- `GET /health` — 헬스체크
- `POST /qr/scan` — QR 스캔 → 오늘 진료 일정 조회
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
├── main.py       FastAPI 앱 · lifespan · 라우터 등록
├── config.py     환경 변수 로딩
├── db.py         asyncpg 풀 관리
├── schemas.py    pydantic 모델
└── routers/      엔드포인트별 라우터
```
