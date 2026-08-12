"""GuideState 값을 LCD에 표시할 문구와 색상으로 변환한다.

하드웨어와 PIL에 의존하지 않는 순수 함수로 두어 Raspberry Pi 없이도 모든
상태 전이를 검증할 수 있게 한다.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DisplayView:
    """Text and accent color for one complete LCD frame."""

    eyebrow: str
    title: str
    route_from: str = ''
    route_to: str = ''
    instruction: str = ''
    accent: str = 'blue'


def build_display_view(
        *, robot_state: str, session_state: str, previous_visit: str,
        current_visit: str) -> DisplayView:
    """안전 상태를 우선한 뒤 안내 세션 상태를 사람이 읽을 문구로 바꾼다."""
    if robot_state == 'battery_low':
        return DisplayView(
            '배터리 부족', '충전소로 복귀 중',
            instruction='로봇에서 잠시 떨어져 주세요', accent='red')
    if robot_state == 'paused':
        return DisplayView(
            '안전 정지', '잠시 정지했습니다',
            instruction='직원의 안내를 기다려 주세요', accent='red')
    if robot_state == 'comm_lost':
        return DisplayView(
            '연결 끊김', '안내를 계속할 수 없습니다',
            instruction='직원을 불러 주세요', accent='red')
    if robot_state == 'charging':
        return DisplayView(
            '운행 준비', '충전 중입니다',
            instruction='충전이 끝나면 안내를 시작합니다', accent='green')

    if session_state == 'qr_scanning':
        return DisplayView(
            '환자 확인', 'QR 카드를 확인 중입니다',
            instruction='카드를 카메라에 보여 주세요', accent='blue')
    if session_state == 'patient_confirmed':
        return DisplayView(
            '환자 확인 완료', '안내 시작을 기다립니다',
            route_from=previous_visit or '출발 위치',
            route_to=current_visit,
            instruction='잠시만 기다려 주세요', accent='blue')
    if session_state == 'guiding':
        if robot_state != 'moving':
            return DisplayView(
                '안내 일시 중지', '출발 준비 중입니다',
                route_from=previous_visit or '출발 위치',
                route_to=current_visit,
                instruction='직원의 안내를 기다려 주세요', accent='yellow')
        return DisplayView(
            '안내 중', f'{current_visit}로 이동합니다',
            route_from=previous_visit or '출발 위치',
            route_to=current_visit,
            instruction='로봇을 따라와 주세요', accent='green')
    if session_state == 'arrived':
        return DisplayView(
            '목적지 도착', f'{current_visit}에 도착했습니다',
            route_to=current_visit,
            instruction=(
                '대기 장소로 이동 중입니다'
                if robot_state == 'moving'
                else '안전을 위해 로봇 앞을 비워 주세요'),
            accent='green')
    if session_state == 'in_room':
        return DisplayView(
            '대기 장소 도착', f'{current_visit} 안내 완료',
            route_to=current_visit,
            instruction='검사 후 QR 카드를 보여 주세요', accent='green')
    if session_state == 'completed':
        return DisplayView(
            '안내 완료', '모든 안내가 끝났습니다',
            instruction='이용해 주셔서 감사합니다', accent='green')

    return DisplayView(
        'MINGKY CARE', '안내 대기 중',
        instruction='QR 카드를 준비해 주세요', accent='blue')
