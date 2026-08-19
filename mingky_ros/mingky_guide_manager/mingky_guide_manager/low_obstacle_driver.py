"""SidestepStrategy 명령을 Nav2 액션으로 비동기 실행한다."""

import math

from action_msgs.msg import GoalStatus
from builtin_interfaces.msg import Duration
from geometry_msgs.msg import Point
from nav2_msgs.action import DriveOnHeading, Spin

from .low_obstacle import (
    LowObstacleConfig,
    MotionCommand,
    MotionResult,
    SidestepOutcome,
    SidestepStrategy,
)


class SidestepActionDriver:
    """ROS executor를 막지 않고 Spin/DriveOnHeading 시퀀스를 실행한다."""

    def __init__(
            self, node, spin_client, drive_client, config: LowObstacleConfig,
            on_complete, *, range_timeout_sec: float = 0.8):
        self._node = node
        self._spin_client = spin_client
        self._drive_client = drive_client
        self._config = config
        self._strategy = SidestepStrategy(config)
        self._on_complete = on_complete
        self._range_timeout_sec = max(0.1, float(range_timeout_sec))
        self._sequence = None
        self._active_handle = None
        self._active_command = None
        self._generation = 0
        self._latest_range = None
        self._range_sequence = 0
        self._command_range_sequence = 0
        self._waiting_for_range = False
        self._range_timer = None

    @property
    def active(self) -> bool:
        return self._sequence is not None

    def update_range(self, range_m: float) -> None:
        value = float(range_m)
        self._latest_range = value if math.isfinite(value) and value >= 0.0 else None
        self._range_sequence += 1
        if (
                self._active_handle is not None
                and self._active_command is not None
                and self._active_command.kind == 'drive'
                and self._latest_range is not None
                and self._latest_range < self._config.minimum_drive_clearance_m):
            self._active_handle.cancel_goal_async()
            self._finish(SidestepOutcome(
                False,
                None,
                'obstacle became too close during active sidestep drive',
            ))
            return
        if not self._waiting_for_range or self._sequence is None:
            return
        self._waiting_for_range = False
        self._cancel_range_timer()
        self._advance(MotionResult(True, self._latest_range))

    def start(self, preferred_side: int | None = None) -> bool:
        if self.active:
            return False
        if not self._spin_client.wait_for_server(timeout_sec=1.0):
            self._finish(SidestepOutcome(False, None, 'Spin action server unavailable'))
            return False
        if not self._drive_client.wait_for_server(timeout_sec=1.0):
            self._finish(SidestepOutcome(
                False, None, 'DriveOnHeading action server unavailable'))
            return False
        self._generation += 1
        self._sequence = self._strategy.commands(preferred_side)
        self._advance()
        return True

    def cancel(self) -> None:
        self._generation += 1
        if self._active_handle is not None:
            self._active_handle.cancel_goal_async()
        self._active_handle = None
        self._active_command = None
        self._sequence = None
        self._waiting_for_range = False
        self._cancel_range_timer()

    def _advance(self, result: MotionResult | None = None) -> None:
        sequence = self._sequence
        if sequence is None:
            return
        try:
            command = next(sequence) if result is None else sequence.send(result)
        except StopIteration as finished:
            self._finish(finished.value)
            return
        self._send(command)

    def _send(self, command: MotionCommand) -> None:
        self._active_command = command
        self._command_range_sequence = self._range_sequence
        generation = self._generation
        if command.kind == 'spin':
            goal = Spin.Goal()
            goal.target_yaw = float(command.value)
            goal.time_allowance = Duration(sec=8)
            future = self._spin_client.send_goal_async(goal)
        else:
            goal = DriveOnHeading.Goal()
            goal.target = Point(x=float(command.value), y=0.0, z=0.0)
            goal.speed = float(command.speed)
            goal.time_allowance = Duration(sec=10)
            future = self._drive_client.send_goal_async(goal)
        future.add_done_callback(
            lambda done: self._on_goal_response(done, generation, command))

    def _on_goal_response(
            self, future, generation: int, command: MotionCommand) -> None:
        if generation != self._generation or self._sequence is None:
            return
        try:
            handle = future.result()
        except Exception as exc:  # noqa: BLE001 - 액션 오류를 결과로 변환
            self._node.get_logger().error(f'저상 장애물 회피 목표 전송 실패: {exc}')
            self._advance(MotionResult(False))
            return
        if not handle.accepted:
            self._advance(MotionResult(False))
            return
        self._active_handle = handle
        result = handle.get_result_async()
        result.add_done_callback(
            lambda done: self._on_action_result(done, generation, command))

    def _on_action_result(
            self, future, generation: int, command: MotionCommand) -> None:
        if generation != self._generation or self._sequence is None:
            return
        self._active_handle = None
        try:
            succeeded = future.result().status == GoalStatus.STATUS_SUCCEEDED
        except Exception as exc:  # noqa: BLE001 - 액션 오류를 결과로 변환
            self._node.get_logger().error(f'저상 장애물 회피 동작 실패: {exc}')
            succeeded = False
        if not succeeded or not command.sample_range:
            self._advance(MotionResult(succeeded))
            return
        if self._range_sequence > self._command_range_sequence:
            self._advance(MotionResult(True, self._latest_range))
            return
        self._waiting_for_range = True
        self._range_timer = self._node.create_timer(
            self._range_timeout_sec,
            lambda: self._on_range_timeout(generation),
        )

    def _on_range_timeout(self, generation: int) -> None:
        if generation != self._generation or not self._waiting_for_range:
            return
        self._waiting_for_range = False
        self._cancel_range_timer()
        self._advance(MotionResult(True, None))

    def _cancel_range_timer(self) -> None:
        if self._range_timer is None:
            return
        self._range_timer.cancel()
        self._node.destroy_timer(self._range_timer)
        self._range_timer = None

    def _finish(self, outcome: SidestepOutcome) -> None:
        self._cancel_range_timer()
        self._sequence = None
        self._active_handle = None
        self._active_command = None
        self._waiting_for_range = False
        self._on_complete(outcome)
