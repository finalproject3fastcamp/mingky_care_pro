"""서보 온도·전류 판정 (§4.4 · 로드맵 11).

Dynamixel 이 공짜로 주는 유일한 예지보전 신호다. 여기서 잠그는 것은 셋이다.

  1. **지금 뜨거운 것과 오르는 중인 것을 구분하는가.** 40℃ 인데 회차마다
     오르는 조인트가 55℃ 에서 평평한 조인트보다 나쁜 신호다
  2. 추세를 함부로 내지 않는가. 조제는 사이클마다 부하가 출렁여서, 짧은
     창의 기울기는 다음 표본에 뒤집힌다
  3. 조인트별 임계를 지키는가. 그리퍼는 쥔 채 버티므로 뜨겁게 도는 것이
     정상이고, 같은 선을 쓰면 정상 동작이 매 사이클 경고가 된다
"""

from datetime import datetime, timedelta, timezone

from app import servo_health
from app.servo_health import ServoLimitRules
from app.schemas import ServoReadingIn

BASE = datetime(2026, 8, 16, 9, 0, tzinfo=timezone.utc)


def _rules():
    return ServoLimitRules(
        default={"warn_temp_c": 55, "hot_temp_c": 65,
                 "rising_c_per_hour": 4, "warn_current_ma": 1200},
        joints={"gripper": {"warn_temp_c": 60, "hot_temp_c": 70}},
    )


def _latest(joint="shoulder_lift", temp_c=40.0, hardware_error=0,
            current_ma=300.0, at=BASE):
    return [{"joint": joint, "recorded_at": at, "temp_c": temp_c,
             "current_ma": current_ma, "voltage_v": 12.0,
             "hardware_error": hardware_error}]


def _series(joint, start_temp, per_hour, count=8, minutes=30):
    """등간격 온도 표본. per_hour 만큼 오르내린다."""
    return [
        {"joint": joint,
         "recorded_at": BASE + timedelta(minutes=minutes * i),
         "temp_c": start_temp + per_hour * (minutes * i) / 60.0}
        for i in range(count)
    ]


def _one(latest, trend=()):
    return servo_health.summarize(
        "omx-01", latest, list(trend), rules=_rules()).servos[0]


# --- 지금 상태 ----------------------------------------------------------------

def test_cool_joint_is_ok():
    assert _one(_latest(temp_c=38.0)).state == "ok"


def test_warm_is_shown_but_not_alarmed():
    reading = _one(_latest(temp_c=57.0))

    assert reading.state == "warm"
    assert reading.warn_temp_c == 55


def test_hot_crosses_the_limit():
    assert _one(_latest(temp_c=66.0)).state == "hot"


def test_hardware_error_outranks_temperature():
    """서보가 이미 토크를 끊었을 수 있다. 온도가 멀쩡해도 fault 가 먼저다."""
    assert _one(_latest(temp_c=30.0, hardware_error=0b100000)).state == "fault"


def test_missing_temperature_is_unknown_not_ok():
    """에러 비트를 0 으로 읽었어도 온도가 없으면 뜨거운지는 모른다."""
    assert _one(_latest(temp_c=None)).state == "unknown"


def test_the_gripper_gets_its_own_limits():
    """쥔 채 버티는 축이다. 공통선을 쓰면 정상 동작이 매 사이클 경고가 된다."""
    assert _one(_latest(joint="gripper", temp_c=57.0)).state == "ok"
    assert _one(_latest(joint="shoulder_lift", temp_c=57.0)).state == "warm"


# --- 추세 --------------------------------------------------------------------

def test_a_climbing_joint_is_flagged_even_while_it_is_still_cool():
    """이 판정이 없으면 온도계 하나만 남고 예지보전 신호가 사라진다."""
    reading = _one(_latest(temp_c=41.0),
                   _series("shoulder_lift", 35.0, per_hour=6))

    assert reading.state == "ok"
    assert reading.rising is True
    assert reading.slope_c_per_hour == 6.0


def test_a_flat_joint_is_not_rising():
    reading = _one(_latest(temp_c=45.0),
                   _series("shoulder_lift", 45.0, per_hour=0))

    assert reading.rising is False
    assert reading.slope_c_per_hour == 0.0


def test_too_few_samples_yield_no_trend_at_all():
    """틀린 추세는 없는 추세보다 나쁘다 (battery_forecast 와 같은 규칙)."""
    reading = _one(_latest(),
                   _series("shoulder_lift", 40.0, per_hour=20, count=3))

    assert reading.slope_c_per_hour is None
    assert reading.rising is False
    assert reading.sample_count == 3


def test_a_short_window_yields_no_trend():
    # 5분 폭의 기울기로 "시간당 24℃" 를 만들면 다음 표본에 뒤집힌다.
    reading = _one(_latest(),
                   _series("shoulder_lift", 40.0, per_hour=24,
                           count=6, minutes=1))

    assert reading.slope_c_per_hour is None


def test_noisy_samples_yield_no_trend():
    """조제 부하가 출렁이는 중이다. 방향을 말하지 않는다."""
    noisy = [
        {"joint": "elbow", "recorded_at": BASE + timedelta(minutes=20 * i),
         "temp_c": temp}
        for i, temp in enumerate([40.0, 58.0, 41.0, 57.0, 42.0, 56.0])
    ]
    reading = _one(_latest(joint="elbow"), noisy)

    assert reading.slope_c_per_hour is None


def test_bad_joints_come_first_so_the_screen_does_not_have_to_sort():
    latest = (_latest(joint="wrist", temp_c=38.0)
              + _latest(joint="shoulder_lift", temp_c=66.0)
              + _latest(joint="elbow", temp_c=57.0))

    result = servo_health.summarize("omx-01", latest, [], rules=_rules())

    assert [s.joint for s in result.servos] == [
        "shoulder_lift", "elbow", "wrist"]


# --- 이벤트 발행 --------------------------------------------------------------

def _crossings(*temps, joint="shoulder_lift"):
    return servo_health.crossings(
        "omx-01",
        [ServoReadingIn(joint=joint, temp_c=temp) for temp in temps],
        rules=_rules())


def test_overheat_is_announced_once_not_every_sample():
    servo_health.reset()

    first = _crossings(66.0)
    second = _crossings(67.0)

    assert [e.event_code for e in first] == ["manipulator.servo_overheat"]
    # 확률적 실패가 아니라 실제 신호지만, 팔은 아직 돌고 있다 (§8.4).
    assert first[0].level == "warning"
    assert first[0].payload["joint"] == "shoulder_lift"
    assert second == []


def test_cooling_needs_to_come_all_the_way_down_to_the_warn_line():
    """임계 바로 아래에서 흔들리는 조인트가 과열/해제를 반복 발행하면 그
    알림을 아무도 안 본다. 히스테리시스가 없으면 알림이 잡음이 된다."""
    servo_health.reset()
    _crossings(66.0)

    # 임계 아래지만 아직 경고선 위다. 해제하지 않는다.
    assert _crossings(60.0) == []

    cooled = _crossings(54.0)
    assert [e.event_code for e in cooled] == ["manipulator.servo_cooled"]
    # 다시 뜨거워지면 다시 알린다.
    assert [e.event_code for e in _crossings(66.0)] == [
        "manipulator.servo_overheat"]


def test_the_gripper_does_not_trip_at_the_common_limit():
    servo_health.reset()

    assert _crossings(66.0, joint="gripper") == []
    assert [e.event_code for e in _crossings(71.0, joint="gripper")] == [
        "manipulator.servo_overheat"]


def test_a_sample_without_temperature_never_alarms():
    servo_health.reset()

    assert servo_health.crossings(
        "omx-01",
        [ServoReadingIn(joint="wrist", hardware_error=0)],
        rules=_rules()) == []


# --- 정본 로딩 ----------------------------------------------------------------

def test_missing_limits_file_does_not_kill_the_backend(tmp_path):
    rules = ServoLimitRules.load(str(tmp_path / "없는파일.yaml"))

    assert rules.for_joint("wrist").hot_temp_c > 0


def test_shipped_limits_stay_below_the_servo_shutdown_point():
    """XM430 은 80℃ 에서 스스로 토크를 끊는다. 그 지점에서 처음 알리면 이미
    사이클이 깨진 뒤다."""
    rules = ServoLimitRules.load()

    for joint in ("wrist", "gripper", "shoulder_lift"):
        limits = rules.for_joint(joint)
        assert limits.warn_temp_c < limits.hot_temp_c < 80
