"""Pinky 카메라에서 QR을 읽어 백엔드로 patient_id를 POST하는 노드."""

from __future__ import annotations

import time
from dataclasses import dataclass

import cv2
import rclpy
import requests
from pyzbar import pyzbar
from rclpy.node import Node


@dataclass
class LastScan:
    value: str
    ts: float


class QrReaderNode(Node):
    def __init__(self) -> None:
        super().__init__("qr_reader_node")

        self.declare_parameter("source", "image")
        self.declare_parameter("image_path", "")
        self.declare_parameter("usb_device_index", 0)
        self.declare_parameter("backend_url", "http://localhost:8000")
        self.declare_parameter("fps", 10.0)
        self.declare_parameter("debounce_seconds", 5.0)
        self.declare_parameter("http_timeout_seconds", 3.0)

        self.source = self.get_parameter("source").value
        self.backend_url = self.get_parameter("backend_url").value.rstrip("/")
        self.debounce_seconds = float(self.get_parameter("debounce_seconds").value)
        self.http_timeout = float(self.get_parameter("http_timeout_seconds").value)
        fps = float(self.get_parameter("fps").value)

        self._capture: cv2.VideoCapture | None = None
        self._static_frame = None
        self._last: LastScan | None = None

        self._setup_source()

        period = 1.0 / max(fps, 0.1)
        self._timer = self.create_timer(period, self._tick)
        self.get_logger().info(
            f"qr_reader_node started (source={self.source}, backend={self.backend_url})"
        )

    def _setup_source(self) -> None:
        if self.source == "image":
            path = self.get_parameter("image_path").value
            if not path:
                raise RuntimeError("source=image 이면 image_path 파라미터가 필요합니다")
            frame = cv2.imread(path)
            if frame is None:
                raise RuntimeError(f"이미지를 읽을 수 없습니다: {path}")
            self._static_frame = frame
        elif self.source == "usb":
            index = int(self.get_parameter("usb_device_index").value)
            capture = cv2.VideoCapture(index)
            if not capture.isOpened():
                raise RuntimeError(f"USB 카메라를 열 수 없습니다 (index={index})")
            self._capture = capture
        elif self.source == "csi":
            # TODO(pinky): IMX219 GStreamer/Picamera2 파이프라인 붙이기
            raise NotImplementedError("source=csi 는 아직 미구현 (Pinky 실기 연결 시 추가)")
        else:
            raise RuntimeError(f"알 수 없는 source: {self.source}")

    def _read_frame(self):
        if self._static_frame is not None:
            return self._static_frame
        assert self._capture is not None
        ok, frame = self._capture.read()
        if not ok:
            return None
        return frame

    def _tick(self) -> None:
        frame = self._read_frame()
        if frame is None:
            return

        for code in pyzbar.decode(frame):
            if code.type != "QRCODE":
                continue
            value = code.data.decode("utf-8", errors="replace").strip()
            if not value:
                continue
            if self._is_debounced(value):
                continue
            self._last = LastScan(value=value, ts=time.monotonic())
            self._post_scan(value)
            return

    def _is_debounced(self, value: str) -> bool:
        if self._last is None:
            return False
        if self._last.value != value:
            return False
        return (time.monotonic() - self._last.ts) < self.debounce_seconds

    def _post_scan(self, patient_id: str) -> None:
        url = f"{self.backend_url}/qr/scan"
        try:
            response = requests.post(
                url,
                json={"patient_id": patient_id},
                timeout=self.http_timeout,
            )
        except requests.RequestException as exc:
            self.get_logger().error(f"백엔드 호출 실패: {exc}")
            return

        if response.status_code == 200:
            self.get_logger().info(f"QR 확인 성공: {patient_id}")
        elif response.status_code == 404:
            self.get_logger().warn(f"QR 인식 실패 (환자 없음): {patient_id}")
        else:
            self.get_logger().error(
                f"백엔드 응답 이상: {response.status_code} {response.text[:200]}"
            )

    def destroy_node(self) -> bool:
        if self._capture is not None:
            self._capture.release()
        return super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = QrReaderNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
