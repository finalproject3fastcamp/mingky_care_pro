"""안내 세션 ↔ 조제 연결 (로드맵 item 4).

안내 로봇이 약국에 도착하면 item 2 가 `pharmacy.arrived{session_id, patient_id}`
를 발행한다. 이 모듈이 그 이벤트를 소비해서 —

  1. 환자의 처방을 조제 스테이션(omx-01)에서 시작하고,
  2. `pharmacy.dispense_requested{session_id, patient_id, omx_robot_id}` 를 남기고,
  3. 조제가 끝나면 `pharmacy.dispense_completed{session_id, dispense_id}` 를 남긴다.

조제 실행 자체는 `app/pharmacy.py` 가 한다 — env 가 있으면 원격 러너(박스별 HTTP)로
프록시하고, 없으면(CI·데모) 시뮬레이터로 돈다. 여기는 세션 연결과 이벤트 발행만
맡는다.

## 왜 ingest 트랜잭션 밖에서 도는가

`pharmacy.arrived` 적재는 ingest 트랜잭션 안에서 커밋된다. 조제는 몇십 초~몇 분
걸리므로 그 트랜잭션 안에서 기다리면 events 적재 전체가 막힌다. notify 와 같은
원칙으로(backend/app/ingest.py) 커밋된 사실에 대해서만, 트랜잭션 밖에서
백그라운드 태스크로 시작한다.

## 약국 세션 스텝 완료는 item 2 담당

이 모듈은 `pharmacy.dispense_completed` 발행까지다. 그 신호로 약국 방문 단계를
어떻게 완료시키는지는 item 2(mingky_guide_manager)가 맡는다 — SEAM 계약.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from . import pharmacy
from .db import get_pool

log = logging.getLogger("mingky.pharmacy_link")

SOURCE_NODE = "backend.pharmacy"

# item 2 가 발행하는 코드. 우리는 소비만 한다(정의는 event_codes.yaml 에서 item 2).
ARRIVED_CODE = "pharmacy.arrived"
# 우리가 발행하는 코드(event_codes.yaml 맨 끝 조제-세션 연결 절).
DISPENSE_REQUESTED = "pharmacy.dispense_requested"
DISPENSE_COMPLETED = "pharmacy.dispense_completed"

_INSERT_EVENT = """
    INSERT INTO events (event_id, robot_id, session_id, occurred_at,
                        level, event_code, source_node, payload)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    ON CONFLICT (event_id) DO NOTHING
"""

_INSERT_JOB = """
    INSERT INTO dispense_jobs (dispense_id, session_id, patient_id, omx_robot_id)
    VALUES ($1, $2, $3, $4)
"""

_COMPLETE_JOB = """
    UPDATE dispense_jobs
    SET status = 'completed', completed_at = $2
    WHERE dispense_id = $1 AND status = 'requested'
"""

_ABORT_JOB = """
    UPDATE dispense_jobs
    SET status = 'aborted', completed_at = $2
    WHERE dispense_id = $1 AND status = 'requested'
"""


async def _emit(conn, robot_id: str, session_id: int | None,
                code: str, level: str, payload: dict) -> None:
    """조제-세션 이벤트를 events 에 남긴다. 로봇이 아니라 백엔드가 발행한다."""
    await conn.execute(
        _INSERT_EVENT,
        uuid.uuid4(),
        robot_id,
        session_id or None,          # session_id 0/None 은 NULL 로 (§6.2 와 같은 규칙)
        datetime.now(timezone.utc),
        level,
        code,
        SOURCE_NODE,
        json.dumps(payload, ensure_ascii=False),
    )


async def on_pharmacy_arrived(session_id: int, patient_id: str) -> None:
    """도착 → 세션 연결 조제 시작 → 완료 시 dispense_completed 발행.

    조제가 세션에 연결되는 유일한 진입점이다. 실패해도(연결 못 함·중복·조제 오류)
    이벤트 적재 파이프라인을 죽이지 않도록 예외를 삼킨다 — ingest 가 이 함수를
    커밋 뒤 백그라운드로 던진다.
    """
    robot_id = pharmacy.DEFAULT_ROBOT_ID     # 조제 스테이션 = omx-01
    try:
        처방 = await pharmacy.prescription_for_patient(patient_id)
        if 처방 is None or not 처방.get("조합"):
            log.warning("세션 %s 환자 %s 의 처방을 찾지 못해 조제를 건너뜁니다",
                        session_id, patient_id)
            return

        dispense_id = uuid.uuid4().hex[:12]
        policy_id = pharmacy.DEFAULT_POLICY

        pool = get_pool()
        async with pool.acquire() as conn:
            try:
                await conn.execute(_INSERT_JOB, dispense_id, session_id,
                                   patient_id, robot_id)
            except Exception:  # noqa: BLE001
                # uq_active_dispense_session — 도착 이벤트가 재전송돼 이미 조제가
                # 걸려 있으면 두 번 시작하지 않는다(멱등).
                log.info("세션 %s 에 이미 진행 중인 조제가 있어 건너뜁니다", session_id)
                return
            await _emit(conn, robot_id, session_id, DISPENSE_REQUESTED, "info",
                        {"session_id": session_id, "patient_id": patient_id,
                         "omx_robot_id": robot_id})

        # 조제 실행 — 완료까지 기다린다. 이 태스크는 ingest 밖이므로 오래 걸려도
        # 적재를 막지 않는다.
        ok = await pharmacy.run_session_dispense(dispense_id, 처방, policy_id, robot_id)

        now = datetime.now(timezone.utc)
        async with pool.acquire() as conn:
            if ok:
                await conn.execute(_COMPLETE_JOB, dispense_id, now)
                await _emit(conn, robot_id, session_id, DISPENSE_COMPLETED, "info",
                            {"session_id": session_id, "dispense_id": dispense_id})
            else:
                await conn.execute(_ABORT_JOB, dispense_id, now)
                log.warning("세션 %s 조제 %s 가 완주하지 못했습니다",
                            session_id, dispense_id)
    except Exception:  # noqa: BLE001
        log.exception("세션 연결 조제 실패 (session=%s, patient=%s)",
                      session_id, patient_id)


def schedule_from_event(payload: dict) -> None:
    """`pharmacy.arrived` payload 로 세션 연결 조제를 백그라운드로 시작한다.

    ingest 가 커밋 뒤에 부른다. payload 계약은 item 2 가 정한 SEAM —
    `{session_id: int, patient_id: string}`.
    """
    session_id = payload.get("session_id")
    patient_id = payload.get("patient_id")
    if not session_id or not patient_id:
        log.warning("pharmacy.arrived payload 가 불완전합니다: %s", payload)
        return
    try:
        asyncio.get_running_loop().create_task(
            on_pharmacy_arrived(int(session_id), str(patient_id)))
    except RuntimeError:
        # 실행 중인 루프가 없다(테스트 등 동기 컨텍스트). 그 경우는 호출부가
        # 직접 on_pharmacy_arrived 를 await 한다.
        log.debug("실행 중인 이벤트 루프가 없어 조제 태스크를 예약하지 못했습니다")
