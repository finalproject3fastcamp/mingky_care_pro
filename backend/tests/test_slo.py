"""완주율 판정과 오차 예산 산식 (§1.1 · §1.2).

`slo.judge()` 는 DB 를 모른다 — 행만 받는다. 그래서 여기서 확인하는 것은
집계 산술이고, SQL 이 그 행을 제대로 만드는지는 e2e 가 진짜 DB 로 본다.
둘을 갈라 둔 이유는 실패했을 때 원인이 산식인지 쿼리인지 바로 갈리게 하려는
것이다.

이 파일이 잠그는 경계는 셋이다.

  - 완주(end_reason)와 성공(§1.1 네 조건)이 다르다
  - 잔량 0 과 예산 소진이 다르다
  - 표본 없음과 완주율 0% 가 다르다
"""

import pytest

from app import slo


def _row(session_id=1, end_reason="completed", manual_stop=False,
         operator_order=False):
    """WINDOW_SQL 이 돌려주는 한 행. asyncpg Record 처럼 [] 로 읽힌다."""
    return {
        "session_id": session_id,
        "robot_id": "pinky-01",
        "started_at": "2026-08-15T09:00:00+00:00",
        "ended_at": "2026-08-15T09:20:00+00:00",
        "end_reason": end_reason,
        "manual_stop": manual_stop,
        "operator_order": operator_order,
    }


def test_completed_session_without_intervention_is_the_only_success():
    result = slo.judge([_row()], window=50)

    assert result.success == 1
    assert result.failure == 0
    assert result.failed_sessions == []


def test_completed_but_intervened_session_counts_as_failure():
    """완주가 성공을 뜻하지 않는다.

    end_reason 만 집계하면 이 세션이 성공으로 잡히고 SLO 가 실제보다 좋아
    보인다. 감사 로그를 먼저 세운 이유가 정확히 이 한 줄이다.
    """
    result = slo.judge([_row(operator_order=True)], window=50)

    assert result.success == 0
    assert result.failed_sessions[0].failures == [slo.FAILURE_OPERATOR_ORDER]


@pytest.mark.parametrize("end_reason", [
    "aborted", "battery", "patient_lost", "robot_offline", "system_failure",
    "fire",
])
def test_every_other_end_reason_is_a_failure(end_reason):
    """성공은 completed 하나뿐이다. 006·007 이 추가한 사유도 전부 실패다."""
    result = slo.judge([_row(end_reason=end_reason)], window=50)

    assert result.failed_sessions[0].failures == [slo.FAILURE_ABNORMAL_END]


def test_a_session_can_fail_for_more_than_one_reason():
    """첫 번째에서 멈추면 원인을 찾을 때 헛짚는다."""
    result = slo.judge(
        [_row(end_reason="aborted", manual_stop=True, operator_order=True)],
        window=50)

    assert result.failed_sessions[0].failures == [
        slo.FAILURE_ABNORMAL_END,
        slo.FAILURE_MANUAL_STOP,
        slo.FAILURE_OPERATOR_ORDER,
    ]
    # 사유가 셋이어도 세션은 한 건이다. 예산을 사유 수로 깎으면 안 된다.
    assert result.failure == 1
    assert result.budget_used == 1


def test_budget_is_five_failures_in_a_fifty_session_window():
    assert slo.budget_total(50) == 5


def test_five_failures_meet_the_target_and_six_break_it():
    """§1.2 의 경계. 50세션 중 5건은 정확히 90% 라 아직 목표 안이다.

    여기서 한 칸 밀리면 배포 중단 규칙이 하루 일찍 또는 늦게 걸린다.
    """
    sessions = ([_row(session_id=i, end_reason="aborted") for i in range(5)]
                + [_row(session_id=100 + i) for i in range(45)])

    result = slo.judge(sessions, window=50)

    assert result.completion_rate == pytest.approx(0.90)
    assert result.budget_remaining == 0
    # 잔량 0 과 소진은 다른 상태다. 다음 한 건이 위반이라는 뜻이지 이미
    # 위반인 것이 아니다.
    assert result.budget_exhausted is False

    one_more = slo.judge(sessions + [_row(session_id=999, end_reason="battery")],
                         window=50)
    assert one_more.budget_exhausted is True
    assert one_more.budget_remaining == 0


def test_small_sample_keeps_the_window_budget():
    """표본에 비례해 예산을 줄이면 세션 3건일 때 첫 실패가 곧 소진이다."""
    result = slo.judge([_row(session_id=1), _row(session_id=2)], window=50)

    assert result.budget_total == 5
    assert result.sessions_judged == 2
    assert result.sample_complete is False


def test_empty_window_has_no_completion_rate():
    """표본이 없으면 완주율은 존재하지 않는다.

    0.0 으로 돌려주면 "한 건도 완주 못 했다" 와 구분되지 않는다. 화면은 그
    둘을 아주 다르게 그려야 한다 — 하나는 안내이고 하나는 비상이다.
    """
    result = slo.judge([], window=50)

    assert result.completion_rate is None
    assert result.sessions_judged == 0
    assert result.budget_exhausted is False


def test_intervention_actions_are_sorted_for_a_stable_plan():
    assert slo.intervention_actions() == sorted(slo.intervention_actions())
    assert "teleop_attach" in slo.intervention_actions()


def test_window_sql_only_judges_finished_sessions():
    """진행 중인 세션이 창에 들어오면 같은 시각에 두 번 물어도 답이 달라진다."""
    assert "WHERE ended_at IS NOT NULL" in slo.WINDOW_SQL
    # 창의 정의는 '가장 최근에 판정이 끝난 N건' 이다. started_at 로 정렬하면
    # 오래 끌다 늦게 끝난 세션이 창에서 빠진다.
    assert "ORDER BY ended_at DESC" in slo.WINDOW_SQL


def test_manual_stop_only_counts_what_a_human_pressed():
    """자동 안전 정지를 개입으로 세면 안 된다.

    obstacle · communication_loss · gate 는 로봇의 안전 게이트가 스스로 건
    것이다. 이걸 실패로 세면 §1.1 이 "실패로 치지 않는다" 고 못박은
    nav.stuck 을 다른 이름으로 세는 셈이 된다.
    """
    # 주석은 뺀다. 이 SQL 은 자동 정지를 왜 세지 않는지 주석으로 적고 있어서,
    # 산문까지 훑으면 설명이 위반으로 잡힌다.
    statement = "\n".join(
        line for line in slo.WINDOW_SQL.splitlines()
        if not line.strip().startswith("--"))

    assert "'operator'" in statement and "'remote'" in statement
    for automatic in ("obstacle", "communication_loss", "gate", "startup"):
        assert automatic not in statement
