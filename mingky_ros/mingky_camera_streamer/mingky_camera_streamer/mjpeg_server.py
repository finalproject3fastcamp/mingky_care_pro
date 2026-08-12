"""Small on-demand MJPEG server shared by camera-producing ROS nodes."""

from __future__ import annotations

import threading
import time

import cv2


class MjpegServer:
    """Serve the newest JPEG only while at least one viewer is connected."""

    def __init__(
        self,
        port: int,
        logger,
        *,
        max_width: int = 640,
        quality: int = 60,
        max_fps: float = 10.0,
    ) -> None:
        from flask import Flask, Response, jsonify

        self._cond = threading.Condition()
        self._jpeg: bytes | None = None
        self._seq = 0
        self._clients = 0
        self._max_width = max_width
        self._quality = quality
        self._min_period = 1.0 / max(max_fps, 0.1)
        self._last_encoded = 0.0

        app = Flask(__name__)

        @app.get('/')
        def _index():
            return ('<html><body style="margin:0;background:#000">'
                    '<img src="stream" style="width:100%"></body></html>')

        @app.get('/health')
        def _health():
            return jsonify({'status': 'ok', 'viewers': self.viewer_count})

        @app.get('/stream')
        def _stream():
            return Response(
                self._frames(),
                mimetype='multipart/x-mixed-replace; boundary=frame',
                headers={'Cache-Control': 'no-store'},
            )

        self._thread = threading.Thread(
            target=lambda: app.run(
                host='127.0.0.1', port=port, threaded=True, use_reloader=False),
            daemon=True,
        )
        self._thread.start()
        logger.info(
            f'MJPEG server: http://127.0.0.1:{port}/stream '
            f'(max_width={max_width}, max_fps={max_fps:.1f}, quality={quality})')

    @property
    def viewer_count(self) -> int:
        with self._cond:
            return self._clients

    @property
    def has_viewers(self) -> bool:
        return self.viewer_count > 0

    def _frames(self):
        last = -1
        with self._cond:
            self._clients += 1
        try:
            while True:
                with self._cond:
                    if not self._cond.wait_for(
                            lambda: self._seq != last, timeout=2.0):
                        continue
                    last = self._seq
                    buf = self._jpeg
                if buf is not None:
                    yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n'
                           + buf + b'\r\n')
        finally:
            with self._cond:
                self._clients = max(0, self._clients - 1)

    def clear(self) -> None:
        with self._cond:
            self._jpeg = None
            self._seq += 1
            self._cond.notify_all()

    def update(self, frame) -> bool:
        """Encode and publish a frame if a viewer is present and FPS allows it."""
        if not self.has_viewers:
            return False
        now = time.monotonic()
        if now - self._last_encoded < self._min_period:
            return False

        display = frame
        if 0 < self._max_width < display.shape[1]:
            scale = self._max_width / display.shape[1]
            display = cv2.resize(
                display,
                (self._max_width, max(int(display.shape[0] * scale), 1)),
                interpolation=cv2.INTER_AREA,
            )
        ok, encoded = cv2.imencode(
            '.jpg', display, [int(cv2.IMWRITE_JPEG_QUALITY), self._quality])
        if not ok:
            return False

        with self._cond:
            self._jpeg = encoded.tobytes()
            self._seq += 1
            self._last_encoded = now
            self._cond.notify_all()
        return True
