"""후방 QR 거리의 최신 관측값을 보관하는 비영속 레지스트리."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


STALE_AFTER = timedelta(seconds=2)


@dataclass(frozen=True)
class QrObservation:
    visible: bool
    distance: float | None
    observed_at: datetime


_observations: dict[str, QrObservation] = {}


def update(robot_id: str, visible: bool, distance: float | None) -> None:
    _observations[robot_id] = QrObservation(
        visible=visible,
        distance=distance if visible else None,
        observed_at=datetime.now(timezone.utc),
    )


def get(robot_id: str, now: datetime | None = None) -> QrObservation | None:
    observation = _observations.get(robot_id)
    if observation is None:
        return None
    current = now or datetime.now(timezone.utc)
    if current - observation.observed_at > STALE_AFTER:
        return QrObservation(
            visible=False,
            distance=None,
            observed_at=observation.observed_at,
        )
    return observation


def reset() -> None:
    _observations.clear()
