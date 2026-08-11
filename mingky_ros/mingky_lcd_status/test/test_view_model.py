"""GuideState-to-LCD view mapping tests."""

from mingky_lcd_status.view_model import build_display_view


def view(robot='idle', session='none', previous='', current=''):
    """Build a view with concise defaults for state mapping tests."""
    return build_display_view(
        robot_state=robot,
        session_state=session,
        previous_visit=previous,
        current_visit=current,
    )


def test_first_destination_uses_generic_departure_label():
    """The first leg names its unknown origin without inventing a waypoint."""
    result = view('moving', 'guiding', current='X-ray')

    assert result.route_from == '출발 위치'
    assert result.route_to == 'X-ray'
    assert result.title == 'X-ray로 이동합니다'


def test_next_destination_shows_previous_visit():
    """Later legs show the previous clinical visit as their origin."""
    result = view('moving', 'guiding', previous='X-ray', current='CT')

    assert result.route_from == 'X-ray'
    assert result.route_to == 'CT'


def test_arrival_and_waiting_spot_have_distinct_messages():
    """Clinical arrival and waiting-spot arrival remain distinguishable."""
    arrived = view('moving', 'arrived', previous='X-ray', current='CT')
    waiting = view('waiting', 'in_room', previous='X-ray', current='CT')

    assert arrived.eyebrow == '목적지 도착'
    assert arrived.instruction == '대기 장소로 이동 중입니다'
    assert waiting.eyebrow == '대기 장소 도착'
    assert 'QR 카드' in waiting.instruction


def test_completed_session_thanks_patient():
    """The final state clearly tells the patient the guidance is finished."""
    result = view('idle', 'completed', previous='CT', current='CT')

    assert result.eyebrow == '안내 완료'
    assert '감사합니다' in result.instruction


def test_safety_state_has_priority_over_guidance():
    """Safety warnings replace normal route guidance."""
    result = view('paused', 'guiding', previous='X-ray', current='CT')

    assert result.eyebrow == '안전 정지'
    assert result.accent == 'red'
    assert result.route_to == ''
