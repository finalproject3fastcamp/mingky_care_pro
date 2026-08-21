"""OMX 조제/포장 스테이션 → 관제 리포터 (독립 모듈 · 표준 라이브러리만).

## 왜 이 모듈이 따로 있나

핑키(주행 로봇)는 상시 게이트웨이가 ROS 그래프를 관제로 실어 나른다. OMX 는
ROS 를 쓰지 않아(domain NULL) 게이트웨이가 없다 — 그래서 관제 화면에서
`link_state` 가 영원히 `unknown` 이고 조제 SLI 가 빈 값이었다. 이 모듈이 그
공백을 메운다.

  - 주기 heartbeat 로 '살아있음' 을 보고한다 (`run_heartbeat_loop`)
  - pharmacy 워커가 부르는 진실한 라이프사이클 이벤트를 관제 `/events` 로 보낸다

## 표준 라이브러리만 쓴다

OMX il venv(lerobot v0.4.4)에 requests 가 없을 수 있다. 조제/포장 파이썬과
같은 인터프리터에서 임포트될 수 있어야 하므로 urllib 만 쓴다 — 이 모듈은 추가
설치를 요구하지 않는다.

## pick 성공/실패는 절대 발행하지 않는다

ACT 정책은 "끝났다"나 성공 여부를 내놓지 않는다. 진행률은 시간 기준이고 성공
판정은 사람이 한다(`omx/web/README.md`). 따라서 `manipulator.pick_succeeded` /
`manipulator.pick_failed` 는 우리에게 ground truth 가 없는 신호다 —
`config/event_codes.yaml` 에 코드가 있어도 근거 없는 성공 신호를 지어내지
않는다. 발행하는 것은 시간으로 증명되는 것뿐이다.

  manipulator.cycle_started    사이클 시작       (dispense_id, medication_id)
  manipulator.cycle_completed  실제 소요 시간    (dispense_id, duration_ms)
  manipulator.cycle_aborted    오류/중단         (dispense_id, reason)
  manipulator.policy_loaded    무엇이 돌고 있나  (checkpoint_id, dataset_revision)

페이로드 키는 `config/event_codes.yaml:443-509` 스키마와 정확히 일치한다.

## 실패는 조용히 버린다

heartbeat 는 재전송하지 않는다 — 두절 중 쌓였다 몰려오면 생존 신호의 의미가
사라진다(`backend/app/routers/robots.py`). 이벤트 발행도 워커를 죽이지 않도록
예외를 삼킨다. 관제 보고가 조제를 멈추게 해선 안 된다.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("mingky.omx.report")

DEFAULT_BACKEND_URL = "https://mingkycarepro.site/api"
SOURCE_NODE = "omx.report.reporter"

# 서보 읽기 서브프로세스. dynamixel_sdk 는 il venv 에만 있어 리포터가 직접
# 임포트하지 못한다 — count_tray.py 와 같은 방식으로 il venv 파이썬에 이 스크립트를
# 띄우고 stdout 의 SERVO_JSON 한 줄만 읽는다.
_READ_SERVOS_SCRIPT = Path(__file__).resolve().parent / "read_servos.py"
_SERVO_MARKER = "SERVO_JSON"

# 관제가 Cloudflare 뒤에 있고, 기본 `Python-urllib/x` User-Agent 는 봇으로
# 차단돼 403 이 온다(HTTPError 라 조용히 삼켜져 heartbeat 가 영영 안 닿는다).
# 브라우저 UA 를 흉내 낼 필요는 없고, 기본값만 아니면 통과한다.
USER_AGENT = "mingky-omx-reporter/1.0"

# 발행 가능한(진실한) 이벤트 코드. pick_* 는 여기 없다 — 위 docstring 참조.
CYCLE_STARTED = "manipulator.cycle_started"
CYCLE_COMPLETED = "manipulator.cycle_completed"
CYCLE_ABORTED = "manipulator.cycle_aborted"
POLICY_LOADED = "manipulator.policy_loaded"


# ── 설정 (호출 시점에 읽는다) ─────────────────────────────────────────────
# import 시점이 아니라 매 호출에서 env 를 읽는다. 리포터를 임포트한 뒤 env 를
# 바꿔도 반영되고, 테스트가 백엔드 URL 을 로컬 http.server 로 갈아끼울 수 있다.
def robot_id() -> str | None:
    """이 OMX 박스의 로봇 id. 미설정이면 리포터 전체가 조용히 꺼진다."""
    return os.environ.get("MINGKY_OMX_ROBOT_ID") or None


def backend_url() -> str:
    return os.environ.get("MINGKY_BACKEND_URL", DEFAULT_BACKEND_URL).rstrip("/")


def _resolve_robot_id(explicit: str | None) -> str | None:
    """명시값이 있으면 그것을, 없으면 env 를 쓴다."""
    return explicit or robot_id()


def _timeout() -> float:
    try:
        return float(os.environ.get("MINGKY_OMX_HTTP_TIMEOUT", "5"))
    except ValueError:
        return 5.0


# ── heartbeat ─────────────────────────────────────────────────────────────
def heartbeat(*, rid: str | None = None, base_url: str | None = None,
              body: dict | None = None) -> int | None:
    """생존 신호를 보낸다. 실패하면 조용히 버린다 — 재전송 금지.

    body 가 없으면 본문 없이 순수 생존 핑을 보낸다. `{}` 를 보내면 서버가
    기본값(system_state=unknown 등)으로 runtime 을 덮어써서 생존 핑이 상태
    보고로 둔갑하므로, 실을 것이 없으면 본문을 아예 붙이지 않는다.

    body 가 있으면 자원(cpu_total_pct 등)을 실어 보낸다. OMX 는 manipulator 라
    RobotHeartbeatIn 의 mobile 상태 필드(system_state·localization_active …)는
    화면에 렌더되지 않아, 그 기본값이 실려도 조제 패널을 오염시키지 않는다.
    담는 것은 관제 '자원' 패널이 읽는 필드(cpu_total_pct)뿐이다.
    """
    rid = _resolve_robot_id(rid)
    if not rid:
        return None
    url = f"{base_url or backend_url()}/robots/{rid}/heartbeat"
    if body:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    else:
        data = b""
        headers = {"User-Agent": USER_AGENT}
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_timeout()) as resp:
            return resp.status
    except (urllib.error.URLError, OSError) as e:
        log.debug("heartbeat 실패 %s — %s", url, e)
        return None


# ── 이벤트 발행 ────────────────────────────────────────────────────────────
def _emit(event_code: str, level: str, payload: dict, *,
          rid: str | None, base_url: str | None) -> int | None:
    rid = _resolve_robot_id(rid)
    if not rid:
        return None
    # /events 는 배치(list)를 받고 멱등하다 (backend/app/ingest.py). 한 건씩
    # 리스트로 감싸 보낸다. occurred_at 은 UTC — 서버가 로봇 시계를 믿지 않지만
    # 발생 순서 정렬에는 쓴다.
    event = {
        "event_id": str(uuid.uuid4()),
        "robot_id": rid,
        "session_id": 0,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "event_code": event_code,
        "source_node": SOURCE_NODE,
        "payload": payload,
    }
    url = f"{base_url or backend_url()}/events"
    data = json.dumps([event], ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_timeout()) as resp:
            return resp.status
    except (urllib.error.URLError, OSError) as e:
        log.debug("이벤트 전송 실패 %s (%s) — %s", url, event_code, e)
        return None


def cycle_started(dispense_id, medication_id, *,
                  rid: str | None = None, base_url: str | None = None) -> int | None:
    """조제/포장 사이클 시작. level info."""
    return _emit(
        CYCLE_STARTED, "info",
        {"dispense_id": str(dispense_id), "medication_id": str(medication_id)},
        rid=rid, base_url=base_url)


def cycle_completed(dispense_id, duration_ms, *,
                    rid: str | None = None, base_url: str | None = None) -> int | None:
    """사이클 종료. duration_ms 는 **실제 소요 시간**이다 — 성공 판정이 아니다.

    사이클 타임 p50/p95 의 유일한 재료다(config/event_codes.yaml:469).
    """
    return _emit(
        CYCLE_COMPLETED, "info",
        {"dispense_id": str(dispense_id), "duration_ms": int(duration_ms)},
        rid=rid, base_url=base_url)


def cycle_aborted(dispense_id, reason, *,
                  rid: str | None = None, base_url: str | None = None) -> int | None:
    """오류/중단으로 사이클을 끝내지 못했다. level error."""
    return _emit(
        CYCLE_ABORTED, "error",
        {"dispense_id": str(dispense_id), "reason": str(reason)},
        rid=rid, base_url=base_url)


def policy_loaded(checkpoint_id, dataset_revision, *,
                  rid: str | None = None, base_url: str | None = None) -> int | None:
    """지금 무엇이 돌고 있나(§4.4). 기동/체크포인트 교체 시 발행. level info."""
    return _emit(
        POLICY_LOADED, "info",
        {"checkpoint_id": str(checkpoint_id),
         "dataset_revision": str(dataset_revision)},
        rid=rid, base_url=base_url)


# ── 자원 (CPU) ─────────────────────────────────────────────────────────────
# 박스 전체 CPU 사용률을 표준 라이브러리로 읽는다. /proc/stat 두 표본의 델타로
# 낸다 — os.getloadavg() 는 1분 평균이라 5초 heartbeat 의 순간값과 성격이 다르다.
def read_proc_stat() -> tuple[int, int] | None:
    """/proc/stat 첫 줄에서 (idle_jiffies, total_jiffies). 리눅스 밖이면 None."""
    try:
        with open("/proc/stat", encoding="ascii") as f:
            line = f.readline()
    except OSError:
        return None
    if not line.startswith("cpu "):
        return None
    try:
        nums = [int(x) for x in line.split()[1:]]
    except ValueError:
        return None
    if len(nums) < 5:
        return None
    # user nice system idle iowait irq softirq steal ...
    idle = nums[3] + nums[4]          # idle + iowait
    return idle, sum(nums)


def cpu_total_pct(prev: tuple[int, int] | None,
                  cur: tuple[int, int] | None) -> float | None:
    """두 /proc/stat 표본의 델타로 사용률(%). 표본이 없거나 델타가 0 이면 None."""
    if prev is None or cur is None:
        return None
    idle_delta = cur[0] - prev[0]
    total_delta = cur[1] - prev[1]
    if total_delta <= 0:
        return None
    pct = 100.0 * (1.0 - idle_delta / total_delta)
    return round(max(0.0, min(100.0, pct)), 1)


# ── 서보 (온도·전압) ────────────────────────────────────────────────────────
def _runner_base() -> str:
    """로컬 러너 주소. 조제/포장 진행 여부를 여기서 확인한다."""
    port = os.environ.get("MINGKY_RUNNER_PORT", "8800")
    return f"http://127.0.0.1:{port}"


def _runner_idle() -> bool:
    """조제·포장이 모두 유휴인가.

    러너가 시리얼 포트를 잡고 조제 중이면 서보를 읽어선 안 된다 — 포트 충돌로
    조제가 죽는다. 유휴를 **확인**했을 때만 True 다. 러너에 닿지 못하면(상태를
    모르면) 안전한 쪽으로 False 를 돌려 서보를 읽지 않는다.
    """
    for path in ("/dispense/state", "/pack/state"):
        url = f"{_runner_base()}{path}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=_timeout()) as resp:
                state = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as e:
            log.debug("러너 상태 확인 실패 %s — %s", url, e)
            return False
        if state.get("상태") == "진행":
            return False
    return True


def read_servos_via_subprocess() -> list[dict] | None:
    """il venv 파이썬으로 read_servos.py 를 띄워 서보 표본을 읽는다.

    stdout 의 SERVO_JSON 한 줄을 파싱한다(count_tray.py 와 같은 계약). 스크립트가
    오류 dict 를 주거나 파싱에 실패하면 None — 실패는 조용히 삼킨다.
    """
    omx_python = os.environ.get(
        "OMX_PYTHON", str(Path.home() / "venv" / "il" / "bin" / "python"))
    if not _READ_SERVOS_SCRIPT.is_file():
        return None
    cmd = [omx_python, str(_READ_SERVOS_SCRIPT)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("서보 서브프로세스 실패 — %s", e)
        return None
    for line in reversed(out.stdout.splitlines()):
        if line.startswith(_SERVO_MARKER):
            try:
                payload = json.loads(line[len(_SERVO_MARKER):])
            except ValueError:
                return None
            servos = payload.get("servos")
            return servos if servos else None
    return None


def send_servos(readings: list[dict], *, rid: str | None = None,
                base_url: str | None = None) -> int | None:
    """서보 표본을 `POST /robots/{id}/servos` 로 보낸다 (ServoSampleIn 스키마)."""
    rid = _resolve_robot_id(rid)
    if not rid or not readings:
        return None
    url = f"{base_url or backend_url()}/robots/{rid}/servos"
    data = json.dumps({"servos": readings}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_timeout()) as resp:
            return resp.status
    except (urllib.error.URLError, OSError) as e:
        log.debug("서보 전송 실패 %s — %s", url, e)
        return None


def report_servos(*, rid: str | None = None,
                  base_url: str | None = None) -> int | None:
    """유휴일 때만 서보를 읽어 관제로 보낸다. 실패는 조용히 삼킨다.

    조제/포장 중에는 시리얼 포트 충돌을 피하려 아예 읽지 않는다.
    """
    try:
        if not _runner_idle():
            return None
        readings = read_servos_via_subprocess()
        if not readings:
            return None
        return send_servos(readings, rid=rid, base_url=base_url)
    except Exception as e:  # noqa: BLE001 — 서보 보고가 리포터를 죽여선 안 된다
        log.debug("서보 보고 건너뜀 — %s", e)
        return None


def _servo_interval() -> float:
    """서보 읽기 주기. heartbeat 보다 저빈도(기본 45초)."""
    try:
        return float(os.environ.get("MINGKY_OMX_SERVO_INTERVAL", "45"))
    except ValueError:
        return 45.0


# ── heartbeat 루프 (systemd 진입점) ────────────────────────────────────────
def run_heartbeat_loop(interval: float | None = None) -> None:
    rid = robot_id()
    if not rid:
        log.error("MINGKY_OMX_ROBOT_ID 가 없습니다 — 리포터를 종료합니다")
        return
    if interval is None:
        try:
            interval = float(os.environ.get("MINGKY_OMX_HEARTBEAT_INTERVAL", "5"))
        except ValueError:
            interval = 5.0
    servo_interval = _servo_interval()
    log.info("OMX 리포터 시작 — %s → %s (heartbeat %.1fs · servo %.0fs)",
             rid, backend_url(), interval, servo_interval)

    # CPU 는 직전 표본과의 델타로 낸다. 첫 회차는 기준 표본만 잡아 cpu 없이
    # 생존 핑을 보내고, 다음 회차부터 실제 사용률이 실린다.
    prev_stat = read_proc_stat()
    next_servo = 0.0  # 기동 직후 한 번 시도한다
    while True:
        cur_stat = read_proc_stat()
        pct = cpu_total_pct(prev_stat, cur_stat)
        if cur_stat is not None:
            prev_stat = cur_stat
        # 자원 필드만 담는다. queue_pending 은 OMX 에 큐 개념이 없어 생략한다.
        body = {"cpu_total_pct": pct} if pct is not None else None
        heartbeat(rid=rid, body=body)

        now = time.monotonic()
        if now >= next_servo:
            report_servos(rid=rid)
            next_servo = now + servo_interval
        time.sleep(interval)


def _main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_heartbeat_loop()


if __name__ == "__main__":
    _main()
