"""SLO 조회 — 완주율과 최근 개입.

fleet 탭(§7.2)이 읽는 두 경로다. 판정 정의는 `app/slo.py`, 개입 기록의 구조는
`app/control_audit.py` 에 있고 여기에는 HTTP 만 있다.
"""

from fastapi import APIRouter, Query

from .. import slo
from ..control_audit import INTERVENTION_ACTIONS
from ..db import get_pool
from ..schemas import ControlAuditOut, ControlAuditPage, SloWindowOut

router = APIRouter(tags=["slo"])

# 목록과 집계를 한 쿼리에서 가져온다. 따로 두 번 물으면 그 사이 들어온 개입
# 때문에 "총 21건 중 익명 3건" 처럼 합이 안 맞는 응답이 나간다.
#
# 윈도 함수는 LIMIT **전에** 계산된다. 그래서 count(*) OVER () 는 잘리기 전
# 전체 건수다 — 익명 비율을 "최근 20건 중" 이 아니라 누적으로 보려면 이게
# 필요하다.
_AUDIT_SQL = """
    SELECT audit_id, occurred_at, robot_id, session_id,
           action, argument, actor, actor_source,
           count(*) OVER ()                             AS total,
           count(*) FILTER (WHERE actor IS NULL) OVER () AS anonymous
    FROM control_audit
    ORDER BY occurred_at DESC, audit_id DESC
    LIMIT $1
"""


@router.get("/slo/completion", response_model=SloWindowOut)
async def session_completion(
    window: int = Query(
        slo.DEFAULT_WINDOW, ge=1, le=500,
        description="이동창 크기. 기본 50 은 §1.2 의 판정 창이다"),
) -> SloWindowOut:
    """직전 N세션의 완주율과 오차 예산 잔량.

    **완주와 성공은 다르다.** 세 단계를 다 돌고 completed 로 끝났어도 그
    구간에 사람이 손을 댔으면 실패로 센다(§1.1). 그 구분이 이 API 의 존재
    이유이고, 감사 로그(로드맵 4)가 선행 조건이었던 이유다.

    창 크기를 열어둔 것은 조사용이다. 기본값이 정본이고, 다른 값으로 물어보는
    것은 "최근 10건만 보면 어떤가" 같은 확인이다 — 오차 예산 판정은 항상
    창 기준으로 계산되므로 window 를 줄이면 예산도 같이 줄어든다.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            slo.WINDOW_SQL, window, slo.intervention_actions())
    return slo.judge(rows, window)


@router.get("/control-audit", response_model=ControlAuditPage)
async def list_control_audit(
    limit: int = Query(20, ge=1, le=200),
) -> ControlAuditPage:
    """최근 제어 개입. 누가 무엇을 눌렀는가.

    §1.1 판정 대상만 걸러 내려주지 않는다. 병원 도메인의 감사 요건은 `goto`
    까지 포함하고, 판정에 안 쓰이는 명령이 사고 조사에서 실마리가 되는 일이
    잦다. 대신 각 행에 `intervention` 을 달아 프론트가 백엔드와 다른 집합으로
    다시 거르지 않게 한다.

    total 과 anonymous 는 목록이 아니라 **전체**에 대한 값이다. 익명 비율은
    "최근 20건 중 몇 건" 이 아니라 누적으로 봐야 클라이언트가 헤더를
    빠뜨리기 시작한 것을 알아챌 수 있다.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(_AUDIT_SQL, limit)

    return ControlAuditPage(
        # 행이 없으면 윈도 함수도 없다. 빈 표는 총 0건이다.
        total=rows[0]["total"] if rows else 0,
        anonymous=rows[0]["anonymous"] if rows else 0,
        limit=limit,
        items=[
            ControlAuditOut(
                audit_id=row["audit_id"],
                occurred_at=row["occurred_at"],
                robot_id=row["robot_id"],
                session_id=row["session_id"],
                action=row["action"],
                argument=row["argument"],
                actor=row["actor"],
                actor_source=row["actor_source"],
                intervention=row["action"] in INTERVENTION_ACTIONS,
            )
            for row in rows
        ],
    )
