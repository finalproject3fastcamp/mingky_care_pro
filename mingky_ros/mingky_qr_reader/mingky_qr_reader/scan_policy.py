"""QR 스캔 활성화 규칙.

카메라·HTTP 의존성 없이 상태 전이 규칙만 테스트할 수 있게 분리한다.
"""

from mingky_interfaces.msg import GuideState


def completion_scan_enabled(state: GuideState, robot_id: str) -> bool:
    """검사실 waiting spot에서만 arming 없는 재스캔을 허용한다."""
    return (
        state.robot_id == robot_id
        and state.session_id > 0
        and state.session_state == GuideState.SESSION_IN_ROOM
        and state.robot_state == GuideState.ROBOT_WAITING
    )
