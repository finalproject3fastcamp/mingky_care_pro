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
    assert len(logger.rest_v) == 0
    assert len(logger.load_v) == 0


def test_rest_and_load_are_separated(logger):
    move(logger)
    logger.on_voltage(Float32(data=6.9))            # 부하 중
    logger.last_motion_at = logger.now() - 120.0
    logger.on_voltage(Float32(data=7.4))            # 휴지
    assert list(logger.load_v) == [6.9]
    assert list(logger.rest_v) == [7.4]


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
