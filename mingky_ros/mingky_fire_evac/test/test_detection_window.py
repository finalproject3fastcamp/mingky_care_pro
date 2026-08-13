"""Fire detection confirmation policy tests."""

from mingky_fire_evac.fire_evac_node import _detections_confirmed


def test_confirms_five_detections_in_recent_seven_frames() -> None:
    assert _detections_confirmed(
        [True, False, True, True, False, True, True], required=5)


def test_does_not_confirm_below_required_count() -> None:
    assert not _detections_confirmed(
        [True, False, True, False, False, True, True], required=5)
