"""Publish person-follow events without coupling to Guide Manager internals."""

import json
import time
import uuid

from builtin_interfaces.msg import Time
from mingky_interfaces.msg import Event

_LEVELS = {
    'info': Event.LEVEL_INFO,
    'warning': Event.LEVEL_WARNING,
    'error': Event.LEVEL_ERROR,
}


class PersonFollowEventPublisher:

    def __init__(self, node, robot_id: str):
        self._node = node
        self._robot_id = robot_id
        self._publisher = node.create_publisher(Event, '/events', 10)

    def publish(self, code: str, payload: dict | None = None, level: str = 'info') -> None:
        now_ns = time.time_ns()
        msg = Event()
        msg.event_id = str(uuid.uuid4())
        msg.robot_id = self._robot_id
        msg.session_id = 0
        msg.occurred_at = Time(
            sec=now_ns // 1_000_000_000,
            nanosec=now_ns % 1_000_000_000,
        )
        msg.level = _LEVELS[level]
        msg.event_code = code
        msg.source_node = self._node.get_name()
        msg.payload = json.dumps(
            payload or {}, ensure_ascii=False, separators=(',', ':'))
        self._publisher.publish(msg)
