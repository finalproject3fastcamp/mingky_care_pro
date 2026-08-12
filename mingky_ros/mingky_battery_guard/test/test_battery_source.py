#!/usr/bin/env python3
"""battery_source 회귀 테스트. 로봇 없이 돈다.

두 가지를 고정한다.
  - 값이 없는 상태(아직 못 받음 / 너무 오래됨)를 값으로 취급하지 않는다
  - 손으로 돌리는 스크립트들이 I2C 를 직접 열지 않는다

두 번째가 이 패키지에서 조용히 깨지기 쉬운 쪽이다. pinkylib.Battery 를 한 줄
되살리면 adc_reader 와 리더가 둘이 되어 전압이 낮게 나오는데, 예외도 안 나고
발행자 수로도 안 잡힌다 (스크립트는 토픽을 발행하지 않으므로).
"""

import ast
from pathlib import Path
import time

from mingky_battery_guard.battery_source import VoltageSource
import pytest
import rclpy
from std_msgs.msg import Float32

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


@pytest.fixture(scope='module', autouse=True)
def ros():
    rclpy.init()
    yield
    rclpy.shutdown()


@pytest.fixture
def source():
    node = VoltageSource('test_voltage_source')
    yield node
    node.destroy_node()


def send(node, volt):
    node.on_voltage(Float32(data=float(volt)))


# --- 값이 없는 상태 -----------------------------------------------------


def test_no_voltage_before_first_sample(source):
    assert source.voltage() is None


def test_latest_sample_is_returned(source):
    send(source, 7.31)
    assert source.voltage() == pytest.approx(7.31)


def test_stale_value_is_treated_as_missing():
    """발행이 끊겨도 마지막 값은 변수에 남는다. 그걸 쓰면 안 된다.

    멈춘 값은 읽기 실패보다 위험하다. 실패는 눈에 띄지만 멈춘 값은 정상으로
    보여서, 배터리가 계속 떨어지는 중에도 판정이 그 자리에 굳는다.
    """
    node = VoltageSource('test_stale', stale_after_sec=0.01)
    try:
        send(node, 7.31)
        assert node.voltage() == pytest.approx(7.31)
        time.sleep(0.05)
        assert node.voltage() is None, '오래된 값이 그대로 나온다'
    finally:
        node.destroy_node()


def test_wait_for_sample_waits_for_a_new_one(source):
    """들고 있던 값이 아니라 새로 온 것을 기다려야 한다.

    모터를 멈춘 직후의 전압을 보려면, 부하 중에 받아둔 값으로 답하면 안 된다.
    """
    send(source, 7.31)
    assert source.wait_for_sample(0.2) is False


# --- 스크립트가 I2C 를 직접 열지 않는다 ---------------------------------


def pinkylib_names(path):
    """그 파일이 pinkylib 에서 가져다 쓰는 이름들."""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == 'pinkylib':
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split('.')[0] == 'pinkylib':
                    names.add(alias.name)
    return names


def test_beep_script_does_not_open_i2c():
    assert pinkylib_names(SCRIPTS / 'battery_beep_standalone.py') == set()


def test_motor_load_keeps_motor_but_not_battery():
    """Motor 는 다른 장치라 그대로 둔다. Battery 만 토픽으로 옮겼다."""
    assert pinkylib_names(SCRIPTS / 'motor_load.py') == {'Motor'}
