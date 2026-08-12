"""
전압에서 퍼센트를 내는 식이 pinkylib 과 어긋나지 않는지 검증한다.

battery_publisher 는 get_voltage() 를 한 번만 부르고 퍼센트를 직접 계산한다.
그래서 이 식이 pinkylib.Battery.battery_percentage() 와 조금이라도 다르면
같은 배터리에 대해 두 값이 갈린다. 실제로 예전 구현은 두 함수를 따로 불러
"66.33% (8.18V)" 처럼 서로 맞지 않는 출력을 냈다.
"""

import sys
import types

# pinkylib 은 로봇에만 설치돼 있다. 모듈을 불러오기만 하면 되므로 대체한다.
if 'pinkylib' not in sys.modules:
    stub = types.ModuleType('pinkylib')
    stub.Battery = object
    sys.modules['pinkylib'] = stub

from pinky_bringup.battery_publisher import percent_from_voltage  # noqa: E402


def pinkylib_percentage(voltage):
    """pinkylib/battery.py 의 battery_percentage() 를 그대로 옮긴 것."""
    full_voltage = 7.6
    empty_voltage = 6.8
    percent = (voltage - empty_voltage) / (full_voltage - empty_voltage) * 100
    percent = max(0, min(100, percent))
    return round(percent, 2)


def test_matches_pinkylib_across_the_range():
    for millivolt in range(5000, 9001, 7):
        volt = millivolt / 1000.0
        assert percent_from_voltage(volt) == pinkylib_percentage(volt), volt


def test_known_points():
    assert percent_from_voltage(6.8) == 0.0
    assert percent_from_voltage(7.12) == 40.0
    assert percent_from_voltage(7.28) == 60.0
    assert percent_from_voltage(7.6) == 100.0


def test_clamped_outside_the_window():
    """6.8V 아래와 7.6V 위는 뭉개진다. 범위를 벗어나면 안 된다."""
    assert percent_from_voltage(4.98) == 0.0
    assert percent_from_voltage(8.4) == 100.0


def test_rounded_to_two_decimals():
    """자릿수까지 맞춘다. pinkylib 이 round(percent, 2) 를 하기 때문이다."""
    value = percent_from_voltage(7.3306)
    assert value == round(value, 2)
