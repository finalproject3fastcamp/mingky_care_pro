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
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

log = logging.getLogger("mingky.omx.report")

DEFAULT_BACKEND_URL = "https://mingkycarepro.site/api"
SOURCE_NODE = "omx.report.reporter"

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
def heartbeat(*, rid: str | None = None, base_url: str | None = None) -> int | None:
    """생존 신호만 보낸다(본문 없음). 실패하면 조용히 버린다 — 재전송 금지.

    본문을 붙이지 않는다. `{}` 를 보내면 서버가 기본값(system_state=unknown 등)
    으로 runtime 을 덮어써서, 순수 생존 핑이 상태 보고로 둔갑한다.
    """
    rid = _resolve_robot_id(rid)
    if not rid:
        return None
    url = f"{base_url or backend_url()}/robots/{rid}/heartbeat"
    req = urllib.request.Request(url, data=b"", method="POST")
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
        headers={"Content-Type": "application/json"})
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
    log.info("OMX 리포터 시작 — %s → %s (매 %.1fs)", rid, backend_url(), interval)
    while True:
        heartbeat(rid=rid)
        time.sleep(interval)


def _main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_heartbeat_loop()


if __name__ == "__main__":
    _main()
