"""제어 개입 기록 — `control_audit` 적재.

누가 로봇에 손을 댔는지 남기는 유일한 경로다. 표의 설계 근거는
`database/migrations/011_control_audit.sql`, actor 를 읽는 규칙은 `actor.py`.

## 왜 효과보다 먼저 기록하는가

§1.1 은 "세션 구간에 `system_stop` · `system_restart` · `localize` **order 없음**"
이라고 적혀 있다. 실행 없음이 아니라 order 없음이다. 감사 로그의 주어는 로봇의
행동이 아니라 **사람의 판단**이다 — 조작자가 누른 정지가 새 명령에 덮여 로봇까지
못 갔더라도, 그 세션의 "무개입 완주" 주장은 이미 깨졌다.

순서를 뒤집으면 반대 방향으로 틀린다. 실행됐는데 기록이 없으면 개입한 세션이
성공으로 집계되고 SLO 가 **실제보다 좋아 보인다.** 계기판은 나쁜 쪽으로 틀려야
안전하다.

## 왜 실패해도 진행하는가

기록이 제어를 막으면 안 된다. DB 블립 하나로 조작자가 비상정지를 못 누르는
구조가 되는 것이 익명 행 몇 개보다 나쁘다. 그래서 이 모듈의 함수는 예외를
바깥으로 내보내지 않는다.

대신 그 대가가 남는다. 세션이 이미 열려 있는 상태의 짧은 DB 장애에서는 세션은
멀쩡히 살아남고 감사 행만 빠져, 개입이 SLO 에 안 잡힌다. 순서를 앞으로 옮겨도
안 없어지는 구멍이라 **보이게** 두는 것으로 갚는다 — 실패를 error 로 남기고,
fleet 탭이 그 건수를 익명 비율 옆에 띄운다. "이 창의 판정은 근거가 몇 건
부족하다" 를 화면이 말할 수 있어야 한다.

## 기록과 판정을 분리한다

`action` 은 들어온 명령을 전부 남긴다. 병원 도메인의 감사 요건은 `goto` 까지
포함하고, 무엇이 나중에 문제의 실마리가 될지는 지금 모른다. SLO 판정에 쓰는
것은 그중 좁은 집합(`INTERVENTION_ACTIONS`)뿐이다. 둘을 한 집합으로 합치면
"감사에 남기려고 넓혔더니 완주율이 떨어지는" 사고가 난다.
"""

from __future__ import annotations

import logging
import uuid

from .actor import Actor
from .db import get_pool

log = logging.getLogger("mingky")

TELEOP_ATTACH = "teleop_attach"
TELEOP_DETACH = "teleop_detach"

# §1.1 의 "관리자 개입 없음" · "teleop 없음" 판정 대상.
#
# teleop 은 attach 만 본다. 점유했다는 사실이 판정 근거이고, detach 는 구간
# 길이를 알고 싶을 때 쓰는 부가 정보다.
#
# set_mode(estop|manual) 이 여기 없는 것은 §1.1 이 수동 정지를 events
# (`robot.paused` · `robot.estop_engaged`)로 판정하기로 정해뒀기 때문이다.
# 정본을 두 곳에 두지 않는다(원칙 1). 다만 로봇이 명령을 받기 전에 세션이
# 끝나면 이벤트가 안 남으므로, 실측에서 놓치는 사례가 보이면 여기로 옮기는
# 것을 검토한다 — 그때는 §1.1 문서를 먼저 고친다.
INTERVENTION_ACTIONS = frozenset({
    "system_stop",
    "system_restart",
    "localize",
    TELEOP_ATTACH,
})

# session_id 를 인자로 안 받으면 발행 '시점'의 활성 세션을 서브쿼리로 스냅샷한다.
#
# 따로 SELECT 를 돌리지 않는 이유는 왕복이 하나 줄어서만이 아니다. 조회와 적재
# 사이에 세션이 끝나면 이미 끝난 세션에 개입이 달린다. 한 문장 안에서 잡으면
# 그 틈이 없다.
#
# 서브쿼리가 여러 행을 돌려줄 걱정은 003 의 uq_active_session_robot 이 막는다 —
# 로봇당 활성 세션은 하나뿐이다.
#
# $1 에 캐스트를 붙인 것은 취향이 아니라 필수다. 같은 파라미터가 INSERT 대상
# 컬럼과 WHERE 비교에 동시에 쓰이면 PostgreSQL 이 한쪽은 varchar, 다른 쪽은
# text 로 추론해서 "inconsistent types deduced for parameter $1" 로 거부한다.
# 실제 DB 에서만 나는 오류다 — 가짜 커넥션을 쓰는 단위 테스트는 통과한다.
_INSERT = """
    INSERT INTO control_audit (
        robot_id, session_id, action, argument, actor, actor_source, order_id)
    VALUES (
        $1::varchar,
        COALESCE($2::bigint, (
            SELECT session_id FROM guidance_sessions
            WHERE robot_id = $1::varchar AND ended_at IS NULL
        )),
        $3, $4, $5, $6, $7)
"""


async def record(
    robot_id: str,
    action: str,
    actor: Actor,
    *,
    argument: str | None = None,
    order_id: uuid.UUID | None = None,
    session_id: int | None = None,
) -> bool:
    """개입 한 건을 남긴다. 남겼으면 True.

    돌려주는 값을 호출부가 흐름 제어에 쓰면 안 된다. False 는 "기록에 실패했으니
    명령을 취소하라" 가 아니라 "이 창의 판정 근거가 한 건 비었다" 는 뜻이다.
    """
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                _INSERT, robot_id, session_id, action, argument,
                actor.name, actor.source, order_id)
        return True
    except Exception as exc:
        # 여기서 예외를 올리면 감사가 제어를 막는다. 대신 크게 남긴다 —
        # 이 로그가 SLO 판정에 뚫린 구멍의 유일한 흔적이다.
        log.error(
            "감사 기록 실패 — 명령은 그대로 진행한다: robot=%s action=%s "
            "actor=%s: %s", robot_id, action, actor.name, exc)
        return False
