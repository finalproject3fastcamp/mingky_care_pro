"""검사실이 겹칠 때 다음 목적지를 고르는 판정.

시드의 세 증상이 전부 1단계가 X-ray 라(`001_initial_data.sql`), 환자 둘을
받으면 **반드시** 겹친다. 기다리는 대신 순서를 바꾸는 것이 이 모듈의 일이다.

여기서 지키는 계약은 넷이다.

  1. 앞의 것이 비어 있으면 굳이 안 바꾼다
  2. 앞의 것이 차 있으면 뒤에서 비어 있는 첫 번째를 먼저 간다
  3. **가는 중이면 목적지를 안 바꾼다** — 왔다 갔다 하면 환자가 못 따라온다
  4. 전부 차 있으면 계획대로 두고 기다린다 (갈 곳 없음과 기다림은 다르다)
"""

from app.fleet_rooms import Choice, Step, choose_next, summarize


def steps(*names, started_at=None):
    """계획 순서대로 남은 단계들. started_at 은 이미 출발한 단계의 이름."""
    return [Step(i, name, started=(name == started_at))
            for i, name in enumerate(names, start=1)]


# ------------------------------------------------------------------ 계약 1

def test_planned_order_wins_when_nothing_is_busy():
    choice = choose_next(steps('X-ray', 'CT', 'MRI'), busy={})

    assert choice.visit_name == 'X-ray'
    assert choice.step_order == 1
    assert choice.reordered is False


def test_a_busy_room_that_is_not_next_changes_nothing():
    """뒤에 있는 방이 차 있어도 지금 갈 곳은 그대로다."""
    choice = choose_next(steps('X-ray', 'CT'), busy={'CT': 'pinky-02'})

    assert choice.visit_name == 'X-ray'
    assert choice.reordered is False


# ------------------------------------------------------------------ 계약 2

def test_busy_first_room_is_skipped():
    """시드가 만드는 바로 그 상황 — 둘 다 X-ray 부터다."""
    choice = choose_next(
        steps('X-ray', 'CT', 'MRI'), busy={'X-ray': 'pinky-01'})

    assert choice.visit_name == 'CT'
    assert choice.step_order == 2
    assert choice.reordered is True
    # 화면이 "X-ray 사용 중 — CT 먼저" 를 말할 근거가 남아야 한다.
    assert choice.skipped_visit == 'X-ray'
    assert choice.blocked_by == 'pinky-01'


def test_skips_to_the_first_free_room_not_the_last():
    choice = choose_next(
        steps('X-ray', 'CT', 'MRI', '물리치료실'),
        busy={'X-ray': 'pinky-01', 'CT': 'pinky-01'})

    assert choice.visit_name == 'MRI'
    assert choice.reordered is True
    assert choice.skipped_visit == 'X-ray'


def test_reordering_keeps_the_planned_step_order():
    """step_order 는 계획 스냅샷이라 바뀌지 않는다 (013 마이그레이션).

    순서를 바꿔도 '이 환자에게 원래 무엇을 하려 했는가' 는 남아야 한다.
    """
    choice = choose_next(
        steps('X-ray', 'CT'), busy={'X-ray': 'pinky-01'})

    assert choice.step_order == 2, 'CT 의 계획 순서는 2 그대로여야 한다'


# ------------------------------------------------------------------ 계약 3

def test_a_started_step_is_never_changed():
    """가는 중에 목적지가 바뀌면 로봇이 왔다 갔다 하고 환자가 못 따라온다."""
    choice = choose_next(
        steps('X-ray', 'CT', started_at='X-ray'),
        busy={'X-ray': 'pinky-01'})      # 상대가 쓰는 중이어도

    assert choice.visit_name == 'X-ray'
    assert choice.reordered is False


def test_started_step_wins_even_if_it_is_not_first():
    choice = choose_next(
        steps('X-ray', 'CT', 'MRI', started_at='MRI'), busy={})

    assert choice.visit_name == 'MRI'


# ------------------------------------------------------------------ 계약 4

def test_all_busy_keeps_the_plan_and_says_who_blocks():
    """갈 곳이 없는 것과 기다리는 것은 다르다.

    전부 차 있으면 계획대로 두고 기다린다 — 그 기다림은 복도에서
    `fleet_reserve` 가 관리한다.
    """
    choice = choose_next(
        steps('X-ray', 'CT'),
        busy={'X-ray': 'pinky-01', 'CT': 'pinky-01'})

    assert choice.visit_name == 'X-ray'
    assert choice.reordered is False
    assert choice.blocked_by == 'pinky-01'


def test_no_remaining_steps_means_done():
    choice = choose_next([], busy={'X-ray': 'pinky-01'})

    assert choice == Choice(None, None)


# ------------------------------------------------------------------ 실제 시드

def test_the_seeded_conditions_actually_collide():
    """이 기능이 왜 필요한지를 시드로 못박는다.

    세 증상이 전부 X-ray 로 시작한다. 이 단언이 깨졌다면 시드가 바뀐 것이고,
    그때는 순서 재정렬의 우선순위를 다시 따져도 된다.
    """
    import pathlib
    import re

    seed = (pathlib.Path(__file__).resolve().parents[2]
            / "database/seeds/001_initial_data.sql").read_text(encoding="utf-8")
    firsts = set(re.findall(r"\('([^']+)', 1, '([^']+)'\)", seed))
    assert firsts, "examination_steps 시드를 못 읽었다"
    assert {visit for _, visit in firsts} == {'X-ray'}, (
        f"1단계가 X-ray 만이 아니다: {firsts}")


# ------------------------------------------------------------- 여러 대 배정

class Row(dict):
    """asyncpg Record 처럼 대괄호로 읽히기만 하면 된다."""
    __getattr__ = dict.get


def test_two_idle_robots_do_not_both_avoid_the_same_room():
    """실측으로 잡은 버그 — 서로 양보하다 둘 다 X-ray 를 피했다.

    아직 아무도 출발하지 않았으면 X-ray 는 비어 있다. 그런데 각자 따로
    판정하면 둘 다 '상대가 쓸 것' 으로 보고 피한다. 한 대는 가야 한다.
    """
    current = [Row(robot_id='pinky-01', session_id=1, visit_name=None),
               Row(robot_id='pinky-02', session_id=2, visit_name=None)]
    remaining = [
        Row(session_id=1, step_order=1, visit_name='X-ray', started=False),
        Row(session_id=1, step_order=2, visit_name='임상병리실', started=False),
        Row(session_id=2, step_order=1, visit_name='X-ray', started=False),
        Row(session_id=2, step_order=2, visit_name='CT', started=False),
    ]
    out = summarize(current, remaining)

    assert out['pinky-01'].visit_name == 'X-ray', '아무도 X-ray 에 안 갔다'
    assert out['pinky-01'].reordered is False
    assert out['pinky-02'].visit_name == 'CT'
    assert out['pinky-02'].reordered is True
    assert out['pinky-02'].blocked_by == 'pinky-01'


def test_a_started_room_is_busy_for_the_other_robot():
    """이미 출발한 방은 확실히 차 있다."""
    current = [Row(robot_id='pinky-01', session_id=1, visit_name='X-ray'),
               Row(robot_id='pinky-02', session_id=2, visit_name=None)]
    remaining = [
        Row(session_id=1, step_order=1, visit_name='X-ray', started=True),
        Row(session_id=2, step_order=1, visit_name='X-ray', started=False),
        Row(session_id=2, step_order=2, visit_name='CT', started=False),
    ]
    out = summarize(current, remaining)

    assert out['pinky-01'].visit_name == 'X-ray'
    assert out['pinky-02'].visit_name == 'CT'
    assert out['pinky-02'].skipped_visit == 'X-ray'


def test_assignment_is_deterministic():
    """순서를 바꿔 넣어도 같은 답이라야 서로 양보하다 멈추지 않는다."""
    current = [Row(robot_id='pinky-02', session_id=2, visit_name=None),
               Row(robot_id='pinky-01', session_id=1, visit_name=None)]
    remaining = [
        Row(session_id=2, step_order=1, visit_name='X-ray', started=False),
        Row(session_id=2, step_order=2, visit_name='CT', started=False),
        Row(session_id=1, step_order=1, visit_name='X-ray', started=False),
        Row(session_id=1, step_order=2, visit_name='임상병리실', started=False),
    ]
    out = summarize(current, remaining)

    assert out['pinky-01'].visit_name == 'X-ray'
    assert out['pinky-02'].visit_name == 'CT'
