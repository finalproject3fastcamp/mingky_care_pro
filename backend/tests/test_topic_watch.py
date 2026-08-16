"""토픽 주기 판정 (§7.2 · 로드맵 9).

이 판정이 잡으려는 것은 하나다 — **유닛은 active 인데 데이터가 안 나오는
상태**. systemd 도 heartbeat 도 초록인 채로 로봇이 아무것도 못 하는 그 구간을
지금까지 보는 곳이 없었다.

여기서 잠그는 것은 두 가지다.

  1. 나이만으로 못 잡는 저하(10Hz → 3Hz)를 Hz 로 잡는가
  2. 상시 발행이 아닌 토픽(/cmd_vel)을 고장으로 그리지 않는가.
     이걸 틀리면 대기 중인 로봇 2대가 항상 경고 상태가 되고, 그 빨강 속에
     진짜 /scan 두절이 묻힌다
"""

from datetime import datetime, timedelta, timezone

from app import heartbeat, robot_runtime, topic_watch
from app.schemas import TopicSampleIn
from app.topic_watch import TopicRule, TopicWatchRules


def _rules():
    return TopicWatchRules({
        "/scan": TopicRule(expected_hz=10, warn_sec=1.0, stale_sec=3.0,
                           min_hz_ratio=0.5, always_on=True,
                           why="라이다가 멈춥니다"),
        "/cmd_vel": TopicRule(expected_hz=20, warn_sec=2.0, stale_sec=10.0,
                              min_hz_ratio=0.5, always_on=False,
                              why="컨트롤러가 속도를 못 냅니다"),
    })


def _judge(topic: str, age_sec=None, hz=None):
    reported = {topic: TopicSampleIn(age_sec=age_sec, hz=hz)}
    return {t.topic: t for t in topic_watch.judge(reported, _rules())}[topic]


# --- 판정 --------------------------------------------------------------------

def test_fresh_topic_carries_the_expected_rate_for_the_screen():
    judged = _judge("/scan", age_sec=0.1, hz=9.8)

    assert judged.state == "fresh"
    assert judged.expected_hz == 10
    assert judged.always_on is True


def test_dead_lidar_is_stale_not_slow():
    assert _judge("/scan", age_sec=5.0, hz=0).state == "stale"


def test_late_topic_is_slow_before_it_is_stale():
    assert _judge("/scan", age_sec=1.5, hz=1.0).state == "slow"


def test_rate_drop_is_caught_even_though_the_age_looks_healthy():
    """USB 대역 부족으로 10Hz → 3Hz. 마지막 수신은 0.33초 전이라 어떤 나이
    임계에도 안 걸린다. 이 경로가 없으면 라이다가 절반만 도는 것을 못 본다."""
    judged = _judge("/scan", age_sec=0.33, hz=3.0)

    assert judged.state == "slow"


def test_rate_at_the_ratio_boundary_is_not_flagged():
    # 5.0 은 기대(10) × min_hz_ratio(0.5) 와 같다. 경계에서 흔들리면
    # 정상 로봇의 배지가 깜빡인다.
    assert _judge("/scan", age_sec=0.2, hz=5.0).state == "fresh"


def test_idle_cmd_vel_is_not_a_failure():
    """서 있는 로봇에 /cmd_vel 이 없는 것은 정상이다."""
    assert _judge("/cmd_vel", age_sec=600.0).state == "idle"


def test_cmd_vel_that_is_publishing_but_slow_is_still_slow():
    # 발행이 멈춘 것과, 발행되는데 절반 속도인 것은 다른 사실이다.
    assert _judge("/cmd_vel", age_sec=0.1, hz=4.0).state == "slow"


def test_missing_age_is_not_silently_treated_as_healthy():
    # 구버전 게이트웨이. 0 으로 그리면 "정상" 으로 읽혀 잘못된 안심을 준다.
    assert _judge("/scan", age_sec=None).state == "missing"


def test_topic_outside_the_canon_is_shown_but_not_rated():
    judged = _judge("/some/experimental", age_sec=99.0)

    assert judged.state == "unrated"
    assert judged.age_sec == 99.0


def test_canon_topic_the_robot_never_reports_is_marked_unwatched():
    """감시 노드가 그 토픽을 안 보고 있다는 사실이다. '정상' 과 다르다."""
    judged = topic_watch.judge({"/scan": TopicSampleIn(age_sec=0.1)}, _rules())

    assert {t.topic: t.state for t in judged}["/cmd_vel"] == "unwatched"


def test_a_gateway_that_reports_nothing_yields_nothing():
    """정본 네 개를 전부 unwatched 로 채우면 구버전 게이트웨이 하나가 화면을
    경고로 덮는다. 그건 토픽이 죽은 게 아니라 감시가 아직 안 붙은 것이다."""
    assert topic_watch.judge({}, _rules()) == []


def test_bad_topics_come_first_so_the_screen_does_not_have_to_sort():
    judged = topic_watch.judge(
        {"/scan": TopicSampleIn(age_sec=9.0),
         "/cmd_vel": TopicSampleIn(age_sec=0.05, hz=20.0)},
        _rules())

    assert [t.topic for t in judged] == ["/scan", "/cmd_vel"]


# --- 정본 로딩 ----------------------------------------------------------------

def test_missing_rules_file_does_not_kill_the_backend(tmp_path):
    """관측성 기능 하나 때문에 관제 전체가 안 뜨는 쪽이 더 나쁘다."""
    rules = TopicWatchRules.load(str(tmp_path / "없는파일.yaml"))

    assert rules.get("/scan") is None


def test_shipped_rules_cover_the_topics_the_spec_names():
    rules = TopicWatchRules.load()
    covered = {topic for topic, _ in rules.items()}

    assert {"/scan", "/odom", "/amcl_pose", "/cmd_vel"} <= covered
    # 스펙이 상시 감시로 지목한 둘. 나머지는 간헐 발행이라 이벤트를 안 낸다.
    assert rules.get("/scan").always_on is True
    assert rules.get("/odom").always_on is True
    assert rules.get("/cmd_vel").always_on is False


# --- 이벤트 발행 --------------------------------------------------------------

def _online(robot_id: str, topics: dict) -> None:
    robot_runtime.update(robot_id, "active", False, topics=topics)
    heartbeat.touch(robot_id)


def _setup():
    heartbeat.reset()
    robot_runtime.reset()
    topic_watch.reset()
    topic_watch.load()


def test_stale_topic_is_announced_once_not_every_cycle():
    _setup()
    _online("pinky-01", {"/scan": TopicSampleIn(age_sec=30.0, hz=0.0)})

    first = topic_watch.collect()
    second = topic_watch.collect()

    assert [e.event_code for e in first] == ["robot.topic_stale"]
    assert first[0].level == "error"
    assert first[0].payload["topic"] == "/scan"
    # 매 주기 반복 발행하면 타임라인이 한 사건으로 덮인다.
    assert second == []


def test_recovery_is_announced_so_the_timeline_is_not_one_sided():
    _setup()
    _online("pinky-01", {"/scan": TopicSampleIn(age_sec=30.0)})
    topic_watch.collect()

    _online("pinky-01", {"/scan": TopicSampleIn(age_sec=0.1, hz=10.0)})
    restored = topic_watch.collect()

    assert [e.event_code for e in restored] == ["robot.topic_restored"]
    # 다시 끊기면 다시 알린다. 한 번 알리고 영영 침묵하면 안 된다.
    _online("pinky-01", {"/scan": TopicSampleIn(age_sec=30.0)})
    assert [e.event_code for e in topic_watch.collect()] == ["robot.topic_stale"]


def test_offline_robots_do_not_get_a_second_alarm_for_the_same_fact():
    """두절 중에는 토픽 나이가 멈춰 있을 뿐이다. robot.comm_lost 가 이미
    말한 사실을 여기서 또 알리면 한 사건에 알림이 두 번 간다."""
    _setup()
    _online("pinky-01", {"/scan": TopicSampleIn(age_sec=30.0)})

    # 두절 판정까지 시간을 넘긴다.
    heartbeat.collect(datetime.now(timezone.utc) + timedelta(hours=1))

    assert topic_watch.collect() == []


def test_intermittent_topics_never_raise_an_alarm():
    _setup()
    _online("pinky-01", {"/cmd_vel": TopicSampleIn(age_sec=3600.0)})

    assert topic_watch.collect() == []


# --- 응답 배선 ----------------------------------------------------------------

def _row(robot_id: str, robot_type: str) -> dict:
    return {
        "robot_id": robot_id, "robot_type": robot_type,
        "display_name": robot_id, "domain_id": None, "is_active": True,
        "battery_voltage": None, "battery_percent": None,
        "battery_recorded_at": None, "active_session_id": None,
        "active_patient_id": None, "last_session_ended_at": None,
        "last_session_end_reason": None,
    }


def test_judgement_rides_along_with_the_robot_list():
    """화면이 임계를 다시 들고 있지 않게 state 까지 실어 보낸다."""
    from app.routers import robots

    _setup()
    robot_runtime.update("pinky-01", "active", False,
                         topics={"/scan": TopicSampleIn(age_sec=9.0)})

    out = robots._row_to_out(_row("pinky-01", "mobile"), {})

    assert {t.topic: t.state for t in out.topics}["/scan"] == "stale"


def test_manipulators_have_no_topic_axis_at_all():
    """OMX 는 LeRobot 시리얼 직결이라 토픽이 없다. 빈 목록을 내려보내면
    화면이 '감시 중인데 아무것도 안 온다' 로 읽는다 (§7.3)."""
    from app.routers import robots

    _setup()
    out = robots._row_to_out(_row("omx-01", "manipulator"), {})

    assert "topics" not in out.model_dump()
