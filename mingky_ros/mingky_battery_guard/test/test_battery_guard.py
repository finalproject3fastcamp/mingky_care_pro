#!/usr/bin/env python3
"""battery_guard 판단 로직 회귀 테스트. 로봇도 Nav2 도 필요 없다.

노드를 띄우되 부저/Nav2 를 끄고, 콜백에 직접 값을 넣어 결과만 본다.
"""

from mingky_battery_guard.battery_guard import BatteryGuard, percent_from_voltage
import pytest
import rclpy
from rclpy.parameter import Parameter
from std_msgs.msg import Float32


def make_guard(**overrides):
    """부저를 끈 guard 를 만든다. 발행된 상태 변화를 리스트로 같이 준다."""
    params = {
        'use_buzzer': False,
        'threshold_percent': 40.0,
        'rearm_percent': 60.0,
        'confirm_count': 3,
        'trend_samples': 5,
        'trend_rise': 2.0,
        'median_samples': 3,
    }
    params.update(overrides)
    node = BatteryGuard(
        parameter_overrides=[Parameter(k, value=v) for k, v in params.items()])

    # GuideManager 로 전달되는 latched 상태 토픽을 가로챈다.
    alerts = []
    node.publish_low_state = alerts.append
    return node, alerts


def feed_volts(node, volts):
    for v in volts:
        node.on_voltage(Float32(data=float(v)))


def feed_percents(node, pcts):
    """퍼센트를 전압으로 바꿔 넣는다. fake_battery 와 같은 역함수."""
    feed_volts(node, [6.8 + (p / 100.0) * 0.8 for p in pcts])


@pytest.fixture(scope='module', autouse=True)
def ros():
    rclpy.init()
    yield
    rclpy.shutdown()


@pytest.fixture
def guard():
    node, alerts = make_guard()
    yield node, alerts
    node.destroy_node()


# ---------------------------------------------------------------- 변환식

def test_percent_from_voltage_matches_pinkylib():
    assert percent_from_voltage(6.8) == pytest.approx(0.0)
    assert percent_from_voltage(7.6) == pytest.approx(100.0)
    assert percent_from_voltage(7.12) == pytest.approx(40.0)


def test_percent_is_clamped_outside_the_window():
    """6.8V 아래와 7.6V 위는 뭉개진다. 뭉개지더라도 범위는 벗어나면 안 된다."""
    assert percent_from_voltage(6.0) == 0.0
    assert percent_from_voltage(8.4) == 100.0


# ---------------------------------------------------------------- 발동

def test_fires_once_after_confirm_count(guard):
    node, alerts = guard
    feed_percents(node, [70, 65, 60, 55, 50])     # 표본 채우기
    assert alerts == []
    # median_samples=3 이라 중앙값이 한 샘플 늦게 따라온다. 아래 3개를 넣어도
    # guard 가 낮다고 세는 것은 2회뿐이다. (test_median_filter_costs_one_sample)
    feed_percents(node, [38, 37, 36])
    assert alerts == []
    feed_percents(node, [35])                     # 3회째 - 발동
    assert len(alerts) == 1


def test_median_filter_costs_one_sample_of_delay(guard):
    """필터를 넣은 대가. 창이 3이면 판단이 한 샘플(약 5초) 늦다.

    튄 값 하나를 걸러내는 것과 맞바꾼 것이고, 40% 기준에서 5초는 감당된다.
    이 값이 바뀌면 테스트 시나리오도 같이 바뀌어야 하므로 못 박아 둔다.
    """
    node, alerts = guard
    feed_percents(node, [70, 65, 60, 55, 50])
    feed_percents(node, [38, 37, 36])
    assert node.low_count == 2, '지연이 1샘플이 아니다'


def test_does_not_fire_twice(guard):
    node, alerts = guard
    feed_percents(node, [70, 65, 60, 55, 50, 38, 37, 36, 35, 34, 33])
    assert len(alerts) == 1


def test_single_dip_does_not_fire(guard):
    """한 번 떨어진 것만으로는 울리지 않는다."""
    node, alerts = guard
    feed_percents(node, [70, 65, 60, 55, 50, 30, 55, 60, 65])
    assert alerts == []


# ---------------------------------------------------------------- 중앙값 필터

def test_median_rejects_a_single_spurious_low_sample(guard):
    """battery_publisher 가 필터 없이 단일 샘플을 발행하므로 여기서 걸러야 한다.

    70% 사이에 6.80V(=0%) 가 한 번 튀어도 발동하면 안 된다.
    """
    node, alerts = guard
    feed_percents(node, [70, 70, 70, 70, 70])
    feed_volts(node, [6.80])                      # 튄 샘플 하나
    feed_percents(node, [70, 70])
    assert alerts == []
    assert node.low_count == 0


def test_median_still_follows_a_real_drop(guard):
    """진짜로 계속 낮으면 필터가 있어도 발동해야 한다."""
    node, alerts = guard
    feed_percents(node, [70, 65, 60, 55, 50])
    feed_percents(node, [35, 35, 35, 35, 35])
    assert len(alerts) == 1


def test_low_state_is_published_as_boolean(guard):
    node, alerts = guard
    feed_percents(node, [70, 65, 60, 55, 50, 38, 37, 36, 35])
    assert alerts == [True]


# ---------------------------------------------------------------- 충전 감지

# ---------------------------------------------- 주행 중 왕복 (관제 보고 사례)

def test_fires_when_battery_swings_between_high_and_low(guard):
    """70% <-> 8% 를 왕복해도 경보가 나가야 한다.

    방전이 진행된 배터리는 주행하면 처지고 멈추면 회복하기를 반복한다.
    연속 카운터를 쓰면 기준치 위를 한 번 볼 때마다 0 이 되어 영영 쌓이지
    않는다. 실제로 이 상태에서 로봇 기본 부저는 계속 울리는데 이 노드는
    한 번도 발동하지 않는 일이 있었다.
    """
    node, alerts = guard
    feed_percents(node, [70, 8] * 8)
    assert node.fired is True, '왕복하는 동안 경보가 나가지 않았다'
    # 회복 하나로 풀렸다 다시 걸리기를 반복하면 관제에 이벤트 폭풍이 나고
    # 충전소 복귀도 계속 취소·재시도된다.
    assert alerts == [True], f'상태가 반복해서 뒤집혔다: {alerts}'


def test_single_high_reading_does_not_reset_progress(guard):
    """기준치 위 표본 하나가 그동안의 저전압 기록을 지우면 안 된다."""
    node, alerts = guard
    feed_percents(node, [70, 65, 60, 55, 50])
    feed_percents(node, [30, 30])          # 창에 2회 쌓임
    before = node.low_count
    feed_percents(node, [70])              # 회복 한 번
    assert node.low_count >= before - 1, '높은 값 하나로 기록이 초기화됐다'


# ---------------------------------------------- 위험선 즉시 대응

def test_critical_voltage_fires_without_waiting(guard):
    """위험선(6.80V)까지 내려가면 확인 절차를 기다리지 않는다.

    로봇 기본 부저가 이미 울리는 구간이라 여기서 더 기다릴 이유가 없다.
    """
    node, alerts = guard
    feed_volts(node, [7.36, 6.75, 6.72])   # 원본 2회가 위험선 이하
    assert node.fired is True


def test_single_critical_sample_does_not_fire(guard):
    """ADC 단발 오류 하나로는 발동하지 않는다."""
    node, alerts = guard
    feed_volts(node, [7.36, 7.36, 6.75, 7.36, 7.36])
    assert node.fired is False


def test_critical_fires_even_while_charging():
    """충전 중으로 보여도 위험선이면 알린다."""
    node, alerts = make_guard(median_samples=3)
    feed_percents(node, [5, 10, 15, 20, 25])   # 상승 추세 = 충전 중 판정
    assert node.is_charging() is True
    feed_volts(node, [6.75, 6.72])
    assert node.fired is True, '충전 중이라는 이유로 위험 전압을 무시했다'
    node.destroy_node()


# ---------------------------------------------- 최저 전압 노출

def test_minimum_voltage_is_published(guard):
    """중앙값이 가리는 최저 전압을 따로 볼 수 있어야 한다."""
    node, alerts = guard
    seen = []
    node.vmin_pub.publish = lambda msg: seen.append(round(msg.data, 3))
    feed_volts(node, [7.36, 6.75, 7.36])
    assert min(seen) == 6.75, '최저 전압이 노출되지 않는다'


def test_recovery_after_load_is_not_mistaken_for_charging():
    """모터가 멈추며 전압이 기준선으로 돌아오는 것은 충전이 아니다.

    이걸 충전으로 오인하면 저전압 경보가 영영 나가지 않는다.
    """
    node, alerts = make_guard(median_samples=1)
    # 기준선 38% 에서 모터 부하로 처졌다가 기준선으로 회복
    feed_percents(node, [38, 26, 26, 38, 38])
    assert not node.is_charging()
    node.destroy_node()


def test_real_charging_is_detected():
    node, alerts = make_guard(median_samples=1)
    feed_percents(node, [40, 45, 50, 55, 60])
    assert node.is_charging()
    node.destroy_node()


def test_charging_blocks_the_alert():
    node, alerts = make_guard(median_samples=1)
    feed_percents(node, [20, 25, 30, 35, 39, 39, 39])
    assert alerts == []
    node.destroy_node()


# ---------------------------------------------------------------- 재무장

def test_rearms_after_recovery_and_can_fire_again(guard):
    node, alerts = guard
    feed_percents(node, [70, 65, 60, 55, 50, 38, 37, 36, 35])
    assert len(alerts) == 1
    # 재무장은 창(confirm_window)이 전부 기준치 위여야 인정된다.
    # 회복 표본 하나로 풀어주면 왕복하는 배터리에서 발동·해제가 반복된다.
    feed_percents(node, [45, 55, 65, 70, 75, 80, 85])   # 충전
    assert node.fired is False
    feed_percents(node, [39, 38, 37, 36])         # 다시 떨어짐
    assert alerts == [True, False, True]


def test_rearms_even_when_charging_has_finished():
    """충전이 끝나 전압이 평평해져도 재무장돼야 한다.

    재무장을 충전 감지 분기 안에 두면 100% 인 채로 영영 무장 해제로 남는다.
    """
    node, alerts = make_guard(median_samples=1)
    feed_percents(node, [70, 65, 60, 55, 50, 38, 37, 36])   # 필터 꺼서 지연 없음
    assert node.fired is True
    # 완충 후 평평한 구간만 들어온다 -> is_charging() 은 False
    # 창이 전부 기준치 위가 되도록 confirm_window 만큼 넣는다.
    feed_percents(node, [95] * 8)
    assert node.is_charging() is False
    assert node.fired is False, '평평한 완충 상태에서 재무장되지 않았다'
    node.destroy_node()
