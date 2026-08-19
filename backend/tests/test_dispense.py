"""조제 사이클 접기 (§7.2 · §7.3).

`dispense.summarize()` 는 DB 를 모른다 — 행만 받는다. slo.judge 와 같은 구조다.
여기서 확인하는 것은 접기 규칙이고, DETAIL_SQL 이 그 행을 제대로 만드는지는
e2e 가 진짜 DB 로 본다. 둘을 갈라 두면 실패했을 때 원인이 규칙인지 쿼리인지
바로 갈린다.

이 파일이 잠그는 경계는 셋이다.

  - 표본 없음과 성공률 0% 가 다르다
  - 재시도로 끝난 사이클은 완주다 (§4.4)
  - 진행 중인 사이클은 판정이 미완이라 창에 안 들어간다
"""

import json
from datetime import datetime, timedelta, timezone

from app import dispense

BASE = datetime(2026, 8, 16, 9, 0, tzinfo=timezone.utc)


def _ev(code: str, offset_sec: int = 0, robot_id: str = "omx-01", **payload):
    """DETAIL_SQL 한 행. asyncpg Record 처럼 [] 로 읽힌다."""
    return {
        "robot_id": robot_id,
        "event_code": f"manipulator.{code}",
        "occurred_at": BASE + timedelta(seconds=offset_sec),
        "payload": payload,
    }


def _cycle(index: int, *, picks=(1,), duration_ms=10_000, robot_id="omx-01"):
    """완주 사이클 하나. picks 는 각 pick 의 attempt 번호다."""
    start = index * 100
    events = [_ev("cycle_started", start, robot_id=robot_id,
                  dispense_id=f"d-{index}", medication_id="med-a")]
    for order, attempt in enumerate(picks, start=1):
        events.append(_ev("pick_succeeded", start + order, robot_id=robot_id,
                          medication_id="med-a", attempt=attempt,
                          duration_ms=4200))
    events.append(_ev("cycle_completed", start + 50, robot_id=robot_id,
                      dispense_id=f"d-{index}", duration_ms=duration_ms))
    return events


def _one(rows) -> "dispense.ManipulatorDetail":
    result = dispense.summarize(rows)
    return result["omx-01"]


def test_a_completed_cycle_counts_once():
    detail = _one(_cycle(1))

    assert detail.cycles_completed == 1
    assert detail.cycles_aborted == 0
    assert detail.window_cycles == 1
    assert detail.pick_success_rate == 1.0
    assert detail.cycle_time_p50_ms == 10_000


def test_no_pick_at_all_is_not_a_zero_percent_success_rate():
    """표본 없음과 '한 번도 못 집었다' 는 아주 다른 사실이다.

    0.0 으로 내려보내면 화면이 아무것도 안 한 팔을 고장난 팔로 그린다.
    §1.1 의 completion_rate 를 None 으로 둔 것과 같은 이유다.
    """
    detail = _one([_ev("policy_loaded", 0, checkpoint_id="act_020000",
                       dataset_revision="v3")])

    assert detail.pick_success_rate is None
    assert detail.pick_succeeded == 0
    assert detail.window_cycles == 0
    assert detail.cycle_time_p50_ms is None


def test_a_retried_pick_is_still_a_completed_cycle():
    """모방학습 pick 실패는 정상 동작 범위다 (§4.4).

    재시도로 사이클이 끝났으면 사람이 손댈 일이 아니다. 실패 건수는 성공률에
    남기되 완주 판정은 흔들리지 않아야 한다.
    """
    rows = [
        _ev("cycle_started", 0, dispense_id="d-1"),
        _ev("pick_failed", 1, medication_id="med-c", attempt=1,
            reason="grasp_slip"),
        _ev("pick_succeeded", 2, medication_id="med-c", attempt=2,
            duration_ms=5600),
        _ev("cycle_completed", 3, dispense_id="d-1", duration_ms=14_300),
    ]

    detail = _one(rows)

    assert detail.cycles_completed == 1
    assert detail.cycles_aborted == 0
    assert detail.pick_succeeded == 1
    assert detail.pick_failed == 1
    assert detail.pick_success_rate == 0.5
    # 한 번에 집은 것과 두 번 만에 집은 것을 구분하지 못하면 성공률이 정책
    # 품질을 반영하지 못한다.
    assert detail.pick_retried == 1


def test_an_aborted_cycle_keeps_the_servo_fault_visible():
    """포기의 원인은 창이 아니라 지금 상태다.

    서보 결함에는 해소 이벤트가 없다. 창 집계에서만 세고 지워버리면 화면이
    "왜 멈췄는지" 를 못 보여준다.
    """
    rows = [
        _ev("cycle_started", 0, dispense_id="d-3"),
        _ev("pick_failed", 1, attempt=1, reason="grasp_slip"),
        _ev("pick_failed", 2, attempt=2, reason="joint_overload"),
        _ev("servo_fault", 3, joint="shoulder_lift", fault_bits=5, temp_c=71.5),
        _ev("cycle_aborted", 4, dispense_id="d-3", reason="servo_fault"),
        _ev("homing_required", 5, reason="aborted_mid_cycle"),
    ]

    detail = _one(rows)

    assert detail.cycles_aborted == 1
    assert detail.cycles_completed == 0
    assert detail.pick_success_rate == 0.0
    assert detail.last_servo_fault is not None
    assert detail.last_servo_fault.joint == "shoulder_lift"
    assert detail.last_servo_fault.temp_c == 71.5
    assert detail.homing_required is True
    assert detail.homing_reason == "aborted_mid_cycle"
    # 포기한 사이클의 duration 은 없다. p50 에 0 이 섞이면 사이클 타임이
    # 실제보다 짧아 보인다.
    assert detail.cycle_time_p50_ms is None


def test_a_new_cycle_clears_the_homing_flag():
    """홈 복귀를 알리는 이벤트가 따로 없다.

    다음 사이클이 시작됐다는 것이 곧 복귀가 끝났다는 뜻이다. 여기가 안 풀리면
    화면에 '홈 복귀 필요' 가 영원히 붙어 있고, 그러면 아무도 안 본다.
    """
    rows = [
        _ev("cycle_aborted", 0, dispense_id="d-3", reason="servo_fault"),
        _ev("homing_required", 1, reason="aborted_mid_cycle"),
        *_cycle(2),
    ]

    detail = _one(rows)

    assert detail.homing_required is False
    assert detail.homing_reason is None


def test_a_running_cycle_is_not_judged_yet():
    """진행 중인 사이클을 창에 넣으면 성공률이 사이클 중간마다 흔들린다.

    §1.2 가 진행 중 세션을 창에서 뺀 것과 같다. 대신 무엇을 하고 있는지는
    따로 내려보내 화면이 '조제 중' 을 그릴 수 있게 한다.
    """
    rows = [
        *_cycle(1),
        _ev("cycle_started", 200, dispense_id="d-2", medication_id="med-b"),
        _ev("pick_succeeded", 201, attempt=1, duration_ms=4000),
    ]

    detail = _one(rows)

    assert detail.active_dispense_id == "d-2"
    assert detail.active_started_at == BASE + timedelta(seconds=200)
    assert detail.window_cycles == 1
    # 진행 중 사이클의 pick 은 아직 안 센다. 창은 닫힌 사이클로만 만든다.
    assert detail.pick_succeeded == 1


def test_an_idle_arm_has_no_active_cycle():
    detail = _one(_cycle(1))

    assert detail.active_dispense_id is None
    assert detail.active_started_at is None


def test_the_window_keeps_only_the_most_recent_cycles():
    """직전 N 사이클이다. 며칠치를 다 세면 '지금 팔이 어떤가' 가 안 보인다."""
    rows = []
    for index in range(dispense.DEFAULT_WINDOW + 5):
        rows += _cycle(index, duration_ms=1_000 * (index + 1))

    detail = _one(rows)

    assert detail.window_cycles == dispense.DEFAULT_WINDOW
    assert detail.sample_complete is True
    # 가장 오래된 5건(1~5초)이 빠져 창은 6~25초다. p50 은 그중 10번째(15초)이지
    # 25건 전체의 중앙값(13초)이 아니다 — 창 밖 표본을 보면 여기가 어긋난다.
    assert detail.cycle_time_p50_ms == 15_000


def test_a_short_sample_says_so():
    """3건에 1건 실패면 66.7% 지만 신뢰구간이 창만큼 넓다.

    화면이 숫자만 받으면 그 사실을 말할 수 없다.
    """
    detail = _one(_cycle(1) + _cycle(2))

    assert detail.window_cycles == 2
    assert detail.sample_complete is False
    assert detail.window == dispense.DEFAULT_WINDOW


def test_percentiles_are_nearest_rank():
    """보간하지 않는다. 실제로 돈 사이클 하나의 시간을 그대로 보여준다.

    표본 20건이면 p95 는 19번째 값이다 — ceil(20 * 0.95). 0.95 를 float 으로
    곱하면 이 경계가 조용히 하나 어긋난다(slo.budget_total 과 같은 함정).
    """
    rows = []
    for index in range(20):
        rows += _cycle(index, duration_ms=1_000 * (index + 1))

    detail = _one(rows)

    assert detail.cycle_time_p50_ms == 10_000   # ceil(20 * 0.5) = 10번째
    assert detail.cycle_time_p95_ms == 19_000   # ceil(20 * 0.95) = 19번째


def test_a_cycle_cut_off_by_the_event_limit_still_closes():
    """창 끝에서 시작 이벤트가 잘린 사이클.

    EVENT_LIMIT 만큼만 거슬러 올라가므로 가장 오래된 사이클은 종료 이벤트만
    남을 수 있다. 그걸 버리면 창이 요청한 수보다 항상 하나 적어진다.
    """
    rows = [
        _ev("pick_succeeded", 0, attempt=1, duration_ms=4000),
        _ev("cycle_completed", 1, dispense_id="d-0", duration_ms=9_000),
        *_cycle(1),
    ]

    detail = _one(rows)

    assert detail.window_cycles == 2
    assert detail.cycles_completed == 2
    # 시작을 못 봤어도 종료 payload 에 dispense_id 가 있다.
    assert detail.pick_succeeded == 2


def test_an_unfinished_cycle_before_a_restart_is_dropped():
    """종료 이벤트가 없는 사이클은 완주로도 포기로도 셀 수 없다.

    로봇이 사이클 도중 재기동하면 이런 구멍이 생긴다. 완주로 세면 성공률이
    올라가고 포기로 세면 없던 error 가 생긴다. 둘 다 사실이 아니다.
    """
    rows = [
        _ev("cycle_started", 0, dispense_id="d-1"),
        _ev("pick_succeeded", 1, attempt=1),
        *_cycle(2),
    ]

    detail = _one(rows)

    assert detail.window_cycles == 1
    assert detail.pick_succeeded == 1   # 버려진 사이클의 pick 은 세지 않는다


def test_the_latest_policy_wins():
    """체크포인트를 바꿔 낀 것이 어제 되던 pick 이 오늘 안 되는 첫 후보다 (§4.4)."""
    rows = [
        _ev("policy_loaded", 0, checkpoint_id="act_010000",
            dataset_revision="v2"),
        *_cycle(1),
        _ev("policy_loaded", 200, checkpoint_id="act_020000",
            dataset_revision="v3"),
    ]

    detail = _one(rows)

    assert detail.policy_checkpoint_id == "act_020000"
    assert detail.policy_dataset_revision == "v3"
    assert detail.policy_loaded_at == BASE + timedelta(seconds=200)


def test_payload_arrives_as_text_from_asyncpg():
    """asyncpg 는 JSONB 를 문자열로 돌려준다 (routers/events.py 와 같다).

    여기가 안 풀리면 지표가 전부 0 인데 에러는 하나도 없는 화면이 나온다.
    """
    rows = [
        {"robot_id": "omx-01", "event_code": "manipulator.cycle_completed",
         "occurred_at": BASE, "payload": json.dumps({"dispense_id": "d-1",
                                                     "duration_ms": 12_000})},
    ]

    detail = _one(rows)

    assert detail.cycle_time_p50_ms == 12_000


def test_a_broken_payload_does_not_kill_the_whole_arm():
    """정본을 벗어난 payload 하나가 팔 전체 지표를 못 쓰게 만들면 안 된다."""
    rows = [
        {"robot_id": "omx-01", "event_code": "manipulator.cycle_completed",
         "occurred_at": BASE, "payload": "{not json"},
        *_cycle(1),
    ]

    detail = _one(rows)

    assert detail.cycles_completed == 2
    # duration 을 못 읽은 사이클은 p50 표본에서 빠질 뿐이다.
    assert detail.cycle_time_p50_ms == 10_000


def test_each_arm_is_folded_on_its_own():
    """omx-01 의 실패가 omx-02 의 성공률을 깎으면 안 된다."""
    rows = [
        *_cycle(1, robot_id="omx-01"),
        _ev("cycle_started", 300, robot_id="omx-02", dispense_id="d-9"),
        _ev("pick_failed", 301, robot_id="omx-02", attempt=1,
            reason="grasp_slip"),
        _ev("cycle_aborted", 302, robot_id="omx-02", reason="servo_fault"),
    ]

    result = dispense.summarize(rows)

    assert result["omx-01"].pick_success_rate == 1.0
    assert result["omx-02"].pick_success_rate == 0.0
    assert result["omx-02"].cycles_aborted == 1
    assert result["omx-01"].cycles_aborted == 0
