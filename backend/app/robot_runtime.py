"""로봇 게이트웨이가 보고한 비영속 실행 상태."""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class RuntimeState:
    system_state: str
    localization_active: bool
    reported_at: datetime


_states: dict[str, RuntimeState] = {}


def update(robot_id: str, system_state: str, localization_active: bool) -> None:
    _states[robot_id] = RuntimeState(
        system_state=system_state,
        localization_active=localization_active,
        reported_at=datetime.now(timezone.utc),
    )


def snapshot() -> dict[str, RuntimeState]:
    return dict(_states)


def reset() -> None:
    _states.clear()
