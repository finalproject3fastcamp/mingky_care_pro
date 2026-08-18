# 약국 조제 화면 — 데이터

시연용 데이터 파일이다. **화면 코드는 여기 없다** — 관제 백엔드·프론트로 이관됐다.

| 위치 | 무엇 |
|---|---|
| `backend/app/pharmacy.py` | 상태·워커·SSE 브로드캐스터 (원래 Flask `app.py`) |
| `backend/app/routers/pharmacy.py` | HTTP 엔드포인트 |
| `frontend/src/routes/PharmacyDashboard.tsx` | React 화면 (원래 `templates/index.html`) |
| `frontend/src/lib/pharmacyApi.ts` | 프론트 API 클라이언트 |

## 실행

```bash
# 백엔드
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload

# 프론트
cd frontend && npm run dev
# → http://localhost:5173/pharmacy
```

## 데이터

**환자·병명·약품·처방 조합은 관제 DB (`patients` · `conditions` · `medications` ·
`condition_medications`) 에서 읽는다.** `database/seeds/001_initial_data.sql`
이 시드한 그 테이블이 정본이다 — QR 로 등록된 환자가 약국에서도 그대로 검색된다.

환자를 추가하려면 시드 SQL 을 고치고 다시 로드하면 된다. 별도 pharmacy 전용
파일은 없다.

| 파일 | 내용 |
|---|---|
| `policies.json` | 조제에 쓸 수 있는 학습 정책 목록 (pharmacy 전용 · DB 와 무관) |

## DB 에 없는 시연용 텍스트

약 성분·복용법·담당의·특이사항 은 시드 SQL 에 컬럼이 없다. `backend/app/pharmacy.py`
상단의 `_INGREDIENT` · `_DOSAGE` · `_DOCTOR` · `_NOTES` dict 에 병명/환자 id
키로 채워 둔다. 실제 시스템으로 가면 마이그레이션으로 컬럼을 옮긴다.

## 실제 로봇 모드

`PHARMACY_REAL=1` 환경 변수로 켠다. 학습된 정책과 로봇이 있어야 하므로 조제
담당자 환경에서만 된다.

```
필요한 것
  ~/omx_pill_project/         정책 체크포인트와 run.sh
  ~/venv/il                   lerobot v0.4.4
  /dev/omx_follower           로봇팔
  top / wrist 카메라          /dev/v4l/by-id/ 고정 경로
```

없으면 자동으로 시뮬레이션으로 떨어지지 않고 **오류를 표시**한다 — 로봇이
없는데 있다고 착각하는 것보다 낫기 때문이다.

## API

| 메서드 | 경로 | 하는 일 |
|---|---|---|
| GET | `/pharmacy/patients?q=` | 이름·생년월일·환자ID·병명으로 검색 |
| GET | `/pharmacy/prescriptions` | 약품과 병명별 조합 |
| GET | `/pharmacy/random-prescriptions` | 모든 처방의 색 조합·순서를 새로 뽑는다 |
| GET | `/pharmacy/policies` | 쓸 수 있는 학습 정책 |
| GET | `/pharmacy/tray` | 트레이의 색깔별 알약 개수 |
| POST | `/pharmacy/dispense` | 조제 시작 — `{환자, 처방코드, 조합, 정책}` |
| POST | `/pharmacy/stop` | 진행 중인 조제 중단 |
| POST | `/pharmacy/pack` | 포장 단계 실행 |
| POST | `/pharmacy/reset` | 상태 초기화 (조제 중이면 거절) |
| GET | `/pharmacy/state` | 현재 상태와 단계 |
| GET | `/pharmacy/progress` | 진행 상황 스트림 (SSE, fan-out) |

**조제 요청은 `조합` 을 함께 보내야 한다.** 처방코드만 보내면 서버의 원본
조합을 쓰므로, 무작위로 뽑은 순서가 무시된다.

## 무작위 처방에 걸린 제약

시연에서 "순서를 미리 정해 두지 않았다" 를 보이는 기능인데, 아무 조합이나
뽑으면 로봇이 실패한다. 두 가지를 지킨다.

```
① 트레이에 실제로 있는 색에서만 뽑는다
   없는 색을 처방하면 로봇이 찾지 못해 조제가 끝나지 않는다

② 개수는 학습 분포를 따른다
   3개 64.6% / 2개 24.0% / 1개 11.5% / 4개 이상 0%
   4개 이상은 촬영한 적이 없어 정책이 다루지 못한다
```

## 포장 단계

`_pack_worker()` 가 4단계(봉투 준비 → 약 투입 → 라벨 인쇄 → 밀봉)를 시간만
흘려보낸다. **실제 하드웨어가 붙으면 이 함수만 바꾸면 되도록** 분리했다.
