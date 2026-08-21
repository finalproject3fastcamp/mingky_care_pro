"""약국 조제 화면 — HTTP 엔드포인트.

원본 Flask 판(`omx/web/app.py`)을 대체한다. 상태·워커·SSE 는 `..pharmacy`
모듈로 옮겼고, 여기는 요청/응답 변환만 담는다.

## 경로 규칙

기존 관제 라우터가 `/robots`, `/events` 처럼 영어 경로를 쓰므로 여기도 맞춘다.
화면에 보이는 문구 (환자·처방 필드명) 는 DB 시드(`001_initial_data.sql`) 의
한글 컬럼을 그대로 반영한다.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .. import pharmacy

log = logging.getLogger("mingky.pharmacy")

router = APIRouter(prefix="/pharmacy", tags=["pharmacy"])


@router.get("/prescriptions")
async def get_prescriptions() -> dict:
    """약품 + 병명별 처방 조합. 무작위를 눌러도 여기는 원본이다."""
    return await pharmacy.prescriptions()


@router.get("/patients")
async def search_patients(q: str = Query("", description="이름·생년월일·환자ID·병명")) -> dict:
    """빈 검색어면 전체를 돌려준다."""
    return await pharmacy.patients(q)


@router.get("/random-prescriptions")
async def get_random_prescriptions() -> JSONResponse:
    """모든 처방의 색 조합·순서를 새로 뽑는다.

    실제 모드에서 트레이 카메라가 검은 화면만 주면 503, 알약이 하나도 없으면
    400. 서버가 오류의 이유를 문자열로 넘기고 화면이 그대로 보여준다.
    """
    body, status = await pharmacy.random_prescriptions()
    return JSONResponse(body, status_code=status)


@router.get("/policies")
async def get_policies() -> dict:
    return pharmacy.policies()


@router.get("/tray")
async def get_tray() -> dict:
    """실제 모드면 조제 파트의 top 카메라로 센다 — 몇 초 걸린다."""
    return await pharmacy.read_tray()


# robot_id 는 어느 OMX 스테이션인지다. 생략하면 조제 기본 스테이션(omx-01).
# 조제 박스와 포장 박스가 서로 다른 job 으로 동시에 돌 수 있어야 하므로,
# 조작 엔드포인트가 모두 robot_id 를 받는다 (기본값으로 하위호환 유지).
def _robot(robot_id: str | None) -> str | None:
    return robot_id or None


@router.post("/dispense")
async def post_dispense(request: Request,
                        robot_id: str | None = Query(None)) -> JSONResponse:
    """조제 시작 — `{환자, 처방코드, 조합, 정책}`.

    조합이 비어 있으면 처방코드의 원본을 쓴다. 화면이 무작위로 다시 뽑았다면
    반드시 조합을 함께 보내야 한다 — 안 그러면 로봇이 화면과 다른 순서로 집는다.
    """
    body = await request.json()
    result, status = await pharmacy.start_dispense(body, _robot(robot_id))
    return JSONResponse(result, status_code=status)


@router.post("/stop")
async def post_stop(robot_id: str | None = Query(None)) -> dict:
    """중단을 요청한다. 실제 워커는 홈 복귀 후 종료하므로 몇 초 걸린다."""
    return await pharmacy.stop_dispense(_robot(robot_id))


@router.post("/pack")
async def post_pack(robot_id: str | None = Query(None)) -> JSONResponse:
    result, status = await pharmacy.start_pack(_robot(robot_id))
    return JSONResponse(result, status_code=status)


@router.post("/reset")
async def post_reset(robot_id: str | None = Query(None)) -> JSONResponse:
    result, status = await pharmacy.reset_state(_robot(robot_id))
    return JSONResponse(result, status_code=status)


@router.get("/state")
async def get_state(robot_id: str | None = Query(None)) -> dict:
    return await pharmacy.snapshot(_robot(robot_id))


@router.get("/progress")
async def get_progress() -> StreamingResponse:
    """조제 진행 상황을 SSE 로 흘려보낸다.

    여러 브라우저가 붙어도 각자 큐를 받아 같은 이벤트를 함께 본다.
    `X-Accel-Buffering: no` 는 앞단 nginx/traefik 이 응답을 버퍼링해서
    스트림이 청크 단위로만 도착하는 것을 막는다.
    """
    return StreamingResponse(
        pharmacy.subscribe(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
