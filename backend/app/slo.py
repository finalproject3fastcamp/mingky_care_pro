"""세션 완주율 — §1.1 판정과 §1.2 이동창.

목표는 문장 하나다. **환자 안내 세션의 90% 가 수동 개입 없이 완주한다.**
이 모듈은 그 문장을 숫자로 바꾸는 유일한 곳이다.

## 무엇을 성공으로 보는가

§1.1 의 네 조건을 그대로 옮긴다. 넷 중 하나라도 어긋나면 실패다.

  1. 정상 종료         guidance_sessions.end_reason = 'completed'
  2. teleop 없음       control_audit 에 teleop_attach 없음
  3. 수동 정지 없음    events 의 robot.paused(operator) · estop_engaged(remote) 없음
  4. 관리자 개입 없음  control_audit 에 관리 명령 없음

2·4 는 control_audit 하나로 본다 — 그래서 감사 로그가 이 계산의 선행 조건이다.

## "수동" 을 어떻게 가리는가

§1.1 은 "robot.paused(수동)" 이라고만 적혀 있는데, 정본을 보면 그 코드의
payload 에는 `source` 가 없고 `reason` 만 있다. 실제 발행부에서 어휘를 확인했다.

  robot.paused.reason      operator | obstacle | communication_loss
                           (mingky_battery_guard/emergency_stop.py)
  robot.estop_engaged.source  remote | gate | startup
                           (mingky_teleop/mode_manager.py)

**사람이 누른 것만 센다.** `obstacle` · `communication_loss` 는 로봇의 안전
게이트가 스스로 건 것이고, `gate` 도 마찬가지다. 이걸 개입으로 세면 §1.1 이
"실패로 치지 않는다" 고 못박은 `nav.stuck` 을 다른 이름으로 세는 것과 같아진다.
로봇이 스스로 서고 스스로 복구해 완주했으면 성공이다.

`startup` 은 부팅 직후의 초기 상태 통보라 개입이 아니다.

## 왜 끝난 세션만 보는가

진행 중인 세션은 판정이 미완이다. 창에 넣으면 완주율이 진행 중 세션 수만큼
흔들리고, 같은 시각에 두 번 물으면 다른 답이 나온다.

정렬도 `started_at` 이 아니라 `ended_at` 이다. 창의 정의가 "가장 최근에 판정이
끝난 50건" 이기 때문이다. 오래 끌다 늦게 끝난 세션은 늦게 창에 들어온다.
"""

from __future__ import annotations

import math
from fractions import Fraction

from .control_audit import INTERVENTION_ACTIONS
from .schemas import SloSessionOut, SloWindowOut

# §1.2 의 이동창. 하루치로 판정하면 이 규모에서는 잡음에 흔들린다.
DEFAULT_WINDOW = 50

# §1 의 목표. 예산 계산에는 분수를 쓴다.
#
# 실측이다 — `math.floor(50 * (1 - 0.90))` 은 5 가 아니라 **4** 다.
# 0.90 이 이진수로 정확히 표현되지 않아 1 - 0.90 = 0.09999999999999998 이고,
# 곱하면 4.999999999999999 가 된다. 예산이 하나 줄면 배포 중단 규칙이 실패
# 한 건 일찍 걸리는데, 그 차이는 화면에 5 와 4 로만 보여서 아무도 못 알아챈다.
TARGET_RATIO = Fraction(9, 10)
TARGET = float(TARGET_RATIO)

# end_reason 중 성공은 이것 하나다. aborted · battery · patient_lost ·
# robot_offline · system_failure · fire 는 전부 실패다.
SUCCESS_END_REASON = "completed"

# 실패 사유 라벨. 화면이 원인별로 묶을 수 있게 코드로 돌려주고, 문구는
# 프론트가 정한다.
FAILURE_ABNORMAL_END = "abnormal_end"
FAILURE_MANUAL_STOP = "manual_stop"
FAILURE_OPERATOR_ORDER = "operator_order"

# 세션별 판정 재료. EXISTS 두 개가 붙지만 창이 50행이고 양쪽 다
# session_id 인덱스를 탄다(events 는 idx_events_session, control_audit 은
# idx_control_audit_session).
WINDOW_SQL = """
    WITH recent AS (
        SELECT session_id, robot_id, started_at, ended_at, end_reason
        FROM guidance_sessions
        WHERE ended_at IS NOT NULL
        ORDER BY ended_at DESC
        LIMIT $1
    )
    SELECT r.session_id, r.robot_id, r.started_at, r.ended_at, r.end_reason,
           EXISTS (
               SELECT 1 FROM events e
               WHERE e.session_id = r.session_id
                 AND (
                     -- 대시보드에서 건 비상정지. gate(안전 게이트) · startup
                     -- (부팅 통보)은 사람이 아니다.
                     (e.event_code = 'robot.estop_engaged'
                      AND e.payload->>'source' = 'remote')
                     -- 사람이 누른 정지. obstacle · communication_loss 는
                     -- 로봇이 스스로 선 것이라 개입이 아니다.
                     OR (e.event_code = 'robot.paused'
                         AND e.payload->>'reason' = 'operator')
                 )
           ) AS manual_stop,
           EXISTS (
               SELECT 1 FROM control_audit a
               WHERE a.session_id = r.session_id
                 AND a.action = ANY($2::text[])
           ) AS operator_order
    FROM recent r
    ORDER BY r.ended_at DESC
"""


def intervention_actions() -> list[str]:
    """SQL 파라미터용. 정렬해서 넘겨야 쿼리 계획이 매번 같다."""
    return sorted(INTERVENTION_ACTIONS)


def _failures_of(row) -> list[str]:
    """이 세션이 §1.1 의 어느 조건에서 어긋났는가.

    첫 번째에서 멈추지 않는다. 개입이 있었고 종료도 비정상인 세션은 둘 다
    보여야 원인을 찾을 때 헛짚지 않는다.
    """
    failures = []
    if row["end_reason"] != SUCCESS_END_REASON:
        failures.append(FAILURE_ABNORMAL_END)
    if row["manual_stop"]:
        failures.append(FAILURE_MANUAL_STOP)
    if row["operator_order"]:
        failures.append(FAILURE_OPERATOR_ORDER)
    return failures


def budget_total(window: int) -> int:
    """이 창에서 허용되는 실패 건수.

    50세션 × 10% = 5. 5건까지는 완주율이 정확히 90% 라 목표를 지킨 것이고,
    6건부터 위반이다(§1.2). 그래서 잔량 0 과 소진은 다른 상태다 — 잔량이 0 인
    창은 아직 목표 안이지만 다음 한 건이 위반이라는 뜻이다.

    "목표를 지키려면 최소 몇 건이 성공해야 하는가" 에서 거꾸로 뺀다.
    창이 50 이 아닐 때도 경계가 어긋나지 않는다 — 12세션이면 성공 11건이
    필요하므로 예산은 1이고, 실패 2건이면 83% 로 목표를 깬다.
    """
    required_success = math.ceil(window * TARGET_RATIO)
    return window - required_success


def judge(rows, window: int = DEFAULT_WINDOW) -> SloWindowOut:
    """창 하나를 판정한다. DB 를 모른다 — 행만 받는다.

    표본이 창보다 적어도 예산은 창 기준을 쓴다. 표본에 비례해 줄이면 세션이
    3건일 때 예산이 0 이 되어 첫 실패가 곧 소진이 된다. 대신 sessions_judged
    와 sample_complete 를 함께 내려, 화면이 "아직 표본이 모자란다" 를 말할 수
    있게 한다.
    """
    judged = [
        SloSessionOut(
            session_id=row["session_id"],
            robot_id=row["robot_id"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            end_reason=row["end_reason"],
            failures=_failures_of(row),
        )
        for row in rows
    ]
    failures = [session for session in judged if session.failures]
    used = len(failures)
    total = budget_total(window)

    return SloWindowOut(
        window=window,
        sessions_judged=len(judged),
        sample_complete=len(judged) >= window,
        success=len(judged) - used,
        failure=used,
        # 표본이 없으면 완주율이 없다. 0.0 으로 돌려주면 "한 건도 완주 못 했다"
        # 와 구분되지 않고, 화면은 그 둘을 아주 다르게 그려야 한다.
        completion_rate=(
            None if not judged else (len(judged) - used) / len(judged)),
        target=TARGET,
        budget_total=total,
        budget_used=used,
        budget_remaining=max(0, total - used),
        budget_exhausted=used > total,
        failed_sessions=failures,
    )
