"""실시간 관제 중계가 느린 브라우저 탭 때문에 함께 멈추지 않는지 검증한다."""

import asyncio

from app.routers import teleop


class _Operator:
    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.messages: list[str] = []
        self.closed = False

    async def send_text(self, message: str) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        self.messages.append(message)

    async def close(self) -> None:
        self.closed = True


def test_slow_operator_does_not_block_other_operator(monkeypatch):
    robot_id = "pinky-test"
    fast = _Operator()
    slow = _Operator(delay=1.0)
    monkeypatch.setattr(teleop, "OPERATOR_SEND_TIMEOUT_SECONDS", 0.01)
    teleop._operators[robot_id] = {fast, slow}

    try:
        asyncio.run(teleop._broadcast_to_operators(robot_id, '{"type":"plan"}'))

        assert fast.messages == ['{"type":"plan"}']
        assert fast in teleop._operators[robot_id]
        assert slow not in teleop._operators[robot_id]
        assert slow.closed is True
    finally:
        teleop._operators.pop(robot_id, None)
