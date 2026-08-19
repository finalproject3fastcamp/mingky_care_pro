"""Low-rate C++ topic-health summaries retain the heartbeat contract."""

import threading

from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue

from mingky_event_gateway.gateway_node import EventGateway


def test_topic_health_summary_is_sanitized_and_cached() -> None:
    gateway = EventGateway.__new__(EventGateway)
    gateway._topic_health_lock = threading.Lock()
    gateway._topic_health_snapshot = {}
    message = DiagnosticArray(status=[
        DiagnosticStatus(
            name='/scan',
            values=[
                KeyValue(key='age_sec', value='0.0349'),
                KeyValue(key='hz', value='9.876'),
            ],
        ),
        DiagnosticStatus(
            name='/odom',
            values=[KeyValue(key='age_sec', value='invalid')],
        ),
    ])

    EventGateway._on_topic_health(gateway, message)

    assert gateway._topic_health_snapshot == {
        '/scan': {'age_sec': 0.035, 'hz': 9.88},
    }
