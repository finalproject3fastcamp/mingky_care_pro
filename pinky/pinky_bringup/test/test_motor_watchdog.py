"""모터 명령 두절 시 마지막 RPM을 유지하지 않는지 검증한다."""

from types import SimpleNamespace

from pinky_bringup.bringup import Pinky


class Stamp:
    def __init__(self, seconds):
        self.nanoseconds = int(seconds * 1e9)

    def __sub__(self, other):
        return SimpleNamespace(nanoseconds=self.nanoseconds - other.nanoseconds)


class Driver:
    def __init__(self):
        self.commands = []

    def set_double_rpm(self, left, right):
        self.commands.append((left, right))
        return True


def test_motor_is_zeroed_after_command_timeout():
    robot = SimpleNamespace(
        motor_command_active=True,
        last_cmd_time=Stamp(1.0),
        command_timeout=0.5,
        driver=Driver(),
        get_logger=lambda: SimpleNamespace(warn=lambda message: None,
                                           error=lambda message: None),
    )

    Pinky._enforce_command_timeout(robot, Stamp(1.6))

    assert robot.driver.commands == [(0, 0)]
    assert robot.motor_command_active is False


def test_recent_command_is_left_unchanged():
    robot = SimpleNamespace(
        motor_command_active=True,
        last_cmd_time=Stamp(1.0),
        command_timeout=0.5,
        driver=Driver(),
        get_logger=lambda: SimpleNamespace(warn=lambda message: None,
                                           error=lambda message: None),
    )

    Pinky._enforce_command_timeout(robot, Stamp(1.2))

    assert robot.driver.commands == []
    assert robot.motor_command_active is True
