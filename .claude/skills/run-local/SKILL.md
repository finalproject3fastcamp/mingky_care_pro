---
name: run-local
description: 로컬 개발 스택(PostgreSQL · FastAPI 백엔드 · React 대시보드)을 띄우고 확인한다. "대시보드 띄워줘", "백엔드 실행", "로컬에서 확인해줘", "앱 켜줘" 같은 요청과, 변경 사항이 실제 화면에서 동작하는지 봐야 할 때 사용한다. docker 없이 root 권한 없이 동작한다.
---

# 로컬 스택 실행

3층이 다 떠야 화면에 데이터가 보인다. 하나라도 빠지면 대시보드는 뜨되
"일부 데이터를 갱신하지 못했습니다" 경고와 함께 로봇 0대로 나온다.

| 층 | 주소 | 비고 |
| --- | --- | --- |
| 대시보드 (Vite) | <http://localhost:5173> | `/api` → :8000 프록시 |
| 백엔드 (FastAPI) | <http://localhost:8000> · `/docs` | 인스턴스 1개만 |
| PostgreSQL | `127.0.0.1:5432` | `mingky_care` |

## 실행

```bash
./.claude/skills/run-local/start.sh            # 전부
./.claude/skills/run-local/start.sh db         # DB 만 (backend|frontend 도 가능)
./.claude/skills/run-local/stop.sh             # 전부 종료 (데이터는 유지)
```

멱등하다. 이미 떠 있는 층은 건너뛰고, 마이그레이션·시드도 다시 돌려 안전하다.
최초 실행은 Node 와 PostgreSQL 바이너리를 받느라 몇 분 걸리고, 그 다음부터는
몇 초다.

로그: `~/.local/share/mingky-care-logs/{backend,frontend}.log`,
PostgreSQL 은 `~/.local/share/mingky-care-pg/server.log`.

## 확인 — 띄우고 끝내지 말 것

```bash
curl -s localhost:8000/health          # {"status":"ok","event_codes":65}
curl -s localhost:8000/robots | head -c 200
```

그다음 브라우저로 실제 화면을 본다. 시드가 들어간 상태의 기준선은 이렇다.

- `/medical` — 핑키 2대 카드. 상단에 노란 경고 배너가 **없어야** 한다.
- `/engineer/fleet` — "로봇 4대" (omx-01·02 manipulator, pinky-01·02 mobile)
- `/pharmacy`, `/engineer/{events,system,waypoints,cameras}`

경고 배너가 보이거나 로봇이 0대면 백엔드나 DB가 빠진 것이다. 프런트 문제로
쫓아가기 전에 `/health` 부터 확인한다.

## 약국 조제 — 시뮬레이션과 실제 모드

`/pharmacy` 하단 "진행 상황" 카드는 **이미 실제 SSE 이벤트로 구동된다.** 배선을
새로 할 것은 없다.

```
로봇 stdout ─"담기 완료"·"다음 목표"·"놓쳤습니다"─▶ _run_sequence  (backend/app/pharmacy.py)
   ─▶ _push({종류:"단계끝"…}) ─▶ SSE /pharmacy/progress
   ─▶ EventSource ─▶ stepItems ─▶ 하단 카드  (frontend/src/routes/PharmacyDashboard.tsx)
```

기본은 시뮬레이션이라 `_dispense_worker` 가 4초짜리 가짜 단계를 흘린다
(`pharmacy.py` 의 `if not REAL_MODE:`). 실제 모드를 켜면 같은 카드가 로봇 로그를
그대로 반영한다. SSE 이벤트 종류는 `frontend/src/lib/pharmacyApi.ts` 의
`ProgressEvent` 유니온이 정본이다.

**조제 쪽은 화면·SSE 를 손댈 것이 없다.** 실제 로봇을 붙일 때 고치는 곳은 백엔드
워커와 그 워커가 띄우는 러너뿐이다. 포장은 예외로 `포장진행` 이벤트가 하나 더
있다 — 아래 "진행 막대" 를 볼 것.

```bash
./.claude/skills/run-local/preflight-omx.sh          # 준비됐는지 먼저 점검
PHARMACY_REAL=1 ./.claude/skills/run-local/start.sh backend
```

`preflight-omx.sh` 는 아무것도 움직이지 않는다 — 서보는 broadcast ping 만,
카메라는 프레임만 읽는다. 기준은 `~/omx_hardware_inventory.md` 의 실측 BOM 이다.
`start.sh` 도 `PHARMACY_REAL=1` 이면 기동 전에 이 점검을 한 번 돌리고, 빠진 게
있으면 뜨지 않는다.

### 조제와 포장은 스위치가 다르다

OMX 작업이 둘로 나뉘어 있고, **서로 다른 노트북에서 다른 모델로** 만들어졌다.
한쪽만 갖춘 자리가 정상이라 스위치를 묶지 않는다.

| | 조제 (알약 하나씩 집기) | 포장 (약통 → 봉투) |
| --- | --- | --- |
| 스위치 | `PHARMACY_REAL=1` | `PACK_REAL=1` |
| 코드 | `~/omx_pill_project` (저장소 밖) | `omx/web/pack_run.py` (저장소 안) |
| 정책 | `omx/web/policies.json` 의 `pill_v3` 계열 | `~/train/act_pill_bottle_v1` (`PACK_CKPT`) |
| 화면 | 진행 상황 카드의 색깔 단계들 | 진행 상황 카드의 마지막 "포장" 단계 + 진행 막대 |
| 백엔드 | `_run_sequence` | `_run_pack` |

**포장 4단계 중 로봇이 하는 것은 "약 투입" 하나다.** 학습된 작업이 "약통을 집어
봉투에 넣기" 뿐이라(`omx/il/TASK.md`), 봉투 준비·라벨 인쇄·밀봉은 실제 모드에서도
시뮬레이션이다. 넷 다 진짜인 것처럼 보이게 만들지 않는다.

#### 진행 막대

"약 투입" 은 `PACK_SECONDS` (기본 60초) 동안 돈다. 그동안 알림에 아무것도 올라가지
않으면 화면이 멈춘 것처럼 보이므로, 러너가 매초 올리는 진행률을 그대로 흘린다.

```
pack_run.py ─PACK_JSON {"단계":"약 투입","진행":0.42}─▶ _run_pack  (backend/app/pharmacy.py)
   ─▶ _push({종류:"포장진행"…}) ─▶ SSE ─▶ packProg ─▶ 진행 상황 카드의 막대
```

**로그가 아니라 막대로만 보낸다** — 초당 한 줄씩 쌓으면 알림의 나머지 기록이 다
밀려난다. 진행률은 **시간 기준**이라 성공 여부가 아니다. ACT 는 "끝났다" 를
알려주지 않으므로 성공 판정은 사람이 한다 (`omx/il/TASK.md` 의 성공 기준).

```bash
# 리허설 — 로봇·카메라에 붙고 추론까지 하지만 팔이 움직이지 않는다.
PACK_REAL=1 PACK_DRY_RUN=1 PACK_SECONDS=6 \
  ./.claude/skills/run-local/start.sh backend

# 실기 — 팔이 실제로 움직인다. 작업대를 비우고 e-stop 을 손 닿는 곳에.
PACK_REAL=1 ./.claude/skills/run-local/start.sh backend
```

| 환경 변수 | 기본값 | |
| --- | --- | --- |
| `PACK_REAL` | `0` | 포장 실제 모드 |
| `PACK_DRY_RUN` | `0` | 행동을 보내지 않는 리허설 |
| `PACK_CKPT` | `~/train/act_pill_bottle_v1/checkpoints/last/pretrained_model` | |
| `PACK_SECONDS` | `60` | 에피소드 **상한**. 팔이 학습 시작 자세로 돌아오면 그 전에 끊는다 |
| `PACK_NO_EARLY_STOP` | `0` | 홈 복귀 감지를 끄고 상한을 끝까지 채운다 |

`pack_run.py` 는 단독으로도 돌릴 수 있다 — 배선을 의심할 때 여기부터 좁힌다.

```bash
~/venv/il/bin/python omx/web/pack_run.py --dry-run --seconds 3
```

### 실제 모드에 필요한 것

| 항목 | 없으면 |
| --- | --- |
| 팔로워 암 `/dev/ttyACM0` + 서보 6개 (ID 11~16) | 조제 불가 |
| top 카메라 `4c4a:4a55` → `/dev/video4` | 트레이 계수·정책 추론 불가 |
| wrist 카메라 `0c45:6367` → `/dev/video6` | 정책이 두 시점을 못 받는다 |
| `~/omx_pill_project` (`run.sh` · `pharmacy.py`) | 백엔드가 부를 대상이 없다 |
| `~/venv/il` (lerobot · torch · cv2) | 위 둘을 띄울 파이썬이 없다 |

조제 파트는 **저장소 밖**이다. 관제 백엔드 venv 에는 lerobot·torch·cv2 가 없고
앞으로도 넣지 않는다 — 경로는 `OMX_PILL_ROOT` · `OMX_PYTHON` 으로 받는다.
리더 암(`/dev/ttyACM1`)은 원격조작 데이터 수집용이라 자율 조제에는 필요없다.

## 이 환경이 문서와 다른 점

`backend/README.md` 와 `database/README.md` 는 docker 와 nvm 을 전제한다. 이
개발 머신에는 **docker·postgres·npm·nvm 이 전부 없고 `sudo` 는 비밀번호를 묻는다.**
그래서 스크립트가 root 없이 되는 경로로 우회한다.

- **Node** — 시스템은 18, Vite 8 은 20.19+ 필요, `.nvmrc` 는 24. 공식 tarball 을
  `~/.local/opt/node-v24.19.0-linux-x64` 에 풀어 쓴다. `nvm use` 는 안 된다.
- **PostgreSQL** — PyPI `pgserver` 휠에 들어 있는 재배치 가능한 바이너리를
  `~/.local/opt/pgserver-venv` 에 설치해 쓴다. 데이터는
  `~/.local/share/mingky-care-pg`.
- **마이그레이션** — 저장소의 `deploy/init-db.sh` 를 그대로 부른다.
  `MINGKY_MIGRATIONS_DIR`·`MINGKY_SEEDS_DIR` 로 경로만 넘기면 되고 접속 정보는
  psql 이 `PGHOST`·`PGPORT`·`PGPASSWORD` 에서 읽는다. 따로 러너를 쓰지 말 것.

### ⚠️ PostgreSQL 버전이 운영과 다르다

`compose.yaml` 은 `postgres:18-alpine` 인데 pgserver 가 주는 건 **16.2** 다.
현재 마이그레이션에는 18 전용 문법이 없어(전부 `GENERATED ALWAYS AS IDENTITY`
수준) 13개가 그대로 적용되지만, 운영과 같은 버전은 아니다. 18 전용 기능
(`uuidv7()`, virtual generated column, `RETURNING OLD` 등)을 쓰는 마이그레이션을
추가하면 여기서 깨진다. 그때는 docker 가 필요하다.

## 함정

- **psql 비밀번호 프롬프트 무한 루프** — `PGPASSWORD` 없이, 또는 TTY 없이
  `psql`/`createdb` 를 부르면 프롬프트가 EOF 를 만나 `Password:` 를 무한히
  찍는다. 반드시 `PGPASSWORD` 를 주고 `</dev/null` 을 붙인다.
- **백엔드는 절대 2개 띄우지 말 것** — `heartbeat`·`arming`·`orders` 가
  인메모리라 판정이 갈린다. advisory lock 으로 막혀 있고 못 잡으면 종료 코드 3.
  `--workers 2`, `WEB_CONCURRENCY=2` 도 전부 걸린다.
- **약국 화면이 열려 있으면 백엔드가 SIGTERM 으로 안 죽는다** — `/pharmacy/progress`
  는 끝나지 않는 SSE 요청이라 uvicorn 이 리스너만 닫고 그 요청을 기다리며
  매달린다. 그동안 DB 세션을 쥔 채라 advisory lock 이 반납되지 않고, 다음 기동이
  "다중 워커/레플리카 감지" 로 **종료 코드 3** 에 걸린다. `stop.sh` 가 10초 기다린
  뒤 `SIGKILL` 로 마무리한다. 수동으로 `pkill` 만 했다면 프로세스가 남았는지
  반드시 확인할 것.
- **백엔드를 재시작하면 열려 있던 약국 화면의 SSE 가 조용히 죽는다** — vite 프록시가
  백엔드가 죽어도 클라이언트 소켓을 붙잡고 있어서, 브라우저는 스트림이 열린 줄
  안다. `onerror` 도 EventSource 자동 재연결도 오지 않고 화면만 영영 멈춘다
  (경고 한 줄 없이). 직접 `:8000` 으로 붙으면 정상적으로 끊긴다 — 프록시만의
  문제다. 지금은 서버가 20초마다 `핑` 이벤트를 보내고 화면이 45초 침묵하면 스스로
  다시 붙는다("진행 상황 연결이 조용히 끊겼습니다"). **keep-alive 를 SSE 주석으로
  되돌리지 말 것** — 주석은 EventSource 가 화면에 넘기지 않아 감지가 불가능해진다.
- **DB를 재시작하면 백엔드가 락을 잠깐 잃는다** — 로그에 "단일 인스턴스 락을
  잃었습니다. 다시 잡습니다." 가 찍히고 10초 안에 스스로 복구한다. 백엔드를
  다시 띄울 필요 없다.
- **`VITE_API_BASE_URL` 에 절대 URL 을 넣지 말 것** — 백엔드에 CORS 설정이 없어
  vite 프록시를 지나치면 브라우저가 막는다. `.env.example` 대로 `/api` 로 둔다.
