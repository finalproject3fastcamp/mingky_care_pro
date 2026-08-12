#!/usr/bin/env python3
"""battery_guard 판단 로직 회귀 테스트. 로봇도 Nav2 도 필요 없다.

노드를 띄우되 부저/Nav2 를 끄고, 콜백에 직접 값을 넣어 결과만 본다.
"""

from mingky_battery_guard.battery_guard import BatteryGuard, percent_from_voltage
import pytest
import rclpy
from rclpy.parameter import Parameter
from std_msgs.msg import Float32


# 판정 기준은 전압이다. 시나리오도 전압으로 쓴다.
#
# 퍼센트로 시나리오를 쓰고 전압으로 변환해 넣으면, 임계값이 전압으로 바뀐
# 뒤에도 테스트는 그대로 통과하면서 검증하는 대상만 바뀐다. 통과가 아무것도
# 보증하지 않는 상태가 되므로 눈금을 판정 도메인과 일치시킨다.
LOW_V = 7.12        # 발동선
REARM_V = 7.28      # 재무장선
CRITICAL_V = 6.80   # 위험선 (= 퍼센트 0% 가 시작되는 지점)
FULL_V = 7.60       # 퍼센트 100% 가 시작되는 지점

HIGH = 7.36         # 발동선보다 확실히 위
SAG = 6.86          # 주행 중 처진 값. 발동선 아래, 위험선 위

# 추세 판단 표본(trend_samples=5)을 채우는 하강 구간. 전부 발동선 위다.
FILL = [7.36, 7.32, 7.28, 7.24, 7.20]


def make_guard(**overrides):
    """부저를 끈 guard 를 만든다. 발행된 상태 변화를 리스트로 같이 준다."""
    params = {
        'use_buzzer': False,
        'low_voltage': LOW_V,
        'rearm_voltage': REARM_V,
        'confirm_count': 3,
        'trend_samples': 5,
        'trend_rise_volt': 0.05,
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
    """예비 경로(battery/percent)로 넣는다. 전압 경로를 안 탄 노드에만 쓴다."""
    for p in pcts:
        node.on_percent(Float32(data=float(p)))


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
    feed_volts(node, FILL)                        # 표본 채우기
    assert alerts == []
    # median_samples=3 이라 중앙값이 한 샘플 늦게 따라온다. 아래 3개를 넣어도
    # guard 가 낮다고 세는 것은 2회뿐이다. (test_median_filter_costs_one_sample)
    feed_volts(node, [7.104, 7.096, 7.088])
    assert alerts == []
    feed_volts(node, [7.08])                      # 3회째 - 발동
    assert len(alerts) == 1


def test_median_filter_costs_one_sample_of_delay(guard):
    """필터를 넣은 대가. 창이 3이면 판단이 한 샘플(약 5초) 늦다.

    튄 값 하나를 걸러내는 것과 맞바꾼 것이고, 7.12V 기준에서 5초는 감당된다.
    이 값이 바뀌면 테스트 시나리오도 같이 바뀌어야 하므로 못 박아 둔다.
    """
    node, alerts = guard
    feed_volts(node, FILL)
    feed_volts(node, [7.104, 7.096, 7.088])
    assert node.low_count == 2, '지연이 1샘플이 아니다'


def test_does_not_fire_twice(guard):
    node, alerts = guard
    feed_volts(node, FILL + [7.104, 7.096, 7.088, 7.08, 7.072, 7.064])
    assert len(alerts) == 1


def test_single_dip_does_not_fire(guard):
    """한 번 떨어진 것만으로는 울리지 않는다."""
    node, alerts = guard
    feed_volts(node, FILL + [7.04, 7.24, 7.28, 7.32])
    assert alerts == []


# ---------------------------------------------------------------- 중앙값 필터

def test_median_rejects_a_single_spurious_low_sample(guard):
    """battery_publisher 가 필터 없이 단일 샘플을 발행하므로 여기서 걸러야 한다.

    7.36V 사이에 6.80V 가 한 번 튀어도 발동하면 안 된다.
    """
    node, alerts = guard
    feed_volts(node, [HIGH] * 5)
    feed_volts(node, [CRITICAL_V])                # 튄 샘플 하나
    feed_volts(node, [HIGH, HIGH])
    assert alerts == []
    assert node.low_count == 0


def test_median_still_follows_a_real_drop(guard):
    """진짜로 계속 낮으면 필터가 있어도 발동해야 한다."""
    node, alerts = guard
    feed_volts(node, FILL)
    feed_volts(node, [7.08] * 5)
    assert len(alerts) == 1


def test_low_state_is_published_as_boolean(guard):
    node, alerts = guard
    feed_volts(node, FILL + [7.104, 7.096, 7.088, 7.08])
    assert alerts == [True]


# ---------------------------------------------------------------- 충전 감지

# ---------------------------------------------- 주행 중 왕복 (관제 보고 사례)

def test_fires_when_battery_swings_between_high_and_low(guard):
    """7.36V <-> 6.86V 를 왕복해도 경보가 나가야 한다.

    방전이 진행된 배터리는 주행하면 처지고 멈추면 회복하기를 반복한다.
    연속 카운터를 쓰면 기준치 위를 한 번 볼 때마다 0 이 되어 영영 쌓이지
    않는다. 실제로 이 상태에서 로봇 기본 부저는 계속 울리는데 이 노드는
    한 번도 발동하지 않는 일이 있었다.
    """
    node, alerts = guard
    feed_volts(node, [HIGH, SAG] * 8)
    assert node.fired is True, '왕복하는 동안 경보가 나가지 않았다'
    # 회복 하나로 풀렸다 다시 걸리기를 반복하면 관제에 이벤트 폭풍이 나고
    # 충전소 복귀도 계속 취소·재시도된다.
    assert alerts == [True], f'상태가 반복해서 뒤집혔다: {alerts}'


def test_single_high_reading_does_not_reset_progress(guard):
    """기준치 위 표본 하나가 그동안의 저전압 기록을 지우면 안 된다."""
    node, alerts = guard
    feed_volts(node, FILL)
    feed_volts(node, [7.04, 7.04])         # 창에 2회 쌓임
    before = node.low_count
    feed_volts(node, [HIGH])               # 회복 한 번
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
    # 위험선 근처에서 올라오는 중 = 충전 중 판정
    feed_volts(node, [6.84, 6.88, 6.92, 6.96, 7.00])
    assert node.is_charging() is True
    feed_volts(node, [6.75, 6.72])
    assert node.fired is True, '충전 중이라는 이유로 위험 전압을 무시했다'
    node.destroy_node()


def test_critical_still_fires_with_the_median_filter_off():
    """필터를 꺼도 위험선 판정은 살아 있어야 한다.

    창 크기를 median_samples 에만 맞추면 median_samples=1 일 때 표본이
    하나뿐이라 'critical_count 회 이상'을 영영 만족하지 못한다.
    경보가 사라지는데 로그에는 아무 흔적도 남지 않는다.
    """
    node, alerts = make_guard(median_samples=1)
    feed_volts(node, [6.75, 6.72])
    assert node.fired is True, '필터를 끄자 위험 전압 판정이 사라졌다'
    node.destroy_node()


def test_median_filter_stays_off_when_disabled():
    """median_samples=1 은 '필터 끔'이어야 한다.

    위험 판정 때문에 원본 창이 커져도 중앙값 창까지 같이 커지면 안 된다.
    창이 둘이 되면 median() 이 두 값의 평균을 내어 필터가 꺼지지 않는다.
    """
    node, alerts = make_guard(median_samples=1)
    feed_volts(node, [7.36, 7.00])
    assert node.voltage == pytest.approx(7.00), '필터가 꺼지지 않았다'
    node.destroy_node()


def test_critical_count_is_configurable():
    """위험선 확인 횟수는 발행 주기에 따라 달라지므로 파라미터여야 한다."""
    node, alerts = make_guard(median_samples=1, critical_count=3)
    feed_volts(node, [6.75, 6.72])
    assert node.fired is False, '2회로 발동했다 — critical_count 가 무시된다'
    feed_volts(node, [6.70])
    assert node.fired is True
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
    # 7.104V 에서 모터 부하로 0.1V 처졌다가 원래 값으로 회복
    feed_volts(node, [7.104, 7.008, 7.008, 7.104, 7.104])
    assert not node.is_charging()
    node.destroy_node()


def test_real_charging_is_detected():
    node, alerts = make_guard(median_samples=1)
    feed_volts(node, [7.12, 7.16, 7.20, 7.24, 7.28])
    assert node.is_charging()
    node.destroy_node()


def test_charging_blocks_the_alert():
    """발동선 아래에 있어도 오르는 중이면 경보하지 않는다.

    전 구간이 7.12V 아래라 low_window 는 가득 찬다. 그럼에도 상승 추세가
    이를 막아야 한다. 충전소에 꽂힌 로봇을 충전소로 또 보내지 않기 위함이다.
    """
    node, alerts = make_guard(median_samples=1)
    feed_volts(node, [6.90, 6.94, 6.98, 7.02, 7.06, 7.10, 7.11])
    assert alerts == []
    node.destroy_node()


def test_a_plateau_is_not_charging():
    """평평한 구간은 충전이 아니다. 낮으면 경보해야 한다.

    trend_rise_volt 가 노이즈 수준이면 이런 정체 구간까지 '충전 중'으로
    읽혀 경보가 영영 막힌다. 기존 2.0%p(=0.016V)가 그랬다.
    """
    node, alerts = make_guard(median_samples=1)
    feed_volts(node, [6.96, 7.00, 7.04, 7.08, 7.112, 7.112, 7.112])
    assert node.is_charging() is False
    assert alerts == [True], '정체 구간이 충전으로 읽혀 경보가 막혔다'
    node.destroy_node()


# ---------------------------------------------------------------- 재무장

def test_rearms_after_recovery_and_can_fire_again(guard):
    node, alerts = guard
    feed_volts(node, FILL + [7.104, 7.096, 7.088, 7.08])
    assert len(alerts) == 1
    # 재무장은 창(confirm_window)이 전부 기준치 위여야 인정된다.
    # 회복 표본 하나로 풀어주면 왕복하는 배터리에서 발동·해제가 반복된다.
    feed_volts(node, [7.16, 7.24, 7.32, 7.36, 7.40, 7.44, 7.48])   # 충전
    assert node.fired is False
    feed_volts(node, [7.112, 7.104, 7.096, 7.088])   # 다시 떨어짐
    assert alerts == [True, False, True]


def test_rearms_even_when_charging_has_finished():
    """충전이 끝나 전압이 평평해져도 재무장돼야 한다.

    재무장을 충전 감지 분기 안에 두면 만충인 채로 영영 무장 해제로 남는다.
    """
    node, alerts = make_guard(median_samples=1)
    feed_volts(node, FILL + [7.104, 7.096, 7.088])   # 필터 꺼서 지연 없음
    assert node.fired is True
    # 완충 후 평평한 구간만 들어온다 -> is_charging() 은 False
    # 창이 전부 기준치 위가 되도록 confirm_window 만큼 넣는다.
    feed_volts(node, [7.56] * 8)
    assert node.is_charging() is False
    assert node.fired is False, '평평한 완충 상태에서 재무장되지 않았다'
    node.destroy_node()


# ------------------------------------------- 클램프 구간 (전압 판정의 이유)

def test_charging_is_detected_above_the_percent_ceiling():
    """7.6V 위에서도 충전을 감지해야 한다.

    퍼센트로 판단하면 7.9~8.3V 가 전부 100.0 으로 들어와 상승폭이 0 이 되고,
    충전 중인 로봇을 영영 '충전 아님'으로 본다. 그러면 충전소에 꽂혀 있는
    로봇에게 저전압 경보를 내고 충전소로 또 보내게 된다.
    """
    node, alerts = make_guard(median_samples=1)
    feed_volts(node, [7.9, 8.0, 8.1, 8.2, 8.3])
    assert node.is_charging() is True, '클램프 구간에서 충전 감지가 죽었다'
    node.destroy_node()


def test_judgment_keeps_resolution_below_the_percent_floor():
    """6.8V 아래에서도 값이 구분돼야 한다.

    퍼센트로는 6.7V 와 6.3V 가 똑같이 0% 다. 가장 위험한 구간에서 판정
    입력의 해상도가 0 이 된다.
    """
    node, alerts = make_guard(median_samples=1)
    feed_volts(node, [6.7])
    assert node.last_voltage == pytest.approx(6.7)
    feed_volts(node, [6.3])
    assert node.last_voltage == pytest.approx(6.3)
    node.destroy_node()


def test_percent_fallback_is_converted_into_the_volt_window():
    """예비 경로도 전압으로 환산해 같은 창에 넣어야 한다.

    한 창에 퍼센트와 전압이 섞이면 중앙값도 최저값도 전부 무의미해진다.
    battery_publisher 는 percent 타이머를 먼저 만들므로 기동 직후 실제로
    섞인다.
    """
    node, alerts = make_guard(median_samples=1)
    feed_percents(node, [40.0])
    assert node.voltage == pytest.approx(LOW_V), '퍼센트가 그대로 창에 들어갔다'
    node.destroy_node()


def test_voltage_path_wins_over_percent_path():
    """전압을 한 번이라도 받으면 퍼센트는 무시한다."""
    node, alerts = make_guard(median_samples=1)
    feed_volts(node, [7.30])
    feed_percents(node, [5.0])
    assert node.voltage == pytest.approx(7.30), '예비 경로가 1차 소스를 덮었다'
    node.destroy_node()
