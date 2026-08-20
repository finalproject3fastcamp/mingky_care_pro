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
| `count_tray.py` | 트레이 알약 계수 브리지 — 백엔드가 il venv 파이썬으로 띄운다 |
| `pack_run.py` | 포장(약통 → 봉투) 헤드리스 러너 — 같은 방식으로 띄운다 |

## DB 에 없는 시연용 텍스트

약 성분·복용법·담당의·특이사항 은 시드 SQL 에 컬럼이 없다. `backend/app/pharmacy.py`
상단의 `_INGREDIENT` · `_DOSAGE` · `_DOCTOR` · `_NOTES` dict 에 병명/환자 id
키로 채워 둔다. 실제 시스템으로 가면 마이그레이션으로 컬럼을 옮긴다.

## 실제 로봇 모드

`PHARMACY_REAL=1` 환경 변수로 켠다. 학습된 정책과 로봇이 있어야 하므로 조제
담당자 환경에서만 된다.

```
필요한 것
  ~/omx_pill_project/         정책 체크포인트 · run.sh · pharmacy.py
  ~/venv/il                   lerobot v0.4.4
  /dev/omx_follower           로봇팔
  top / wrist 카메라          /dev/v4l/by-id/ 고정 경로
```

| 환경 변수 | 기본값 | 무엇 |
|---|---|---|
| `PHARMACY_REAL` | `0` | `1` 이면 트레이·조제 모두 실제 로봇 파트를 쓴다 |
| `OMX_PILL_ROOT` | `~/omx_pill_project` | 조제 파트 경로 (`pharmacy.py` · `run.sh` 가 있는 곳) |
| `OMX_PYTHON` | `~/venv/il/bin/python` | 조제 파트를 돌릴 파이썬 (lerobot v0.4.4) |

```bash
cd backend && PHARMACY_REAL=1 uvicorn app.main:app
```

없으면 자동으로 시뮬레이션으로 떨어지지 않고 **오류를 표시**한다 — 로봇이
없는데 있다고 착각하는 것보다 낫기 때문이다.

### 트레이 계수는 왜 별도 프로세스인가

트레이를 세는 것은 조제 파트의 `pharmacy.count_pills()` 다. 그런데 그 모듈은
import 만 해도 `run_policy` → lerobot · torch · cv2 를 끌어오고, 관제 백엔드
venv 에는 그 스택이 없다 (`backend/requirements.txt` 는 FastAPI · asyncpg 뿐).
조제 노트북에서만 되는 것을 관제 서비스 전체의 설치 조건으로 만들 수 없다.

그래서 조제(`run.sh`)와 같은 방식을 쓴다 — `count_tray.py` 를 `OMX_PYTHON` 으로
띄우고 stdout 의 `TRAY_JSON` 한 줄만 읽는다. 손으로 확인할 때도 같은 명령이다.

```bash
~/venv/il/bin/python omx/web/count_tray.py --root ~/omx_pill_project --frames 5
TRAY_JSON {"개수": {"red": 2, "yellow": 1, "green": 3}}
```

**조제 중에는 트레이를 읽지 않는다.** top 카메라는 V4L2 라 같은 장치가 두 번
열리지 않아서, 읽으려 들면 돌고 있는 조제가 죽는다. 화면의 "다시 확인" 은 그
동안 오류를 돌려준다.

밝기가 한 자릿수면(모든 픽셀 0) `top 카메라가 검은 화면만 줍니다` 가 뜬다.
USB 를 다시 꽂고, 자동절전을 끈다 — [omx/il/TASK.md](../il/TASK.md) 2절.

## 화면 흐름 — 트레이 연결이 첫 관문

약국 화면은 열리자마자 `/pharmacy/tray` 를 한 번 읽는다. **연결이 확인되기
전에는 조제를 시작할 수 없다** — 환자·처방·정책을 다 고른 뒤에 카메라가 죽어
있는 것을 알게 되면 고른 것을 다 버리게 되고, 실제 모드에서는 로봇이 빈 트레이를
뒤지다 제한 시간을 태우기 때문이다.

```
확인 필요 ─(트레이 확인)─→ 연결됨 ─→ 환자·처방·정책 선택 ─→ 조제 시작
              └─────────→ 연결 실패 ─(다시 확인)─┘
```

트레이 카드 제목 옆 배지가 상태를 그대로 보여주고, "조제 시작" 이 막히는 이유는
버튼 옆에 뜬다.

| 트레이 상태 | 버튼 옆 안내 |
|---|---|
| 아직 안 읽음 | 트레이 연결을 먼저 확인하세요 |
| 읽는 중 | 트레이를 확인하는 중입니다 |
| 오류 | 트레이가 연결되지 않았습니다 — 다시 확인하세요 |
| 연결됨 · 처방 색이 트레이에 없음 | 트레이에 빨강 알약이 없습니다 |

무작위로 다시 뽑기도 같은 관문을 지난다 — 트레이에 있는 색에서만 뽑기 때문이다.
**실제 모드에서는 서버도 조제 시작 직전에 한 번 더 트레이를 읽는다.** 화면이
들고 있는 값은 읽은 시각의 것이라, 그 사이 알약을 집어 갔을 수 있다.

## API

| 메서드 | 경로 | 하는 일 |
|---|---|---|
| GET | `/pharmacy/patients?q=` | 이름·생년월일·환자ID·병명으로 검색 |
| GET | `/pharmacy/prescriptions` | 약품과 병명별 조합 |
| GET | `/pharmacy/random-prescriptions` | 모든 처방의 색 조합·순서를 새로 뽑는다 |
| GET | `/pharmacy/policies` | 쓸 수 있는 학습 정책 |
| GET | `/pharmacy/tray` | 트레이의 색깔별 알약 개수 (실제 모드는 카메라를 열어 몇 초 걸린다) |
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

## 포장 단계 — 조제와 다른 로봇, 다른 스위치

OMX 작업은 둘로 나뉘어 있고 **서로 다른 노트북에서 다른 모델로** 만들어졌다.
한쪽만 갖춘 자리가 정상이라 스위치를 묶지 않는다 — `PHARMACY_REAL` 로 포장까지
켜면, 조제 파트가 없는 자리에서 포장을 쓰려다 같이 실패한다.

| | 조제 (알약 하나씩 집기) | 포장 (약통 → 봉투) |
|---|---|---|
| 스위치 | `PHARMACY_REAL=1` | `PACK_REAL=1` |
| 코드 | `~/omx_pill_project` (저장소 밖) | `pack_run.py` (여기) |
| 정책 | `policies.json` 의 `pill_v3` 계열 | `~/train/act_pill_bottle_v1` |
| 백엔드 | `_run_sequence()` | `_run_pack()` |

`_pack_worker()` 의 4단계 중 **로봇이 하는 것은 "약 투입" 하나다.** 학습된 작업이
"약통을 집어 봉투에 넣기" 뿐이라([../il/TASK.md](../il/TASK.md)), 봉투 준비·라벨
인쇄·밀봉은 실제 모드에서도 시뮬레이션이다. 넷 다 진짜인 것처럼 보이게 만들지
않는다.

| 환경 변수 | 기본값 | 무엇 |
|---|---|---|
| `PACK_REAL` | `0` | `1` 이면 "약 투입" 을 실제 로봇이 한다 |
| `PACK_DRY_RUN` | `0` | 로봇·카메라에 붙어 추론까지 하되 **행동을 보내지 않는다** |
| `PACK_CKPT` | `~/train/act_pill_bottle_v1/checkpoints/last/pretrained_model` | 정책. 로컬 경로 또는 HF Hub repo id |
| `PACK_SECONDS` | `60` | 에피소드 **상한** (05_record.sh 가 60초로 찍었다). 팔이 학습 시작 자세로 돌아오면 그 전에 끊는다 |
| `PACK_NO_EARLY_STOP` | `0` | `1` 이면 홈 복귀 감지를 끄고 상한을 끝까지 채운다 |

```bash
# 리허설 — 팔이 움직이지 않는다. 배선을 확인할 때.
cd backend && PACK_REAL=1 PACK_DRY_RUN=1 PACK_SECONDS=6 uvicorn app.main:app

# 실기 — 팔이 실제로 움직인다.
cd backend && PACK_REAL=1 uvicorn app.main:app
```

### 다른 노트북에서 포장을 돌리려면

**체크포인트는 저장소에 없다.** `policies.json` 이 조제 정책을 HF Hub repo id 로
참조하는 것과 같은 규칙이다 — 200MB 짜리 바이너리가 git 에 들어가면 앞으로 모든
clone 이 그 값을 치르고, 모델을 새로 학습할 때마다 히스토리가 그만큼 불어난다.

옮길 것은 **`pretrained_model` 하나, 약 200MB** 다.

```
~/train/act_pill_bottle_v1/
└── checkpoints/last/
    ├── pretrained_model/   198M  ← 이것만 필요 (추론에 쓰는 전부)
    └── training_state/     394M  ← 학습 재개용. 안 옮겨도 된다
```

`PACK_CKPT` 는 **로컬 경로와 HF Hub repo id 를 모두 받는다.** 자리가 셋 이상이 되면
허브에 올려 두는 쪽이 편하다 (`06_train.sh` 는 기본이 `push_to_hub=false` 다).

```bash
PACK_CKPT=~/train/act_pill_bottle_v1/checkpoints/last/pretrained_model   # 로컬
PACK_CKPT=mingky/pill_bottle_v1_act                                     # 허브
```

옮기는 쪽에서 확인할 것 — **팔마다·머신마다 다른 것들이라 저장소가 들고 갈 수 없다.**

| | 어떻게 |
|---|---|
| lerobot v0.4.4 (`~/venv/il`) | `omx/il/01_install.sh` (20~40분). 조제를 학습시킨 자리면 이미 있다 |
| `/dev/omx_follower` 이름 고정 | `omx/il/02_find_ports.sh` (sudo) |
| **그 팔의 캘리브레이션** | `lerobot-calibrate`. 남의 팔 것을 쓰면 관절 영점이 어긋나 엉뚱한 데로 간다 |
| `omx/il/cams.env` | `omx/il/03_check_cameras.sh --view`. gitignore 대상이다 (by-id 경로가 머신마다 다르다) |
| 학습 데이터셋 (921MB) | **필요 없다.** `09_compare_view.py` 로 화각을 대조할 때만 쓴다 |

준비됐으면 프리플라이트로 한 번에 확인한다. 아무것도 움직이지 않는다.

```bash
./.claude/skills/run-local/preflight-omx.sh
~/venv/il/bin/python omx/web/pack_run.py --dry-run --seconds 3   # 팔 무동작
```

### 왜 `omx/il/07_run.sh` 를 쓰지 않는가

같은 정책을 돌리지만 그쪽은 **평가용**이라 웹 백엔드가 부를 수 없다.

- `read -r _` 로 엔터를 기다린다 — 헤드리스로 못 붙인다
- `lerobot-record` 라서 실행할 때마다 평가 데이터셋이 쌓인다
- 로컬 패치가 첫 에피소드 앞에 리셋 대기를 넣어 → 를 누를 때까지 서 있는다
- 스페이스바 DAgger 개입 · `--display_data=true` 전제
- 진행 문구를 찍지 않아 화면에 올릴 것이 없다

`pack_run.py` 는 같은 정책을 **키 입력 없이 한 번만** 돌리고, 데이터셋을 남기지
않으며, `PACK_JSON` 한 줄씩으로 진행을 알린다 (`count_tray.py` 의 `TRAY_JSON` 과
같은 방식). 배선을 의심할 때는 단독으로 돌려 범위를 좁힌다.

```bash
~/venv/il/bin/python omx/web/pack_run.py --dry-run --seconds 3
PACK_JSON {"단계": "정책 로드"}
PACK_JSON {"단계": "로봇 연결"}
PACK_JSON {"단계": "약 투입", "진행": 0.347}
PACK_JSON {"완료": true, "초": 3.2, "dry_run": true}
```

진행률은 **시간 기준**이다. ACT 는 "끝났다" 나 성공 여부를 내놓지 않기 때문에,
화면에는 진행 표시로만 쓰고 성공 판정은 사람이 한다
([../il/TASK.md](../il/TASK.md) 의 성공 기준).

`_run_pack()` 이 이 줄을 `포장진행` SSE 이벤트로 올리면 진행 상황 카드에 막대가
찬다. **알림 로그에는 넣지 않는다** — 초당 한 줄씩 쌓이면 나머지 기록이 밀려난다.
`정책 로드`·`로봇 연결` 둘만 로그로 간다 (첫 실행은 torch 로딩에 수십 초 걸려서
아무 소식이 없으면 멈춘 것으로 보이기 때문이다).

### 상한을 채우지 않고 끊는다

`PACK_SECONDS` 는 상한이지 소요 시간이 아니다. 실제로 40초에 끝난 작업이 60초를
채우는 동안 **팔은 다 하고 서 있고 화면만 기다리는** 구간이 생겨서, 러너가 팔이
학습 시작 자세(`10_home.py` 의 `HOME`)로 돌아오는 것을 보고 끊는다.

```
홈에서 15 이상 벗어남   →  작업을 시작했다
다시 8 이내로 들어옴     →  돌아왔다
그 상태로 2초 유지       →  지나가는 길이 아니다 → 끝
```

세 조건을 다 거는 이유는 **시작하자마자 끝나는 것**과 **중간에 홈을 스쳐 지나가는
것**을 걸러야 하기 때문이다. 완료 줄의 `최대이탈` 로 임계값을 조정한다 — 이 값이
`--leave-margin` 을 넘지 못하면 감지가 아예 시작되지 않은 것이다.

**성공 판정이 아니다.** 약통을 놓치고 돌아와도 똑같이 끝난다. 없애는 것은 꼬리
시간뿐이고, 성공 여부는 여전히 사람이 본다.
