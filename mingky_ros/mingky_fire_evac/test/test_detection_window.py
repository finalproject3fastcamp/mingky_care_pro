"""Fire detection confirmation policy tests."""

import threading
from types import SimpleNamespace

from mingky_fire_evac.fire_evac_node import _detections_confirmed
from mingky_fire_evac.fire_evac_node import FireEvacNode


def test_confirms_five_detections_in_recent_seven_frames() -> None:
    assert _detections_confirmed(
        [True, False, True, True, False, True, True], required=5)


def test_does_not_confirm_below_required_count() -> None:
    assert not _detections_confirmed(
        [True, False, True, False, False, True, True], required=5)


def test_cancel_evacuation_requests_motion_stop_but_keeps_alarm_latched() -> None:
    calls = []
    node = SimpleNamespace(
        _evacuating=True,
        _alarm_latched=True,
        _cancel_evacuation=threading.Event(),
        _current_goal_handle=SimpleNamespace(
            cancel_goal_async=lambda: calls.append('goal')),
        cancel_nav_client=SimpleNamespace(
            service_is_ready=lambda: True,
            call_async=lambda request: calls.append('all')),
        get_logger=lambda: SimpleNamespace(warn=lambda message: None),
    )
    response = SimpleNamespace(success=None, message='')

    result = FireEvacNode._on_cancel_evacuation(node, None, response)

    assert result.success is True
    assert node._cancel_evacuation.is_set()
    assert node._alarm_latched is True
    assert calls == ['goal', 'all']
