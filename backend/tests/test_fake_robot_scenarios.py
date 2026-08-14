"""가짜 로봇 시나리오가 이벤트 정본을 지키는지 본다.

monitoring-spec.md §9.1 이 하네스에 기대하는 역할 중 하나다 — "정본이 바뀌면
가짜 로봇이 먼저 깨지므로 정본 준수 검사 역할까지 한다". 그 '먼저 깨진다' 를
실제로 만드는 것이 이 파일이다. 서버도 DB 도 필요 없으니 CI 단위 잡에서 돈다.

하네스는 backend 를 import 하지 않고 config/event_codes.yaml 을 따로 읽는다.
로봇 쪽을 흉내내는 별개 프로그램이라 그게 맞지만, 정본 로더가 둘이 되었으니
둘이 같은 답을 내는지도 여기서 잠근다.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS_DIR = REPO_ROOT / "tools" / "fake_robot"
SCENARIO_DIR = HARNESS_DIR / "scenarios"

# 하네스는 패키지가 아니라 실행 스크립트다. tools/ 에 __init__.py 를 뿌려
# 패키지로 만드는 것보다 여기서 경로를 한 줄 넣는 편이 낫다.
sys.path.insert(0, str(HARNESS_DIR))

import fake_robot  # noqa: E402

from app.event_codes import EventCodeRegistry  # noqa: E402


def _canon():
    return fake_robot.Canon.load()


def _scenario_from(tmp_path, body: str):
    path = tmp_path / "scenario.yaml"
    path.write_text(body, encoding="utf-8")
    return fake_robot.load_scenario(path)


SCENARIOS = sorted(SCENARIO_DIR.glob("*.yaml"))


def test_scenarios_exist():
    """glob 이 비면 아래 파라미터 테스트가 조용히 0건이 된다."""
    assert SCENARIOS


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_every_scenario_conforms_to_the_canon(path):
    problems = fake_robot.validate(fake_robot.load_scenario(path), _canon())

    assert problems == [], "\n".join(problems)


def test_the_two_canon_loaders_agree():
    """하네스와 백엔드가 같은 파일을 따로 읽는다. 갈라지면 안 된다.

    갈라지면 하네스가 통과시킨 시나리오를 백엔드가 오배선으로 잡거나 그 반대가
    된다. 그 상태에서는 E2E 실패가 코드 버그인지 로더 불일치인지 구분되지 않는다.
    """
    harness = _canon()
    backend = EventCodeRegistry.load()

    for code in backend._codes:
        assert harness.is_known(code), code
        assert harness.level_of(code) == backend.level_of(code), code
        assert harness.allowed_robot_types(code) == \
            backend.allowed_robot_types(code), code


def test_unknown_code_is_caught(tmp_path):
    scenario = _scenario_from(tmp_path, """
name: t
robots: [{ id: pinky-01, type: mobile }]
steps:
  - { robot: pinky-01, action: event, code: bogus.code }
""")
    problems = fake_robot.validate(scenario, _canon())

    assert len(problems) == 1
    assert "bogus.code" in problems[0]


def test_robot_type_violation_is_caught(tmp_path):
    """팔이 주행 이벤트를 내는 시나리오는 서버에 붙기 전에 걸린다."""
    scenario = _scenario_from(tmp_path, """
name: t
robots: [{ id: omx-01, type: manipulator }]
steps:
  - { robot: omx-01, action: event, code: nav.goal_succeeded }
""")
    problems = fake_robot.validate(scenario, _canon())

    assert len(problems) == 1
    assert "nav.goal_succeeded" in problems[0]
    assert "manipulator" in problems[0]


def test_expect_mismatch_skips_only_the_type_check(tmp_path):
    """오배선을 일부러 만드는 시나리오라고 아무 코드나 쓸 수는 없다."""
    scenario = _scenario_from(tmp_path, """
name: t
robots: [{ id: omx-01, type: manipulator }]
steps:
  - { robot: omx-01, action: event, code: nav.goal_succeeded, expect_mismatch: true }
  - { robot: omx-01, action: event, code: bogus.code, expect_mismatch: true }
""")
    problems = fake_robot.validate(scenario, _canon())

    assert len(problems) == 1
    assert "bogus.code" in problems[0]


def test_level_drift_from_the_canon_is_caught(tmp_path):
    """확률적 실패를 error 로 올리는 식의 드리프트를 잡는다."""
    scenario = _scenario_from(tmp_path, """
name: t
robots: [{ id: pinky-01, type: mobile }]
steps:
  - { robot: pinky-01, action: event, code: nav.stuck, level: error }
""")
    problems = fake_robot.validate(scenario, _canon())

    assert len(problems) == 1
    assert "warning" in problems[0]


def test_step_referencing_an_undeclared_robot_is_caught(tmp_path):
    scenario = _scenario_from(tmp_path, """
name: t
robots: [{ id: pinky-01, type: mobile }]
steps:
  - { robot: ghost-99, action: event, code: robot.paused }
""")
    problems = fake_robot.validate(scenario, _canon())

    assert any("ghost-99" in p for p in problems)


def test_all_problems_are_reported_at_once(tmp_path):
    """하나 고치고 다시 돌려서 다음 하나를 보는 것보다 한 번에 다 보는 게 빠르다."""
    scenario = _scenario_from(tmp_path, """
name: t
robots: [{ id: omx-01, type: manipulator }]
steps:
  - { robot: omx-01, action: event, code: nav.goal_sent }
  - { robot: omx-01, action: event, code: bogus.code }
  - { robot: ghost-99, action: event, code: robot.paused }
""")
    problems = fake_robot.validate(scenario, _canon())

    assert len(problems) == 3


def test_unknown_robot_key_fails_loudly(tmp_path):
    """오타난 키가 조용히 무시되면 시나리오가 의도와 다르게 돈다."""
    with pytest.raises(ValueError, match="battery"):
        _scenario_from(tmp_path, """
name: t
robots: [{ id: pinky-01, battery: 50 }]
steps: []
""")
