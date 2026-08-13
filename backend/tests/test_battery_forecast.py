"""전압 기울기로 낸 충전/방전 추정 검증.

핵심은 "언제 추정을 내지 않는가" 다. 틀린 시간은 없는 시간보다 나쁘다 —
의료진이 그 숫자를 믿고 일정을 잡는다.
"""

from datetime import datetime, timedelta, timezone

from app import battery_forecast
from app.battery_forecast import forecast

BASE = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)


def _ramp(start_v, per_hour, count=13, step_min=5, jitter=None):
    """일정 기울기로 오르내리는 표본."""
    samples = []
    for i in range(count):
        minutes = i * step_min
        voltage = start_v + per_hour * (minutes / 60.0)
        if jitter:
            voltage += jitter[i % len(jitter)]
        samples.append((BASE + timedelta(minutes=minutes), voltage))
    return samples


def test_charging_is_estimated_from_voltage_not_percent():
    # 램프가 1시간이라 마지막 표본이 7.4V 다. 추정은 현재값 기준이므로
    # 7.6V 까지 시간당 0.2V 면 1시간 남는다.
    # 퍼센트로 계산하면 7.6V 위가 전부 100% 라 기울기가 0 이 된다.
    result = forecast(_ramp(7.2, 0.2))

    assert result is not None
    assert result.direction == "charging"
    assert result.seconds is not None
    assert abs(result.seconds - 3600) < 300


def test_discharging_targets_the_empty_voltage():
    result = forecast(_ramp(7.2, -0.2))

    assert result.direction == "discharging"
    # 마지막 표본이 7.0V. 6.8V 까지 시간당 0.2V 면 1시간.
    assert abs(result.seconds - 3600) < 300


def test_unstable_slope_reports_direction_but_no_time():
    # 주행 부하가 출렁이는 구간. 기울기로 시간을 내면 "3분 남음" 이
    # 다음 표본에 "47분 남음" 이 된다.
    noisy = _ramp(7.2, 0.05, count=13, jitter=[0.0, 0.25, -0.25, 0.2, -0.2])

    result = forecast(noisy)

    assert result.seconds is None
    assert result.reason == "unstable_slope"
    # 방향은 여전히 알려준다 — "충전 중" 만 표시하면 된다.
    assert result.direction in ("charging", "discharging")


def test_flat_voltage_is_idle_not_a_huge_number():
    result = forecast(_ramp(7.4, 0.0))

    assert result.direction == "idle"
    assert result.seconds is None
    assert result.reason == "voltage_flat"


def test_too_few_samples_yields_nothing():
    assert forecast(_ramp(7.2, 0.2, count=3)) is None


def test_too_short_a_window_yields_nothing():
    # 5개 표본이 1분 안에 몰려 있으면 잡음이 기울기를 지배한다.
    samples = [(BASE + timedelta(seconds=i * 10), 7.2 + i * 0.001)
               for i in range(6)]

    assert forecast(samples) is None


def test_samples_without_voltage_are_dropped_not_backfilled():
    # 변환 노드는 살아 있는데 ADC 읽기가 실패한 경우다.
    # 퍼센트로 대신 계산하지 않는다.
    samples = _ramp(7.2, 0.2)
    samples.insert(3, (BASE + timedelta(minutes=12), None))

    result = forecast(samples)

    assert result is not None
    assert result.sample_count == 13


def test_out_of_order_samples_are_sorted_first():
    # 두절 후 몰아 들어온 기록이 섞일 수 있다.
    samples = _ramp(7.2, 0.2)
    shuffled = samples[6:] + samples[:6]

    assert forecast(shuffled).seconds == forecast(samples).seconds


def test_already_full_reports_no_time_left():
    # 이미 7.6V 를 넘겼다. 남은 시간이 음수로 나오면 안 된다.
    result = forecast(_ramp(7.65, 0.1))

    assert result.direction == "charging"
    assert result.seconds is None
    assert result.reason == "target_reached"


def test_a_forecast_beyond_the_horizon_is_not_shown():
    # 시간당 0.03V 로 7.0 → 7.6 이면 20시간이다. 보여줘도 쓸모가 없다.
    result = forecast(_ramp(7.0, 0.03, count=25, step_min=10))

    assert result.seconds is None
    assert result.reason == "beyond_horizon"


def test_constants_match_the_migration_curve():
    # 004_battery_voltage.sql 의 6.8V(0%) ~ 7.6V(100%) 와 어긋나면
    # 추정이 조용히 틀린다.
    assert battery_forecast.FULL_VOLTAGE == 7.6
    assert battery_forecast.EMPTY_VOLTAGE == 6.8
