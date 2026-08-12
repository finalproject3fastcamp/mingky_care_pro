#!/usr/bin/env python3
"""battery_logger 회귀 테스트. 로봇 없이 돈다.

기록기가 잘못 분류하면 근거 데이터 전체가 무의미해지므로, 조건 분류만
집중해서 본다.
"""

import csv

from geometry_msgs.msg import Twist
from mingky_battery_guard.battery_logger import BatteryLogger, percent_from_voltage
import pytest
import rclpy
from rclpy.parameter import Parameter
from std_msgs.msg import Float32


@pytest.fixture(scope='module', autouse=True)
def ros():
    rclpy.init()
    yield
    rclpy.shutdown()


@pytest.fixture
def logger(tmp_path):
    node = BatteryLogger(parameter_overrides=[
        Parameter('out_dir', value=str(tmp_path)),
        Parameter('rest_settle_sec', value=60.0),
    ])
    yield node
    node.destroy_node()


def move(node, moving=True):
    t = Twist()
    if moving:
        t.linear.x = 0.2
    node.on_cmd(t)


def test_percent_matches_pinkylib():
    assert percent_from_voltage(6.8) == pytest.approx(0.0)
    assert percent_from_voltage(7.6) == pytest.approx(100.0)
    assert percent_from_voltage(7.12) == pytest.approx(40.0)


def test_moving_is_classified_as_load(logger):
    move(logger)
    assert logger.state_now() == 'load'


def test_startup_without_any_cmd_is_not_load(logger):
    """움직이는 걸 못 봤다는 건 '정지'가 아니라 '모름'이다.

    기동 직후를 load 로 넣으면 부하 최저 전압 통계가 오염된다.
    """
    assert logger.last_motion_at is None
    assert logger.state_now() == 'unknown'


def test_unknown_samples_go_to_neither_bucket(logger):
    logger.on_voltage(Float32(data=7.4))
    assert logger.rest_n == 0
    assert logger.load_n == 0


def test_long_startup_without_cmd_becomes_rest(logger):
    """명령이 한 번도 없이 오래 지났으면 정지로 봐도 된다."""
    logger.started = logger.now() - 120.0
    assert logger.state_now() == 'rest'


# ---------------------------------------------- 요약 통계 (덱 길이와 무관)

def test_summary_keeps_the_full_range_not_just_recent(logger):
    """요약은 누적 집계로 낸다.

    덱으로 들고 있으면 30분 방치를 재는 동안 초기 고전압 표본이 밀려나가,
    '휴지 최대' 가 완충 전압이 아니라 '최근 N분 중 최고' 가 된다. ΔV 도
    같이 어긋난다.
    """
    logger.last_motion_at = logger.now() - 120.0
    logger.on_voltage(Float32(data=7.60))           # 완충 직후
    for _ in range(500):                            # 덱이었으면 밀려날 분량
        logger.on_voltage(Float32(data=7.10))
    assert logger.rest_max == pytest.approx(7.60), '초기 최고값이 밀려났다'
    assert logger.rest_min == pytest.approx(7.10)


def test_delta_v_uses_full_range(logger):
    logger.last_motion_at = logger.now() - 120.0
    logger.on_voltage(Float32(data=7.40))           # 휴지 최대
    move(logger)
    logger.on_voltage(Float32(data=6.90))           # 부하 최저
    assert logger.rest_max - logger.load_min == pytest.approx(0.5)


# ---------------------------------------------- settling 구간 자체가 데이터

def test_settling_rise_and_duration_are_collected(logger):
    """정지 후 자연 회복 상승폭을 모은다.

    battery_guard 의 trend_rise 가 이 값보다 작으면 모터 부하 해제를 충전으로
    오인해 저전압 경보가 영영 막힌다. 그래서 이 구간이 곧 정해야 할 값이다.
    """
    move(logger)
    logger.on_voltage(Float32(data=6.90))           # 부하 중
    logger.last_motion_at = logger.now() - 5.0      # 멈춤 -> settling
    logger.on_voltage(Float32(data=7.00))           # 회복 시작점
    logger.on_voltage(Float32(data=7.20))
    logger.last_motion_at = logger.now() - 120.0    # 휴지 도달 -> 구간 종료
    logger.on_voltage(Float32(data=7.35))

    assert len(logger.settle_rises) == 1
    rise, dur = logger.settle_rises[0]
    assert rise == pytest.approx(0.35, abs=1e-6), '상승폭이 시작→끝으로 안 잡힌다'
    assert dur >= 0.0


def test_just_stopped_is_not_rest_yet(logger):
    """모터를 멈추자마자 잰 값은 아직 처져 있다. 휴지로 세면 안 된다."""
    move(logger)
    logger.last_motion_at = logger.now() - 5.0      # 5초 전에 멈춤
    assert logger.state_now() == 'settling'


def test_long_idle_is_rest(logger):
    logger.last_motion_at = logger.now() - 120.0    # 2분 전에 멈춤
    assert logger.state_now() == 'rest'


def test_settling_samples_go_to_neither_bucket(logger):
    """회복 중 표본을 휴지에 섞으면 비교 기준이 망가진다."""
    logger.last_motion_at = logger.now() - 5.0
    logger.on_voltage(Float32(data=7.0))
    assert logger.rest_n == 0
    assert logger.load_n == 0


def test_rest_and_load_are_separated(logger):
    move(logger)
    logger.on_voltage(Float32(data=6.9))            # 부하 중
    logger.last_motion_at = logger.now() - 120.0
    logger.on_voltage(Float32(data=7.4))            # 휴지
    assert logger.load_min == pytest.approx(6.9)
    assert logger.rest_last == pytest.approx(7.4)
    assert logger.load_n == 1 and logger.rest_n == 1


def test_csv_has_a_row_per_sample(logger):
    logger.last_motion_at = logger.now() - 120.0
    for v in (7.40, 7.38, 7.36):
        logger.on_voltage(Float32(data=v))
    logger.fh.flush()
    with open(logger.path, encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3
    assert rows[0]['state'] == 'rest'
    assert rows[0]['voltage'] == '7.400'


def test_published_percent_is_recorded_for_comparison(logger):
    """발행된 퍼센트도 같이 남긴다. 둘을 비교하는 게 이 기록의 목적이다."""
    logger.on_percent(Float32(data=73.0))
    logger.last_motion_at = logger.now() - 120.0
    logger.on_voltage(Float32(data=7.4))
    logger.fh.flush()
    with open(logger.path, encoding='utf-8') as f:
        row = list(csv.DictReader(f))[0]
    assert row['percent_published'] == '73.0'
    assert row['percent_linear'] == '75.0'


def test_label_is_recorded_and_in_filename(tmp_path):
    """부하 조건이 다른 기록을 섞으면 비교가 무의미해진다. 라벨로 구분한다."""
    node = BatteryLogger(parameter_overrides=[
        Parameter('out_dir', value=str(tmp_path)),
        Parameter('session_label', value='full_system'),
    ])
    node.last_motion_at = node.now() - 120.0
    node.on_voltage(Float32(data=7.4))
    node.fh.flush()
    assert 'full_system' in node.path.name
    with open(node.path, encoding='utf-8') as f:
        assert list(csv.DictReader(f))[0]['label'] == 'full_system'
    node.destroy_node()
