"""최초 QR arming과 검사 완료 QR 스캔 창을 구분한다."""

from mingky_interfaces.msg import GuideState
from mingky_qr_reader.scan_policy import completion_scan_enabled


def _state(*, robot='pinky-01', session=81, session_state='', robot_state=''):
    return GuideState(
        robot_id=robot,
        session_id=session,
        session_state=session_state,
        robot_state=robot_state,
    )


def test_completion_scan_opens_only_at_waiting_spot() -> None:
    state = _state(
        session_state=GuideState.SESSION_IN_ROOM,
        robot_state=GuideState.ROBOT_WAITING,
    )

    assert completion_scan_enabled(state, 'pinky-01')


def test_completion_scan_stays_closed_while_moving_or_for_other_robot() -> None:
    moving = _state(
        session_state=GuideState.SESSION_GUIDING,
        robot_state=GuideState.ROBOT_MOVING,
    )
    other_robot = _state(
        robot='pinky-02',
        session_state=GuideState.SESSION_IN_ROOM,
        robot_state=GuideState.ROBOT_WAITING,
    )
    no_session = _state(
        session=0,
        session_state=GuideState.SESSION_IN_ROOM,
        robot_state=GuideState.ROBOT_WAITING,
    )

    assert not completion_scan_enabled(moving, 'pinky-01')
    assert not completion_scan_enabled(other_robot, 'pinky-01')
    assert not completion_scan_enabled(no_session, 'pinky-01')
