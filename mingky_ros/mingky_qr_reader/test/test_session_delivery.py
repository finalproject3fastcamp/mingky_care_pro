"""최초 QR 세션 전달의 재시도와 복구 회귀 테스트."""

import sys
from types import ModuleType, SimpleNamespace

# 개발 PC에는 Raspberry Pi 런타임 의존성인 pyzbar가 없을 수 있다. 이 테스트는
# 카메라 디코드가 아니라 세션 전달만 검증하므로 import 경계만 최소 대체한다.
if 'pyzbar' not in sys.modules:
    package = ModuleType('pyzbar')
    module = ModuleType('pyzbar.pyzbar')
    module.ZBarSymbol = SimpleNamespace(QRCODE='QRCODE')
    module.decode = lambda *_args, **_kwargs: []
    package.pyzbar = module
    sys.modules['pyzbar'] = package
    sys.modules['pyzbar.pyzbar'] = module

from mingky_interfaces.msg import GuideState, SessionStart
from mingky_qr_reader.qr_reader_node import QrReaderNode


class FakePublisher:
    def __init__(self):
        self.messages = []

    def publish(self, message):
        self.messages.append(message)


class FakeLogger:
    def __init__(self):
        self.lines = []

    def info(self, message):
        self.lines.append(('info', message))

    def warn(self, message):
        self.lines.append(('warn', message))

    def error(self, message):
        self.lines.append(('error', message))


def _node(**overrides):
    values = {
        '_pending_session': None,
        '_session_publish_attempts': 0,
        'session_retry_limit': 3,
        '_session_pub': FakePublisher(),
        '_guide_state_seen': False,
        '_guide_session_id': 0,
        '_session_recovery_requested': False,
        '_completion_scan_enabled': False,
        '_preview': None,
        '_armed': False,
        'robot_id': 'pinky-01',
        'backend_url': 'https://backend.example/api',
        'http_timeout': 3.0,
        '_session_message': QrReaderNode._session_message,
        'get_logger': lambda: FakeLogger(),
    }
    values.update(overrides)
    node = SimpleNamespace(**values)
    node._disarm = lambda: QrReaderNode._disarm(node)
    return node


def _session(session_id=18):
    return SessionStart(
        session_id=session_id,
        patient_id='p003',
        current_step_order=1,
        visit_names=['X-ray', 'MRI'],
    )


def test_initial_session_retries_until_guide_state_acknowledges_it():
    message = _session()
    node = _node(_pending_session=message, _session_publish_attempts=1)

    QrReaderNode._retry_session_start(node)

    assert node._session_pub.messages == [message]
    assert node._session_publish_attempts == 2

    QrReaderNode._on_guide_state(node, GuideState(
        robot_id='pinky-01',
        session_id=18,
        session_state=GuideState.SESSION_CONFIRMED,
    ))

    assert node._pending_session is None
    assert node._session_publish_attempts == 0


def test_initial_session_stops_after_retry_limit():
    node = _node(
        _pending_session=_session(),
        _session_publish_attempts=3,
        session_retry_limit=3,
    )

    QrReaderNode._retry_session_start(node)

    assert node._session_pub.messages == []
    assert node._pending_session is None


def test_backend_recovery_selects_only_this_robots_active_session(monkeypatch):
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: [
            {
                'session_id': 17,
                'robot_id': 'pinky-02',
                'patient': {'patient_id': 'p002'},
                'current_step_order': 1,
                'steps': [{'step_order': 1, 'visit_name': 'CT'}],
            },
            {
                'session_id': 18,
                'robot_id': 'pinky-01',
                'patient': {'patient_id': 'p003'},
                'current_step_order': 1,
                'steps': [
                    {'step_order': 2, 'visit_name': 'MRI'},
                    {'step_order': 1, 'visit_name': 'X-ray'},
                ],
            },
        ],
    )
    monkeypatch.setattr(
        'mingky_qr_reader.qr_reader_node.requests.get',
        lambda *_args, **_kwargs: response,
    )
    node = _node(
        _guide_state_seen=True,
        _session_recovery_requested=True,
    )

    QrReaderNode._recover_active_session(node)

    assert len(node._session_pub.messages) == 1
    message = node._session_pub.messages[0]
    assert message.session_id == 18
    assert message.patient_id == 'p003'
    assert message.visit_names == ['X-ray', 'MRI']
    assert node._pending_session is message
    assert node._session_recovery_requested is False


def test_guide_restart_requests_one_backend_recovery():
    node = _node(_guide_state_seen=True, _guide_session_id=18)

    QrReaderNode._on_guide_state(node, GuideState(
        robot_id='pinky-01',
        session_id=0,
        session_state=GuideState.SESSION_NONE,
    ))

    assert node._session_recovery_requested is True


def test_returning_to_dock_stops_scanning_and_pending_delivery():
    node = _node(
        _armed=True,
        _pending_session=_session(),
        _session_publish_attempts=2,
    )

    QrReaderNode._on_guide_state(node, GuideState(
        robot_id='pinky-01',
        session_id=0,
        session_state=GuideState.SESSION_NONE,
        returning_to_dock=True,
    ))

    assert node._armed is False
    assert node._pending_session is None
    assert node._session_publish_attempts == 0
