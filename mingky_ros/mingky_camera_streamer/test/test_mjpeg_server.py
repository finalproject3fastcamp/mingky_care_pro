import threading

import numpy as np

from mingky_camera_streamer.mjpeg_server import MjpegServer


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
