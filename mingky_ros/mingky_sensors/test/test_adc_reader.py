"""
ADC 값 변환이 기존 구현과 어긋나지 않는지 검증한다.

adc_reader 는 pinkylib 을 쓰지 않고 같은 장치를 직접 읽는다. 그래서 변환식이
조금이라도 다르면 같은 배터리에 대해 기존과 다른 값이 나오고, battery_guard
의 임계값(7.12V / 7.28V)이 다른 지점을 뜻하게 된다.
"""

import sys
import types

# smbus2 는 로봇에만 있다. 모듈을 불러오기만 하면 되므로 대체한다.
if 'smbus2' not in sys.modules:
    stub = types.ModuleType('smbus2')
    stub.SMBus = object
    sys.modules['smbus2'] = stub

from mingky_sensors.adc_reader import (  # noqa: E402
    distance_from_count,
    percent_from_voltage,
    voltage_from_count,
)


def pinkylib_percentage(voltage):
    """pinkylib/battery.py 의 battery_percentage() 를 그대로 옮긴 것."""
    percent = (voltage - 6.8) / (7.6 - 6.8) * 100
    return round(max(0, min(100, percent)), 2)


def pinkylib_voltage(count):
    """pinkylib/battery.py 의 get_voltage() 변환부."""
    return (count / 4096.0) * 4.096 / (13.0 / 28.0)


def test_count_to_voltage_matches_pinkylib():
    """연산 순서까지 같아야 마지막 비트가 어긋나지 않는다."""
    for count in range(0, 4096, 7):
        assert voltage_from_count(count) == pinkylib_voltage(count), count


def test_percent_matches_pinkylib():
    """전 구간에서 pinkylib 과 같은 값을 내야 한다."""
    for millivolt in range(5000, 9001, 7):
        volt = millivolt / 1000.0
        assert percent_from_voltage(volt) == pinkylib_percentage(volt), volt


def test_known_battery_points():
    """battery_guard 의 임계값이 가리키는 지점이 그대로여야 한다."""
    assert percent_from_voltage(7.12) == 40.0     # low_voltage
    assert percent_from_voltage(7.28) == 60.0     # rearm_voltage
    assert percent_from_voltage(6.8) == 0.0
    assert percent_from_voltage(7.6) == 100.0


def test_distance_matches_pinkylib():
    """pinkylib/ultrasonic.py 의 get_dist() 와 같은 식이어야 한다."""
    for count in range(0, 4096, 13):
        assert distance_from_count(count) == (count / 4096.0) - 0.03, count


def test_distance_upper_bound():
    """식이 낼 수 있는 최댓값. main_node.cpp 의 max_range=3.0 과 맞지 않는다."""
    assert distance_from_count(4095) < 0.97
    assert distance_from_count(4095) > 0.96
