"""후방 QR 거리의 최신 관측값을 보관하는 비영속 레지스트리."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


STALE_AFTER = timedelta(seconds=2)


@dataclass(frozen=True)
class QrObservation:
    visible: bool
    distance: float | None
    follow_state: str | None
    follow_distance: float | None
    follow_source: str | None
    qr_visible: bool
    visual_visible: bool
    observed_at: datetime
    patient_wait_remaining_sec: float | None = None


_observations: dict[str, QrObservation] = {}


def update(
    robot_id: str,
    visible: bool,
    distance: float | None,
    *,
    follow_state: str | None = None,
    follow_distance: float | None = None,
    follow_source: str | None = None,
    qr_visible: bool = False,
    visual_visible: bool = False,
    patient_wait_remaining_sec: float | None = None,
) -> None:
    _observations[robot_id] = QrObservation(
        visible=visible,
        distance=distance if visible else None,
        follow_state=follow_state,
        follow_distance=follow_distance,
        follow_source=follow_source,
        qr_visible=qr_visible,
        visual_visible=visual_visible,
        observed_at=datetime.now(timezone.utc),
        patient_wait_remaining_sec=patient_wait_remaining_sec,
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
            follow_state=(
                'waiting' if observation.follow_state is not None else None),
            follow_distance=None,
            follow_source=(
                'stale' if observation.follow_state is not None else None),
            qr_visible=False,
            visual_visible=False,
            observed_at=observation.observed_at,
            patient_wait_remaining_sec=None,
        )
    return observation


def reset() -> None:
    _observations.clear()
