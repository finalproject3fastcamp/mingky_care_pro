import threading

import numpy as np

from mingky_camera_streamer.image_streamer_node import ImageStreamerNode
from mingky_camera_streamer.mjpeg_server import MjpegServer
from std_msgs.msg import Bool


def _server(*, clients: int) -> MjpegServer:
    server = MjpegServer.__new__(MjpegServer)
    server._cond = threading.Condition()
    server._jpeg = None
    server._seq = 0
    server._clients = clients
    server._max_width = 640
    server._quality = 60
    server._min_period = 0.5
    server._last_encoded = 0.0
    return server


def test_frame_is_not_encoded_without_a_viewer(monkeypatch):
    server = _server(clients=0)
    monkeypatch.setattr(
        'mingky_camera_streamer.mjpeg_server.cv2.imencode',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('encode must not run')),
    )

    assert server.update(np.zeros((10, 10, 3), dtype=np.uint8)) is False
    assert server._seq == 0


def test_frame_rate_is_limited_for_connected_viewer(monkeypatch):
    server = _server(clients=1)
    calls = []

    def encode(*_args, **_kwargs):
        calls.append(1)
        return True, np.array([1, 2, 3], dtype=np.uint8)

    monkeypatch.setattr(
        'mingky_camera_streamer.mjpeg_server.cv2.imencode', encode)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    assert server.update(frame) is True
    assert server.update(frame) is False
    assert len(calls) == 1
    assert server._seq == 1


def test_tracking_encoder_stays_idle_when_processing_is_disabled():
    node = ImageStreamerNode.__new__(ImageStreamerNode)
    node._compressed_enabled = False
    node._compressed_enable_topic = '/person_follow/processing_active'
    node.count_publishers = lambda _topic: 1
    node._compressed_pub = type(
        'Publisher', (), {'get_subscription_count': lambda self: 1})()
    node._server = type('Server', (), {'has_viewers': False})()
    node._latest = object()
    node._bridge = type('Bridge', (), {
        'imgmsg_to_cv2': lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('disabled tracking must not convert frames')),
    })()

    node._publish_latest()


def test_tracking_encoder_enable_state_is_applied():
    node = ImageStreamerNode.__new__(ImageStreamerNode)
    node._compressed_enabled = False

    node._on_compressed_enabled(Bool(data=True))

    assert node._compressed_enabled is True


def test_tracking_encoder_fails_open_without_gate_publisher():
    node = ImageStreamerNode.__new__(ImageStreamerNode)
    node._compressed_enabled = False
    node._compressed_enable_topic = '/person_follow/processing_active'
    node.count_publishers = lambda _topic: 0

    assert node._compressed_gate_allows_processing() is True
