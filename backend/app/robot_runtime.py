"""로봇 게이트웨이가 보고한 비영속 실행 상태."""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class RuntimeState:
    system_state: str
    localization_active: bool
    fire_alarm_active: bool | None
    reported_at: datetime
    state_since: datetime


_states: dict[str, RuntimeState] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def update(
    robot_id: str,
    system_state: str,
    localization_active: bool,
    fire_alarm_active: bool | None = None,
) -> None:
    now = _now()
    previous = _states.get(robot_id)
    _states[robot_id] = RuntimeState(
        system_state=system_state,
        localization_active=localization_active,
        fire_alarm_active=fire_alarm_active,
        reported_at=now,
        state_since=(
            previous.state_since
            if previous is not None and previous.system_state == system_state
            else now
        ),
    )


def snapshot() -> dict[str, RuntimeState]:
    return dict(_states)


def reset() -> None:
    _states.clear()
