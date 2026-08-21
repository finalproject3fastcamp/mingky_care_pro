"""약국 조제 화면 — 상태·워커·SSE 브로드캐스터.

Flask 판(`omx/web/app.py`) 을 FastAPI 로 이관한 것. 이유:
  - 관제 백엔드가 이미 FastAPI 라 서비스를 하나로 유지한다
  - 프론트가 `/api/*` 프록시로 이 백엔드와만 통신한다 (vite.config.ts)

## 데이터 원본은 관제 DB

환자·병명·약품·처방 조합은 모두 관제 Postgres (`patients` · `conditions` ·
`medications` · `condition_medications`) 에서 읽는다. `database/seeds/001_initial_data.sql`
이 시드한 그 테이블을 그대로 쓴다. **QR 로 등록된 환자가 여기서도 그대로
보이도록** 정본을 하나로 유지한다.

시드에 없는 시연용 텍스트 (약 성분·복용법·담당의·특이사항) 는 모듈 상단
폴백 dict 에 둔다. 실제 시스템으로 가면 마이그레이션으로 컬럼을 옮긴다.

## 설계상 중요한 점 (원본 Flask 의 계약을 그대로 유지)

  - **모델은 "지정된 알약 하나 집기" 만 안다.** 처방 조합을 순서대로 처리하는 것은
    모델이 아니라 이 서버가 담당한다 — 색마다 pick 을 한 번씩 호출한다.
  - **시뮬레이션이 기본, 실제 모드는 명시적 opt-in.** `PHARMACY_REAL=1` 환경 변수로
    켠다. 실제 모드는 조제 파트(OMX 카메라·로봇팔)와 학습된 정책이 필요해서
    데모 환경에서는 못 돈다.
  - **조제는 한 번에 하나.** 로봇이 한 대뿐이라 동시에 두 조제를 못 돌린다.
  - **트레이 카메라가 검은 화면을 주면 오류로 올린다** — 자동노출 잡히기 전
    프레임을 0개로 판정하면 "알약이 하나도 없다" 가 된다.
  - **화면이 보낸 조합이 언제나 우선.** 무작위로 다시 뽑기 후 서버 원본과
    화면 상태가 다르므로, 조제 요청은 조합을 함께 실어 보내야 한다.

## 실제 조제 파트와의 경계

트레이 계수도 조제 실행도 **저장소 밖의 조제 파트**(`~/omx_pill_project` ·
`OMX_PILL_ROOT`) 를 별도 프로세스로 띄워서 한다. 관제 백엔드 venv 에는 lerobot ·
torch · cv2 가 없고 앞으로도 넣지 않는다 — 조제 노트북에서만 되는 것을 관제
서비스 전체의 설치 조건으로 만들 수 없기 때문이다.

    조제   `run.sh`               (il venv 를 스스로 source 한다)
    트레이 `omx/web/count_tray.py` (il venv 파이썬 `OMX_PYTHON` 으로 띄운다)

두 경로 모두 top 카메라를 쓰는데 V4L2 는 같은 장치를 두 번 열지 못한다. 그래서
조제 프로세스가 살아 있는 동안 트레이 계수는 거절하고, 계수끼리는 잠금으로
직렬화한다.

## SSE 브로드캐스트

Flask 판은 단일 `queue.Queue` 라 브라우저 두 개가 붙으면 이벤트를 나눠 가져갔다.
여기서는 구독자별 `asyncio.Queue` 로 fan-out 해서 여러 화면이 같은 진행 상황을
동시에 본다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

from .db import get_pool

log = logging.getLogger("mingky.pharmacy")

# ── 관제 리포터 (OMX 박스에서만 켜진다) ─────────────────────────────────
# OMX 는 ROS 미사용(domain NULL)이라 게이트웨이가 없다. 이 워커가 조제/포장을
# 실제로 돌리는 프로세스이므로, 여기서 직접 관제(백엔드)로 라이프사이클 이벤트를
# 발행한다. 후크는 `MINGKY_OMX_ROBOT_ID` 가 설정된 OMX 박스에서만 켜진다 —
# env 가 없으면(데모·CI·개발) 아무 것도 나가지 않아 기존 동작·기존 테스트가
# 100% 그대로다.
#
# **pick 성공/실패는 발행하지 않는다.** ACT 정책은 성공 여부를 내놓지 않고
# 진행률은 시간 기준이라, 우리에겐 pick 의 ground truth 가 없다(omx/web/README.md).
# 근거 없는 성공 신호를 지어내지 않는다 — 발행하는 것은 시간으로 증명되는 사이클
# 라이프사이클(started/completed/aborted)과 정책 로드뿐이다.
#
# 리포터는 저장소 밖(omx/report)에 있고 표준 라이브러리만 쓴다. 백엔드 venv 가
# 그 경로를 못 볼 수 있어 저장소 루트를 sys.path 에 넣고 임포트한다. 실패해도
# 후크가 조용히 꺼질 뿐이다.
if str(_PROJECT_ROOT := Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
try:
    from omx.report import reporter as _reporter
except Exception:  # noqa: BLE001
    _reporter = None


def _report_robot_id() -> str | None:
    """OMX 박스에서만 값이 있다. 없으면 후크 전체가 조용히 꺼진다."""
    return os.environ.get("MINGKY_OMX_ROBOT_ID") or None


async def _report(fn_name: str, *args) -> None:
    """리포터 함수를 백그라운드 스레드에서 조용히 호출한다.

    env 미설정이거나 리포터가 없으면 아무 것도 하지 않는다. urllib 은 블로킹이라
    스레드로 밀어 이벤트 루프를 막지 않고, 네트워크 실패는 삼킨다 — 관제 보고가
    조제 워커를 죽이면 안 된다(heartbeat 와 같은 원칙: routers/robots.py).
    """
    rid = _report_robot_id()
    if not rid or _reporter is None:
        return
    try:
        fn = getattr(_reporter, fn_name)
        await asyncio.to_thread(fn, *args, rid=rid)
    except Exception:  # noqa: BLE001
        log.debug("관제 보고 실패(%s) — 무시", fn_name, exc_info=True)


async def _report_policy_loaded(policy_id: str) -> None:
    """선택된 정책의 체크포인트를 관제에 알린다 — '무엇이 돌고 있나'(§4.4).

    조제 정책은 코드 SHA 가 아니라 체크포인트가 버전이다. 색별 정책은 체크포인트가
    색마다 달라 하나로 접어 문자열로 남긴다. dataset_revision 자리에는 정책의 HF
    Hub repo id 를 쓴다 — 저장소가 아는 유일한 '학습 산출물' 참조다.
    """
    if not _report_robot_id() or _reporter is None:
        return
    pol = _POLICIES.get(policy_id) or _POLICIES.get(DEFAULT_POLICY) or {}
    ckpt = pol.get("ckpt")
    repo = pol.get("repo", "")
    if isinstance(ckpt, dict):
        ckpt_str = ",".join(f"{k}={v}" for k, v in sorted(ckpt.items()))
    else:
        ckpt_str = "" if ckpt is None else str(ckpt)
    checkpoint_id = f"{repo}@{ckpt_str}" if repo else ckpt_str
    await _report("policy_loaded", checkpoint_id, repo)

# ── 데이터/모듈 경로 ────────────────────────────────────────────────────
# 학습된 정책 목록만 파일로 남는다 (pharmacy 전용 · 관제 DB 와 무관).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DATA_DIR = _PROJECT_ROOT / "omx" / "web"

# 실제 조제 파트. `pharmacy.py`(트레이 계수) · `run.sh`(정책 실행) · 학습 체크포인트가
# 있는 곳이다. **저장소 안(`omx/`)이 아니라 조제 담당자 노트북의 작업 디렉터리**라
# 환경 변수로 받는다 — 저장소에는 로봇 스크립트와 수 GB 체크포인트가 없다.
_OMX_PROJECT = Path(
    os.environ.get("OMX_PILL_ROOT", str(Path.home() / "omx_pill_project"))
).expanduser()

# 조제 파트를 돌리는 파이썬. lerobot v0.4.4 가 깔린 venv 라 관제 백엔드 venv 와
# 다르다 (`run.sh` 도 이 venv 를 source 한다).
_OMX_PYTHON = Path(
    os.environ.get("OMX_PYTHON", str(Path.home() / "venv" / "il" / "bin" / "python"))
).expanduser()

# 트레이 계수 브리지. 저장소가 들고 있는 스크립트를 _OMX_PYTHON 으로 띄운다.
_TRAY_SCRIPT = _DATA_DIR / "count_tray.py"
_TRAY_MARKER = "TRAY_JSON"

_POL_RAW = json.loads((_DATA_DIR / "policies.json").read_text(encoding="utf-8"))["정책"]
_POLICIES = {p["id"]: p for p in _POL_RAW}

# ── DB → pharmacy 어휘 매핑 ─────────────────────────────────────────────
# DB 는 medications.color 를 한글로("빨간색"), pharmacy CSS 는 영어 슬러그(red)를
# 쓴다. 매핑에 없는 색은 조용히 건너뛴다 — 화면이 그릴 수 있는 것만 태운다.
_COLOR_MAP = {"빨간색": "red", "노란색": "yellow", "초록색": "green"}
_COLOR_SHORT = {"red": "빨강", "yellow": "노랑", "green": "초록"}

# 시드에 없는 시연용 텍스트. 컬럼을 추가하는 대신 여기서 채운다.
_INGREDIENT = {
    "소염진통제": "이부프로펜 400mg",
    "신경진정제": "가바펜틴 300mg",
    "관절재활제": "글루코사민 500mg",
}
_DOSAGE = {
    "퇴행성 무릎 관절염": "1일 3회, 식후 30분",
    "단순 팔 골절": "1일 2회, 아침·저녁 식후",
    "십자인대 파열": "1일 3회, 식후 즉시",
}
# 병명별 처방 설명. 없으면 조합의 약품명을 이어붙인 것을 기본으로 쓴다.
_RX_DESC = {
    "퇴행성 무릎 관절염": "소염진통제 + 관절재활제 장기 복용",
    "단순 팔 골절": "골 회복을 위한 관절재활제 단독",
    "십자인대 파열": "소염진통제·신경진정제·관절재활제 병용",
}
_DOCTOR = {
    "p001": "정형외과 이준호",
    "p002": "정형외과 이준호",
    "p003": "재활의학과 최서연",
}
_NOTES = {
    "p001": "장기 복용 — 위장 상태 주기 확인",
    "p002": "없음",
    "p003": "재활 병행",
}

# ── 환경 변수 ──────────────────────────────────────────────────────────
# Flask 판은 CLI 플래그였다. FastAPI 는 단일 앱이라 CLI 를 쓸 수 없다 —
# 환경 변수로 옮긴다. 기본은 항상 시뮬레이션 (실수로 실제 로봇을 움직이지 않게).
REAL_MODE = os.environ.get("PHARMACY_REAL", "0") == "1"

# 포장(약통 → 봉투)은 조제와 **다른 노트북에서 다른 모델로** 만들어졌다. 조제는
# `~/omx_pill_project` 가, 포장은 저장소 안 `omx/il` 킷과 `~/train/act_pill_bottle_v1`
# 체크포인트가 있어야 돈다. 한쪽만 갖춘 자리가 정상이므로 스위치를 나눈다 —
# PHARMACY_REAL 로 묶으면 포장을 켜려다 없는 조제 파트까지 끌어와 실패한다.
PACK_REAL = os.environ.get("PACK_REAL", "0") == "1"
_PACK_SCRIPT = _DATA_DIR / "pack_run.py"
# 로컬 경로이거나 HF Hub repo id. 체크포인트는 200MB 가 넘어 저장소에 넣지
# 않는다 — `policies.json` 이 조제 정책을 repo id 로 참조하는 것과 같은 규칙이다.
_PACK_CKPT = os.environ.get(
    "PACK_CKPT", "~/train/act_pill_bottle_v1/checkpoints/last/pretrained_model")
# 05_record.sh 가 에피소드를 60초로 찍었다. 정책은 그보다 오래 줘도 배운 것
# 이상은 못 한다 — 상한이지 목표 시간이 아니다.
PACK_SECONDS = float(os.environ.get("PACK_SECONDS", "60"))
# 리허설. 로봇·카메라에 붙어 추론까지 하지만 행동을 보내지 않는다 — 팔이 서
# 있는 채로 화면·SSE·subprocess 배선을 전부 확인할 수 있다. 실기 앞에서 배선을
# 고치다가 팔을 움직이지 않으려고 둔다.
PACK_DRY_RUN = os.environ.get("PACK_DRY_RUN", "0") == "1"
_PACK_MARKER = "PACK_JSON"

DEFAULT_POLICY = os.environ.get("POLICY", "xy")
SHOW_WINDOW = os.environ.get("SHOW", "1") != "0"   # 카메라 창을 띄울지
RECORD = os.environ.get("REC", "0") != "0"         # 정책이 본 화면을 mp4 로 남길지

PICK_TIMEOUT = 150          # 색 하나에 주는 최대 시간 (초)
REST = 5                    # 색별 정책에서 색 사이 카메라 회복 대기 (초)
TRAY_FRAMES = 5             # 트레이를 몇 장 찍어 최빈값을 낼지
# lerobot·torch import + 장마다 자동노출을 기다리는 시간까지 합쳐 실측 18초였다
# (모든 프레임이 검은, 즉 매 장이 75프레임을 다 버리는 최악). 카메라가 아예
# 응답하지 않을 때 요청이 영원히 매달리지 않도록 넉넉히 잡아 끊는다.
TRAY_TIMEOUT = 60           # 트레이 계수 프로세스에 주는 최대 시간 (초)

# 빨강·노랑은 반경 기준으로 보정값을 잡아 왔다 (색별 정책에만 해당).
EXTRA = {"red": "--radial-offset", "yellow": "--radial-offset", "green": ""}

# ── UI 계약: run.sh / run_policy 출력 문자열 ──────────────────────────
LOG_STEP_DONE = "담기 완료"
LOG_NEXT_TARGET = "다음 목표"
LOG_ALL_DONE = "처방 조제 완료"
LOG_MISS = "놓쳤습니다"
LOG_STALL = "제자리"

# ── 상태 (robot_id 별 분리) ────────────────────────────────────────────
# 스테이션이 늘었다. 조제 박스(omx-01)와 포장 박스(omx-02)가 서로 다른 job 으로
# **동시에** 돌 수 있어야 한다. 그래서 전역 단일 job 을 robot_id 별 dict 로 나눈다.
#
# **기본 스테이션은 여전히 모듈 전역 `_JOB` 이다.** robot_id 를 주지 않는 기존
# 호출(프론트·기존 테스트)은 이 전역을 그대로 쓴다 — 하위호환이 깨지지 않는다.
# 그 외 스테이션만 `_JOBS[robot_id]` 에 따로 둔다. `_JOB_PROC` 도 같은 원칙으로,
# 기본(조제) 스테이션의 서브프로세스는 전역에 남는다 — 트레이 카메라 게이트가
# 이 전역을 본다(_tray_preflight, test_pharmacy_tray).
#
# 조제 기본 스테이션과 포장 스테이션의 robot_id 는 env 로 덮을 수 있다.
DEFAULT_ROBOT_ID = os.environ.get("MINGKY_OMX_DISPENSE_ROBOT_ID", "omx-01")
PACK_ROBOT_ID = os.environ.get("MINGKY_OMX_PACK_ROBOT_ID", "omx-02")


def _new_job() -> dict:
    return {"id": None, "상태": "대기", "단계": [],
            "환자": None, "처방": None, "정책": None, "중단요청": False}


_JOB: dict = _new_job()                        # 기본(조제) 스테이션 — 전역 별칭
_JOBS: dict[str, dict] = {}                     # 그 외 스테이션(포장 등)
_JOB_LOCK = asyncio.Lock()
_JOB_TASK: asyncio.Task | None = None           # 기본 스테이션 워커
_JOB_TASKS: dict[str, asyncio.Task] = {}        # 그 외 스테이션 워커
_JOB_PROC: asyncio.subprocess.Process | None = None   # 기본 스테이션 서브프로세스
_JOB_PROCS: dict[str, asyncio.subprocess.Process] = {}


def _is_default(robot_id: str | None) -> bool:
    return robot_id is None or robot_id == DEFAULT_ROBOT_ID


def _job(robot_id: str | None = None) -> dict:
    """robot_id 의 job 상태. 기본 스테이션은 전역 `_JOB` 을 그대로 돌려준다."""
    if _is_default(robot_id):
        return _JOB
    return _JOBS.setdefault(robot_id, _new_job())


def _get_proc(robot_id: str | None):
    if _is_default(robot_id):
        return _JOB_PROC
    return _JOB_PROCS.get(robot_id)


def _set_proc(robot_id: str | None, proc) -> None:
    global _JOB_PROC
    if _is_default(robot_id):
        _JOB_PROC = proc
    elif proc is None:
        _JOB_PROCS.pop(robot_id, None)
    else:
        _JOB_PROCS[robot_id] = proc

# 트레이 계수 직렬화. top 카메라는 V4L2 라 동시에 두 번 열리지 않는다.
_TRAY_LOCK = asyncio.Lock()

# 약품 캐시. DB 를 매 단계마다 조회하지 않기 위해 첫 로드 뒤 메모리에 두고,
# `prescriptions()` (약국 화면 진입 시 호출) 이 갱신한다. 시연 세션 동안 약품
# 테이블이 바뀔 일은 없으므로 TTL 을 두지 않는다.
_MEDS_CACHE: dict[str, dict] = {}

# 진행 상황 fan-out.
_SUBSCRIBERS: set[asyncio.Queue] = set()


def _rx_code(condition_id: int) -> str:
    return f"CN-{condition_id:02d}"


# ── DB 로드 ────────────────────────────────────────────────────────────
async def _load_meds() -> dict[str, dict]:
    """약품 dict — `{color_slug: {이름, 성분, 색이름}}`."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT medication_name, color FROM medications ORDER BY medication_id")
    out: dict[str, dict] = {}
    for r in rows:
        slug = _COLOR_MAP.get(r["color"])
        if slug is None:
            log.debug("알 수 없는 약품 색 (매핑 없음): %s", r["color"])
            continue
        out[slug] = {
            "이름": r["medication_name"],
            "성분": _INGREDIENT.get(r["medication_name"], ""),
            "색이름": _COLOR_SHORT[slug],
        }
    return out


async def _load_rx() -> list[dict]:
    """처방 목록 — 병명별 색 조합.

    한 방에 조인해서 병명 하나당 한 행. 조합 순서는 medication_id 오름차순 —
    시드의 논리적 순서를 그대로 반영한다.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT c.condition_id, c.condition_name,
                   COALESCE(
                     ARRAY_AGG(m.color ORDER BY m.medication_id)
                     FILTER (WHERE m.medication_id IS NOT NULL),
                     ARRAY[]::text[]
                   ) AS colors
            FROM conditions c
            LEFT JOIN condition_medications cm ON cm.condition_id = c.condition_id
            LEFT JOIN medications m ON m.medication_id = cm.medication_id
            GROUP BY c.condition_id, c.condition_name
            ORDER BY c.condition_id
        """)
    result: list[dict] = []
    for r in rows:
        combo = [_COLOR_MAP[c] for c in r["colors"] if c in _COLOR_MAP]
        result.append({
            "코드": _rx_code(r["condition_id"]),
            "병명": r["condition_name"],
            "조합": combo,
            "설명": _RX_DESC.get(r["condition_name"], ""),
            "복용": _DOSAGE.get(r["condition_name"], "복용법 미정"),
        })
    return result


async def _load_patients(q: str) -> list[dict]:
    """환자 목록 + 각자의 병명·처방. `q` 가 비면 전체."""
    pool = get_pool()
    q_lower = q.strip().lower()
    like = f"%{q_lower}%"
    base = """
        SELECT p.patient_id, p.name, p.birth_date, p.gender,
               c.condition_id, c.condition_name,
               COALESCE(
                 ARRAY_AGG(m.color ORDER BY m.medication_id)
                 FILTER (WHERE m.medication_id IS NOT NULL),
                 ARRAY[]::text[]
               ) AS colors
        FROM patients p
        JOIN conditions c ON c.condition_id = p.condition_id
        LEFT JOIN condition_medications cm ON cm.condition_id = c.condition_id
        LEFT JOIN medications m ON m.medication_id = cm.medication_id
        {where}
        GROUP BY p.patient_id, p.name, p.birth_date, p.gender,
                 c.condition_id, c.condition_name
        ORDER BY p.patient_id
    """
    async with pool.acquire() as conn:
        if q_lower:
            rows = await conn.fetch(base.format(where="""
                WHERE LOWER(p.patient_id) LIKE $1
                   OR LOWER(p.name) LIKE $1
                   OR p.birth_date::text LIKE $1
                   OR LOWER(c.condition_name) LIKE $1
            """), like)
        else:
            rows = await conn.fetch(base.format(where=""))

    result: list[dict] = []
    for r in rows:
        combo = [_COLOR_MAP[c] for c in r["colors"] if c in _COLOR_MAP]
        pid = r["patient_id"]
        code = _rx_code(r["condition_id"])
        result.append({
            "id": pid,
            "이름": r["name"],
            "생년": r["birth_date"].isoformat(),
            # 시드는 '남자'/'여자' 로 저장돼 있다. 화면 폭을 위해 첫 글자만.
            "성별": (r["gender"] or "")[:1],
            "병명": r["condition_name"],
            "처방코드": code,
            "담당의": _DOCTOR.get(pid, ""),
            "특이사항": _NOTES.get(pid, ""),
            "처방": {
                "코드": code,
                "병명": r["condition_name"],
                "조합": combo,
                "설명": _RX_DESC.get(r["condition_name"], ""),
                "복용": _DOSAGE.get(r["condition_name"], "복용법 미정"),
            },
        })
    return result


# ── 공개 조회 API ──────────────────────────────────────────────────────
async def prescriptions() -> dict:
    """약품 + 처방 목록. 원본 (랜덤 안 뽑은 상태)."""
    meds = await _load_meds()
    _MEDS_CACHE.clear()
    _MEDS_CACHE.update(meds)   # 워커에서 참조하려고 캐시에 남긴다
    rx = await _load_rx()
    return {"약품": meds, "처방": rx}


def policies() -> dict:
    return {"정책": _POL_RAW, "기본": DEFAULT_POLICY}


async def patients(q: str = "") -> dict:
    """이름·생년월일·환자ID·병명으로 찾는다. 빈 검색어면 전체.

    병명에 맞는 처방을 함께 붙여 준다 — 화면에서 한 번 더 찾을 필요가 없게.
    """
    return {"환자": await _load_patients(q)}


async def _find_prescription(코드: str) -> dict | None:
    for r in await _load_rx():
        if r["코드"] == 코드:
            return r
    return None


async def _get_meds() -> dict[str, dict]:
    """워커에서 부르는 약품 dict. 캐시가 비었으면 채운다 (첫 진입이 아직 안 온 경우)."""
    if not _MEDS_CACHE:
        _MEDS_CACHE.update(await _load_meds())
    return _MEDS_CACHE


# ── 트레이 상태 ────────────────────────────────────────────────────────
def _now_hms() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _tray_preflight() -> str | None:
    """실제 트레이를 읽을 수 있는 상태인지. 못 읽으면 그 이유를 돌려준다."""
    if _JOB_PROC is not None:
        # top 카메라는 V4L2 라 두 번 열리지 않는다. 조제 중에 트레이를 읽으려
        # 들면 정책이 쓰던 카메라를 빼앗아 조제가 통째로 죽는다.
        return "조제 중에는 트레이를 읽을 수 없습니다 — 로봇이 카메라를 쓰고 있습니다"
    if not _TRAY_SCRIPT.is_file():
        return f"트레이 계수 스크립트가 없습니다: {_TRAY_SCRIPT}"
    if not (_OMX_PROJECT / "pharmacy.py").is_file():
        return (f"조제 파트를 찾지 못했습니다: {_OMX_PROJECT} — "
                f"OMX_PILL_ROOT 로 경로를 지정하세요")
    if not _OMX_PYTHON.is_file():
        return (f"조제 파트 파이썬이 없습니다: {_OMX_PYTHON} — "
                f"OMX_PYTHON 으로 경로를 지정하세요")
    return None


async def _count_pills() -> dict:
    """조제 파트의 `count_pills()` 를 별도 프로세스로 돌려 개수를 받는다.

    **in-process import 는 못 한다.** `pharmacy.py` 는 import 만 해도
    `run_policy` → lerobot · torch · cv2 를 끌어오는데, 관제 백엔드 venv 에는
    그 스택이 없다. 조제(`run.sh`) 와 같이 il venv 파이썬으로 띄우고 stdout 의
    `TRAY_JSON` 한 줄만 읽는다 (`omx/web/count_tray.py`).
    """
    cmd = [str(_OMX_PYTHON), str(_TRAY_SCRIPT),
           "--root", str(_OMX_PROJECT), "--frames", str(TRAY_FRAMES)]
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(_OMX_PROJECT),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        raw_out, raw_err = await asyncio.wait_for(
            proc.communicate(), timeout=TRAY_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"오류": f"트레이를 읽는 데 {TRAY_TIMEOUT}초를 넘겼습니다 — "
                       f"top 카메라가 응답하지 않습니다"}

    out = raw_out.decode(errors="replace")
    for line in reversed(out.splitlines()):
        if line.startswith(_TRAY_MARKER):
            return json.loads(line[len(_TRAY_MARKER):])

    # JSON 한 줄이 없다 = 스크립트가 시작도 못 했다. stderr 꼬리가 진짜 이유다.
    tail = (raw_err.decode(errors="replace").strip().splitlines() or ["(출력 없음)"])[-1]
    log.warning("트레이 계수 실패 (rc=%s): %s", proc.returncode, tail)
    return {"오류": f"트레이 계수가 실패했습니다 — {tail}"}


async def read_tray() -> dict:
    """트레이에 각 색이 몇 개 있는지. 실제 모드에서만 카메라를 연다.

    카메라를 여는 일이라 몇 초 걸리고 동시에 두 번 열 수 없다. 화면 두 개가
    같이 눌러도 한 번만 읽도록 잠금으로 직렬화한다.
    """
    if not REAL_MODE:
        return {"모드": "시뮬레이션", "시각": _now_hms(),
                "개수": {"red": 1, "yellow": 1, "green": 1}}

    막힌이유 = _tray_preflight()
    if 막힌이유:
        return {"모드": "실제", "시각": _now_hms(), "오류": 막힌이유}

    async with _TRAY_LOCK:
        # 잠금을 기다리는 사이 조제가 시작됐을 수 있다.
        막힌이유 = _tray_preflight()
        if 막힌이유:
            return {"모드": "실제", "시각": _now_hms(), "오류": 막힌이유}
        try:
            결과 = await _count_pills()
        except Exception as e:  # noqa: BLE001
            결과 = {"오류": f"{type(e).__name__}: {e}"}
    return {"모드": "실제", "시각": _now_hms(), **결과}


async def random_prescriptions() -> tuple[dict, int]:
    """모든 처방의 색 조합을 새로 뽑는다. 응답과 HTTP 상태를 함께 돌려준다."""
    tray = await read_tray()
    if tray.get("오류"):
        return {"오류": tray["오류"]}, 503
    있는색 = [c for c, n in tray["개수"].items() if n > 0]
    if not 있는색:
        return {"오류": "트레이에 알약이 없습니다 — 알약을 놓고 다시 확인하세요"}, 400

    meds = await _get_meds()
    rx_list = await _load_rx()

    최대 = min(len(있는색), 3)
    후보 = list(range(1, 최대 + 1))
    가중 = [{1: 11.5, 2: 24.0, 3: 64.6}[k] for k in 후보]

    처방 = []
    for r in rx_list:
        개수 = random.choices(후보, weights=가중, k=1)[0]
        조합 = random.sample(있는색, 개수)   # sample 은 조합과 순서를 함께 섞는다
        처방.append({**r, "조합": 조합,
                     "설명": " → ".join(meds[c]["색이름"] for c in 조합 if c in meds)})
    return {"처방": 처방, "트레이": tray["개수"]}, 200


# ── SSE 브로드캐스트 ───────────────────────────────────────────────────
def _push(event: dict) -> None:
    data = json.dumps(event, ensure_ascii=False)
    for q in list(_SUBSCRIBERS):
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            log.debug("SSE 구독자 큐 가득 — 이벤트 누락")


async def subscribe() -> AsyncIterator[str]:
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _SUBSCRIBERS.add(q)
    try:
        yield "retry: 3000\n\n"
        while True:
            try:
                data = await asyncio.wait_for(q.get(), timeout=20)
                yield f"data: {data}\n\n"
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
    finally:
        _SUBSCRIBERS.discard(q)


# ── 조제 실행 ──────────────────────────────────────────────────────────
async def _단계시작(i: int, color: str, robot_id: str | None = None) -> None:
    meds = await _get_meds()
    약 = meds.get(color, {"이름": color, "색이름": _COLOR_SHORT.get(color, color)})
    async with _JOB_LOCK:
        _job(robot_id)["단계"].append(
            {"순번": i, "색": color, "약": 약["이름"], "상태": "진행"})
    _push({"종류": "단계시작", "순번": i, "색": color,
           "약": 약["이름"], "색이름": 약["색이름"], "robot_id": robot_id})


async def _단계끝(i: int, color: str, ok: bool, 메모: str,
                robot_id: str | None = None) -> None:
    async with _JOB_LOCK:
        for step in _job(robot_id)["단계"]:
            if step["순번"] == i:
                step.update({"상태": "완료" if ok else "실패", "메모": 메모})
                break
    _push({"종류": "단계끝", "순번": i, "색": color, "성공": ok,
           "메모": 메모, "robot_id": robot_id})


async def _중단(이유: str, robot_id: str | None = None) -> None:
    async with _JOB_LOCK:
        _job(robot_id)["상태"] = "중단"
    _push({"종류": "중단", "이유": 이유, "robot_id": robot_id})


async def _조제완료(job_id: str, robot_id: str | None = None) -> None:
    async with _JOB_LOCK:
        _job(robot_id)["상태"] = "조제완료"
    _push({"종류": "조제완료", "job": job_id, "robot_id": robot_id,
           "시각": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})


async def _dispense_worker(job_id: str, 처방: dict, policy_id: str,
                           robot_id: str | None = None) -> bool:
    조합 = 처방["조합"]
    _push({"종류": "시작", "job": job_id, "총단계": len(조합), "robot_id": robot_id})

    # 관제 보고(OMX 박스에서만). 정책 로드 → 사이클 시작. duration_ms 는 실제
    # 경과로만 채운다 — 성공 판정이 아니라 시간이 정본이다(§4.4). 시뮬/실제 어느
    # 쪽이든 조제 흐름·상태는 건드리지 않는 부수효과이고, env 없으면 전부 무동작.
    await _report_policy_loaded(policy_id)
    _시작 = time.monotonic()
    await _report("cycle_started", job_id, "+".join(조합) or "(빈 조합)")

    async def _완료() -> None:
        await _조제완료(job_id, robot_id)
        await _report("cycle_completed", job_id,
                      int((time.monotonic() - _시작) * 1000))

    async def _포기(이유: str) -> None:
        await _중단(이유, robot_id)
        await _report("cycle_aborted", job_id, 이유)

    if not REAL_MODE:
        for i, color in enumerate(조합, 1):
            await _단계시작(i, color, robot_id)
            for _ in range(16):
                if _job(robot_id).get("중단요청"):
                    await _포기("사용자가 중단했습니다")
                    return False
                await asyncio.sleep(0.25)
            await _단계끝(i, color, True, "시뮬레이션", robot_id)
        await _완료()
        return True

    ok, 메모 = await _run_sequence(조합, policy_id, robot_id)
    if not ok:
        await _포기(메모)
        return False
    await _완료()
    return True


# ── 원격 러너 프록시 (박스별 HTTP) ──────────────────────────────────────
# 아키텍처: 각 OMX 박스(.41=조제, .97=포장)가 로컬 스크립트(run.sh·pack_run.py·
# count_tray.py)를 감싼 경량 HTTP 러너(omx/report/runner.py)를 띄우고, 클라우드
# 백엔드가 여기로 프록시한다. URL 이 env 로 설정되면 원격, 없으면 기존 로컬
# 서브프로세스/시뮬 그대로다 — CI·데모·개발은 env 가 없으므로 동작 불변.
#
# 러너 자체가 lerobot·카메라를 들고 있으므로, 백엔드는 진행 상황만 폴링해
# 화면(SSE)으로 옮긴다. run.sh 는 몇 분씩 걸릴 수 있어 시작을 블로킹으로
# 기다리지 않고 상태를 짧게 폴링한다.
_RUNNER_HTTP_TIMEOUT = float(os.environ.get("MINGKY_OMX_RUNNER_TIMEOUT", "10"))
_RUNNER_POLL_SEC = float(os.environ.get("MINGKY_OMX_RUNNER_POLL", "1"))


def _dispense_runner_url() -> str | None:
    return (os.environ.get("MINGKY_OMX_DISPENSE_URL") or "").rstrip("/") or None


def _pack_runner_url() -> str | None:
    return (os.environ.get("MINGKY_OMX_PACK_URL") or "").rstrip("/") or None


def _http_json(url: str, method: str, body: dict | None = None) -> dict:
    """러너에 JSON 요청. 블로킹 urllib 이라 호출부가 to_thread 로 감싼다."""
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_RUNNER_HTTP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8") or "{}"
        return json.loads(raw)
    except urllib.error.HTTPError as e:  # 러너가 4xx/5xx 로 오류 메시지를 실어 준다
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {"오류": f"러너 HTTP {e.code}"}
    except (urllib.error.URLError, OSError) as e:
        return {"오류": f"러너에 연결하지 못했습니다: {e}"}


async def _run_remote_dispense(base_url: str, 조합: list[str], policy_id: str,
                               robot_id: str | None) -> tuple[bool, str]:
    """원격 러너에 조제를 맡기고 상태를 폴링해 단계 진행을 화면으로 옮긴다."""
    started = await asyncio.to_thread(
        _http_json, f"{base_url}/dispense/start", "POST",
        {"sequence": 조합, "policy": policy_id})
    if started.get("오류"):
        return False, str(started["오류"])

    mirrored = 0   # 화면에 이미 반영한 완료 단계 수
    while True:
        if _job(robot_id).get("중단요청"):
            await asyncio.to_thread(_http_json, f"{base_url}/dispense/stop", "POST", {})
            return False, "사용자가 중단했습니다"

        st = await asyncio.to_thread(_http_json, f"{base_url}/dispense/state", "GET")
        if st.get("오류"):
            return False, str(st["오류"])

        done = min(int(st.get("완료단계", 0)), len(조합))
        while mirrored < done:
            i = mirrored + 1
            await _단계시작(i, 조합[i - 1], robot_id)
            await _단계끝(i, 조합[i - 1], True, "원격 러너", robot_id)
            mirrored += 1

        상태 = st.get("상태")
        if 상태 == "완료":
            return True, "완료"
        if 상태 in ("오류", "중단", "실패"):
            return False, str(st.get("메모") or "원격 조제가 실패했습니다")
        await asyncio.sleep(_RUNNER_POLL_SEC)


async def _run_remote_pack(base_url: str, robot_id: str | None) -> tuple[bool, str]:
    """원격 러너에 포장을 맡긴다. 조제 폴링과 같은 구조."""
    started = await asyncio.to_thread(_http_json, f"{base_url}/pack/start", "POST", {})
    if started.get("오류"):
        return False, str(started["오류"])
    while True:
        if _job(robot_id).get("중단요청"):
            await asyncio.to_thread(_http_json, f"{base_url}/pack/stop", "POST", {})
            return False, "사용자가 중단했습니다"
        st = await asyncio.to_thread(_http_json, f"{base_url}/pack/state", "GET")
        if st.get("오류"):
            return False, str(st["오류"])
        상태 = st.get("상태")
        if 상태 == "완료":
            return True, "완료"
        if 상태 in ("오류", "중단", "실패"):
            return False, str(st.get("메모") or "원격 포장이 실패했습니다")
        await asyncio.sleep(_RUNNER_POLL_SEC)


async def _run_sequence(조합: list[str], policy_id: str,
                        robot_id: str | None = None) -> tuple[bool, str]:
    # 원격 러너(박스별 HTTP)가 설정돼 있으면 로컬 서브프로세스 대신 프록시한다.
    # env 가 없으면(데모·CI·개발) 아래 로컬 경로 그대로 — 하위호환·CI 불변.
    remote = _dispense_runner_url()
    if remote:
        return await _run_remote_dispense(remote, 조합, policy_id, robot_id)

    pol = _POLICIES.get(policy_id, _POLICIES[DEFAULT_POLICY])
    단일 = pol["단일정책"]

    if not 단일:
        for i, color in enumerate(조합, 1):
            if _job(robot_id).get("중단요청"):
                return False, "사용자가 중단했습니다"
            await _단계시작(i, color, robot_id)
            ok, 메모 = await _run_one(pol, color, last=(i == len(조합)))
            await _단계끝(i, color, ok, 메모, robot_id)
            if not ok:
                return False, 메모
            await asyncio.sleep(REST)
        return True, "완료"

    cmd = ["timeout", "-s", "INT", str(PICK_TIMEOUT * len(조합)),
           "bash", str(_OMX_PROJECT / "run.sh"), pol["ckpt"],
           "--repo-id", pol["repo"], "--relax-on-exit",
           "--no-freeze-on-grasp", "--offset-step", "1",
           "--sequence", ",".join(조합), "--trace",
           "--dump-grasp", str(_OMX_PROJECT / "grasp_shots" / policy_id)]
    if RECORD:
        cmd += ["--record-video",
                str(_OMX_PROJECT / "report" / f"web_{policy_id}_{datetime.now():%H%M%S}.mp4")]
    if pol.get("앙상블", True):
        cmd.append("--temporal-ensemble")
    if SHOW_WINDOW:
        cmd += ["--show", "--local-keys"]

    env = {**os.environ, "RUN": pol["run"], "TASK": f"pick {조합[0]} pill",
           "HF_HUB_OFFLINE": "1"}

    i = 1
    await _단계시작(i, 조합[0], robot_id)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(_OMX_PROJECT), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        _set_proc(robot_id, proc)
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            if _job(robot_id).get("중단요청"):
                proc.send_signal(2)
                try:
                    await asyncio.wait_for(proc.wait(), timeout=30)
                except asyncio.TimeoutError:
                    proc.kill()
                return False, "사용자가 중단했습니다"
            if LOG_STEP_DONE in line:
                await _단계끝(i, 조합[i - 1], True, f"{pol['이름']}", robot_id)
            elif LOG_NEXT_TARGET in line and i < len(조합):
                i += 1
                await _단계시작(i, 조합[i - 1], robot_id)
            elif LOG_MISS in line:
                _push({"종류": "알림",
                       "글": "놓쳤습니다 — 다시 시도합니다", "급": "warn"})
            elif LOG_STALL in line:
                _push({"종류": "알림",
                       "글": "제자리에 멈춰 홈으로 되돌립니다", "급": "warn"})
            elif LOG_ALL_DONE in line:
                pass
        await proc.wait()
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    finally:
        _set_proc(robot_id, None)

    async with _JOB_LOCK:
        done = sum(1 for s in _job(robot_id)["단계"] if s["상태"] == "완료")
    if done < len(조합):
        await _단계끝(i, 조합[i - 1], False, "제한 시간 안에 담지 못했습니다", robot_id)
        return False, f"{done}/{len(조합)} 만 담았습니다"
    return True, "완료"


async def _run_one(pol: dict, color: str, last: bool = True) -> tuple[bool, str]:
    cmd = ["timeout", "-s", "INT", str(PICK_TIMEOUT),
           "bash", str(_OMX_PROJECT / "run.sh"), pol["ckpt"][color],
           "--repo-id", pol["repo"],
           "--no-freeze-on-grasp", "--offset-step", "1", "--trace"]
    if last:
        cmd.append("--relax-on-exit")
    if pol.get("앙상블", True):
        cmd.append("--temporal-ensemble")
    if EXTRA[color]:
        cmd.append(EXTRA[color])
    if SHOW_WINDOW:
        cmd += ["--show", "--local-keys"]
    env = {**os.environ, "RUN": pol["run"][color], "TASK": f"pick {color} pill",
           "HF_HUB_OFFLINE": "1"}
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(_OMX_PROJECT), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=PICK_TIMEOUT + 30)
        except asyncio.TimeoutError:
            proc.kill()
            return False, f"제한 시간({PICK_TIMEOUT}초)을 넘겼습니다"
        ok = LOG_STEP_DONE in stdout.decode(errors="replace")
        return (ok, pol["이름"] if ok else "제한 시간을 넘겼습니다")
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


async def _pack_worker(job_id: str, robot_id: str | None = None) -> None:
    _push({"종류": "포장시작", "job": job_id, "robot_id": robot_id})
    async with _JOB_LOCK:
        _job(robot_id)["상태"] = "포장중"

    # 포장 사이클도 관제로 보고한다(OMX 박스에서만). 조제와 job_id 가 같으므로
    # dispense_id 에 접미사를 붙여 구분한다. 넷 중 로봇이 하는 것은 "약 투입"
    # 하나지만, 사이클 시간은 포장 전체의 벽시계 소요다.
    _시작 = time.monotonic()
    await _report("cycle_started", f"{job_id}-pack", "pack")

    for 단계 in ("봉투 준비", "약 투입", "라벨 인쇄", "밀봉"):
        _push({"종류": "포장단계", "이름": 단계, "robot_id": robot_id})
        # 로봇이 하는 것은 "약 투입" 하나다. 학습된 작업이 "약통을 집어 봉투에
        # 넣기" 뿐이라 (`omx/il/TASK.md`), 나머지 셋은 실제 모드에서도 시뮬레이션
        # 이다. 넷 다 진짜인 것처럼 보이게 만들지 않는다.
        if 단계 == "약 투입" and PACK_REAL:
            ok, 메모 = await _run_pack(robot_id)
            if not ok:
                await _중단(메모, robot_id)
                await _report("cycle_aborted", f"{job_id}-pack", 메모)
                return
        else:
            await asyncio.sleep(1.2)

    async with _JOB_LOCK:
        _job(robot_id)["상태"] = "완료"
    _push({"종류": "완료", "job": job_id, "robot_id": robot_id,
           "시각": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    await _report("cycle_completed", f"{job_id}-pack",
                  int((time.monotonic() - _시작) * 1000))


async def _run_pack(robot_id: str | None = None) -> tuple[bool, str]:
    """`omx/web/pack_run.py` 를 il venv 로 띄우고 `PACK_JSON` 줄만 읽는다.

    조제(`_run_sequence`) 와 같은 구조다 — 백엔드는 lerobot·torch·카메라를 모르고,
    자식 프로세스의 stdout 한 줄씩만 화면 이벤트로 옮긴다.
    """
    # 원격 러너가 설정돼 있으면 포장도 프록시한다. env 없으면 로컬 그대로.
    remote = _pack_runner_url()
    if remote:
        return await _run_remote_pack(remote, robot_id)

    if not _PACK_SCRIPT.is_file():
        return False, f"포장 러너를 찾지 못했습니다: {_PACK_SCRIPT}"
    # repo id 는 여기서 확인할 방법이 없다 (받아 봐야 안다). 로컬 경로일 때만
    # 미리 걸러 주고, 나머지는 러너가 판단해 `PACK_JSON {"오류": ...}` 로 올린다.
    로컬 = Path(_PACK_CKPT).expanduser()
    if _PACK_CKPT.startswith(("/", "~", ".")) and not (로컬 / "config.json").is_file():
        return False, (f"포장 정책을 찾지 못했습니다: {로컬} — "
                       f"PACK_CKPT 로 지정하거나 omx/il/06_train.sh 로 학습하세요")

    cmd = [str(_OMX_PYTHON), str(_PACK_SCRIPT),
           "--ckpt", _PACK_CKPT,
           "--seconds", str(PACK_SECONDS)]
    if PACK_DRY_RUN:
        cmd.append("--dry-run")
        _push({"종류": "알림", "글": "포장 — 리허설(팔이 움직이지 않습니다)", "급": "warn"})
    마지막오류 = ""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(_DATA_DIR),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        _set_proc(robot_id, proc)
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            if _job(robot_id).get("중단요청"):
                # SIGINT 로 보내야 러너의 finally 가 돌아 팔의 토크가 풀린다.
                # 죽여 버리면 팔이 마지막 자세로 힘을 준 채 남는다.
                proc.send_signal(2)
                try:
                    await asyncio.wait_for(proc.wait(), timeout=30)
                except asyncio.TimeoutError:
                    proc.kill()
                return False, "사용자가 중단했습니다"
            if _PACK_MARKER not in line:
                continue
            try:
                ev = json.loads(line.split(_PACK_MARKER, 1)[1].strip())
            except json.JSONDecodeError:
                continue
            if ev.get("오류"):
                마지막오류 = str(ev["오류"])
            elif ev.get("단계") in ("정책 로드", "로봇 연결"):
                # 첫 실행은 torch·정책 로드에만 수십 초가 걸린다. 아무 소식이
                # 없으면 멈춘 것으로 보이므로 로그에 남긴다.
                _push({"종류": "알림", "글": f"포장 — {ev['단계']}"})
        await proc.wait()
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    finally:
        _set_proc(robot_id, None)

    if 마지막오류:
        return False, 마지막오류
    if proc.returncode != 0:
        return False, f"포장 러너가 종료 코드 {proc.returncode} 로 끝났습니다"
    return True, "완료"


# ── 조작 API ──────────────────────────────────────────────────────────
def _set_task(robot_id: str | None, task: asyncio.Task) -> None:
    global _JOB_TASK
    if _is_default(robot_id):
        _JOB_TASK = task
    else:
        _JOB_TASKS[robot_id] = task


async def start_dispense(body: dict, robot_id: str | None = None) -> tuple[dict, int]:
    job = _job(robot_id)
    환자 = body.get("환자") or {}
    처방 = await _find_prescription(body.get("처방코드") or "")
    meds = await _get_meds()
    # **화면이 보낸 조합이 언제나 우선이다.** 무작위로 다시 뽑기 후 서버가 다시
    # DB 조회를 하면 원본이 나오므로, 화면이 보낸 조합만 통과시킨다.
    조합 = [c for c in (body.get("조합") or []) if c in meds]
    if 조합:
        바탕 = 처방 or {}
        처방 = {**바탕,
                "코드": body.get("처방코드") or "RND",
                "병명": body.get("병명") or 바탕.get("병명") or "임의 조합 (시연용)",
                "조합": 조합,
                "설명": " → ".join(meds[c]["색이름"] for c in 조합),
                "복용": 바탕.get("복용", "시연용 — 실제 복용법이 아닙니다")}
    policy_id = body.get("정책") or DEFAULT_POLICY

    if not 환자.get("이름"):
        return {"오류": "환자 이름을 입력하세요"}, 400
    if 처방 is None:
        return {"오류": "처방을 선택하세요"}, 400
    if policy_id not in _POLICIES:
        return {"오류": f"모르는 정책입니다: {policy_id}"}, 400

    async with _JOB_LOCK:
        if job["상태"] == "조제중":
            return {"오류": "이미 조제가 진행 중입니다"}, 409
        job_id = uuid.uuid4().hex[:8]
        job.update({"id": job_id, "상태": "조제중", "단계": [],
                    "환자": 환자, "처방": 처방, "정책": policy_id,
                    "중단요청": False})

    if REAL_MODE:
        tray = await read_tray()
        # 카메라가 못 읽은 것과 알약이 없는 것은 다르다. 오류를 "알약이 없습니다"
        # 로 뭉치면 사람이 트레이에 알약을 더 올리며 원인을 못 찾는다.
        if tray.get("오류"):
            async with _JOB_LOCK:
                job["상태"] = "대기"
            return {"오류": tray["오류"]}, 503
        부족 = [c for c in 처방["조합"] if tray.get("개수", {}).get(c, 0) < 1]
        if 부족:
            async with _JOB_LOCK:
                job["상태"] = "대기"
            이름 = ", ".join(meds[c]["색이름"] for c in 부족 if c in meds)
            return {"오류": f"트레이에 {이름} 알약이 없습니다"}, 400

    _set_task(robot_id, asyncio.create_task(
        _dispense_worker(job_id, 처방, policy_id, robot_id)))
    return {"job": job_id, "처방": 처방, "robot_id": robot_id or DEFAULT_ROBOT_ID,
            "정책": _POLICIES[policy_id]["이름"]}, 200


async def stop_dispense(robot_id: str | None = None) -> dict:
    async with _JOB_LOCK:
        _job(robot_id)["중단요청"] = True
    _push({"종류": "중단요청", "robot_id": robot_id})
    return {"결과": "중단 요청을 보냈습니다"}


async def start_pack(robot_id: str | None = None) -> tuple[dict, int]:
    # 포장 기본 스테이션은 조제와 다른 박스(omx-02)다. robot_id 를 주지 않으면
    # 조제(기본) 스테이션에서 이어 포장한다 — 기존 단일-스테이션 흐름 유지.
    # 조제와 다른 스테이션을 지정하면 두 job 이 동시에 돌 수 있다(§6.2 · item 3).
    job = _job(robot_id)
    async with _JOB_LOCK:
        # 실행 중인 job 위에 겹쳐 시작하지 않는다. 그 외(대기·조제완료·완료)는
        # 포장을 허용한다 — 조제를 마친 스테이션이든, 조제 없이 포장만 받는
        # 전용 포장 스테이션이든.
        if job["상태"] in ("조제중", "포장중"):
            return {"오류": "이미 진행 중입니다"}, 409
        job_id = job["id"] or uuid.uuid4().hex[:8]
        job["id"] = job_id
    _set_task(robot_id, asyncio.create_task(_pack_worker(job_id, robot_id)))
    return {"결과": "포장을 시작했습니다",
            "robot_id": robot_id or DEFAULT_ROBOT_ID}, 200


async def reset_state(robot_id: str | None = None) -> tuple[dict, int]:
    job = _job(robot_id)
    async with _JOB_LOCK:
        if job["상태"] == "조제중":
            return {"오류": "조제 중입니다 — 먼저 중단하세요"}, 409
        job.update({"id": None, "상태": "대기", "단계": [],
                    "환자": None, "처방": None, "정책": None, "중단요청": False})
    _push({"종류": "리셋", "robot_id": robot_id})
    return {"결과": "초기화"}, 200


async def snapshot(robot_id: str | None = None) -> dict:
    async with _JOB_LOCK:
        return dict(_job(robot_id))


# ── 세션 연결 조제 (item 4) ────────────────────────────────────────────
# 안내 로봇이 약국에 도착하면(pharmacy.arrived) 백엔드가 그 환자의 처방을 조제
# 스테이션에서 시작한다. 위 start_dispense 와 달리 화면 입력이 아니라 세션이
# 방아쇠이고, **완료를 기다렸다가** pharmacy_link 가 dispense_completed 를
# 발행해야 하므로 워커를 태스크로 던지지 않고 여기서 끝까지 await 한다.
async def prescription_for_patient(patient_id: str) -> dict | None:
    """환자의 현재 처방(색 조합)을 DB 에서 읽어 조제용 dict 로 만든다."""
    rows = await _load_patients(patient_id)
    for p in rows:
        if p["id"] == patient_id:
            처방 = dict(p["처방"])
            처방["환자"] = {"id": p["id"], "이름": p["이름"]}
            return 처방
    return None


async def run_session_dispense(dispense_id: str, 처방: dict, policy_id: str,
                               robot_id: str) -> bool:
    """세션 연결 조제를 스테이션에서 시작해 완료까지 기다린다. 완주면 True."""
    job = _job(robot_id)
    async with _JOB_LOCK:
        job.update({"id": dispense_id, "상태": "조제중", "단계": [],
                    "환자": 처방.get("환자"), "처방": 처방,
                    "정책": policy_id, "중단요청": False})
    return await _dispense_worker(dispense_id, 처방, policy_id, robot_id)
