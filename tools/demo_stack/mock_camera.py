#!/usr/bin/env python3
"""회수된 로봇 자리에 서는 MJPEG 카메라.

## 무엇을 대신하나

실기가 있을 때 카메라는 이렇게 흘렀다 (deploy/robot/systemd/mingky-camera-tunnel.service).

    [로봇 8091/8092] --ssh -R--> [클라우드 127.0.0.1:1880x] --nginx--> /camera/...

로봇이 회수되면 역터널이 사라지고, nginx 는 그 포트에 못 붙어 502 를 준다.
대시보드는 그걸 '카메라 고장' 으로 그린다. 이 프로그램이 **역터널이 끝나던
바로 그 포트에** 대신 앉는다. nginx 설정도, 프론트엔드도 고칠 것이 없다 —
포트 뒤에 무엇이 있는지는 nginx 가 묻지 않기 때문이다.

## 왜 stdlib 만 쓰나

fake_robot.py 와 같은 이유다. 로봇 하나 흉내내려고 requirements 를 늘리지
않는다. 프레임을 만드는 무거운 일(유튜브 내려받기·디코딩)은 설치 시점의
fetch_demo_frames.sh 가 ffmpeg 으로 끝내고, 상시로 도는 이 프로그램은
만들어진 JPEG 을 그대로 흘리기만 한다.

## 시간 기준을 공유한다

프레임 번호를 접속마다 따로 세지 않고 단조 시계에서 계산한다. 그래야 전방·후방
을 같이 띄우거나 여러 명이 동시에 봐도 같은 장면이 나온다. 접속마다 0 번부터
세면 두 창의 시간이 어긋나 한눈에 가짜인 게 드러난다.

## 사용법

    python3 tools/demo_stack/mock_camera.py --frames-dir tools/demo_stack/frames

    # 포트↔프레임 묶음을 직접 지정
    python3 tools/demo_stack/mock_camera.py \
        --frames-dir /var/lib/mingky-demo/frames \
        --bind 18801:pinky-01/front --bind 18802:pinky-01/rear
"""

from __future__ import annotations

import argparse
import http.server
import socket
import socketserver
import sys
import threading
import time
from pathlib import Path

# 역터널이 쓰던 포트와 그 뒤에 있던 카메라. deploy/cloud/nginx 의 location 과
# 짝이 맞아야 한다 — 여기를 바꾸면 nginx 도 같이 바꿔야 화면에 나온다.
DEFAULT_BINDS = [
    (18801, "pinky-01/front"),
    (18802, "pinky-01/rear"),
    (18803, "pinky-02/front"),
    (18804, "pinky-02/rear"),
]

BOUNDARY = "mingkyframe"

# 실기 스트림과 같은 체감을 준다. 더 올리면 대역만 먹고 눈에 띄는 차이가 없다.
DEFAULT_FPS = 12.0


class FrameLoop:
    """한 카메라의 프레임을 메모리에 물고 단조 시계로 돌린다."""

    def __init__(self, name: str, frames: list[bytes], fps: float):
        self.name = name
        self.frames = frames
        self.fps = fps

    @classmethod
    def load(cls, name: str, directory: Path, fps: float) -> "FrameLoop":
        files = sorted(directory.glob("*.jpg")) + sorted(directory.glob("*.jpeg"))
        if not files:
            raise FileNotFoundError(
                f"{directory} 에 JPEG 이 없다. "
                f"먼저 tools/demo_stack/fetch_demo_frames.sh 를 돌려라")
        frames = [path.read_bytes() for path in files]
        total = sum(len(frame) for frame in frames)
        print(f"[{name}] 프레임 {len(frames)}장 / {total / 1e6:.1f} MB "
              f"← {directory}", flush=True)
        return cls(name, frames, fps)

    def at(self, monotonic: float) -> bytes:
        return self.frames[int(monotonic * self.fps) % len(self.frames)]


class MjpegHandler(http.server.BaseHTTPRequestHandler):
    """nginx 가 prefix 를 떼고 넘기므로 여기 오는 경로는 /stream 이다."""

    loop: FrameLoop            # 서버가 클래스 속성으로 꽂아 준다
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:            # noqa: N802  (stdlib 규약)
        path = self.path.split("?")[0].rstrip("/")
        if path in ("/health", "/healthz"):
            self._text(200, "ok")
            return
        if path not in ("", "/stream", "/stream.mjpg"):
            self._text(404, "not found")
            return
        self._stream()

    def _text(self, status: int, body: str) -> None:
        payload = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _stream(self) -> None:
        self.send_response(200)
        self.send_header(
            "Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
        # 스트림은 캐시 대상이 아니다. 중간 프록시가 물면 화면이 한 장에 언다.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        # 무한 스트림에 keepalive 를 걸어 두면 nginx 가 다음 요청을 기다린다.
        self.send_header("Connection", "close")
        self.end_headers()

        interval = 1.0 / self.loop.fps
        try:
            while True:
                frame = self.loop.at(time.monotonic())
                self.wfile.write(
                    f"--{BOUNDARY}\r\n"
                    f"Content-Type: image/jpeg\r\n"
                    f"Content-Length: {len(frame)}\r\n\r\n".encode())
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
                time.sleep(interval)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            # 보는 사람이 창을 닫으면 여기로 온다. 정상 경로다.
            pass

    def log_message(self, fmt: str, *args) -> None:
        # 기본 구현은 접속마다 stderr 에 한 줄을 쓴다. 상시로 도는 데모에서는
        # 그게 journal 을 채우기만 한다. 스트림 시작만 남긴다.
        if "GET" in (fmt % args):
            print(f"[{self.loop.name}] {self.address_string()} 접속", flush=True)


class ReusableServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    # 재시작할 때 TIME_WAIT 로 남은 소켓 때문에 바인드가 실패하지 않게 한다.
    allow_reuse_address = True
    address_family = socket.AF_INET


def serve(port: int, loop: FrameLoop) -> threading.Thread:
    handler = type(f"Handler{port}", (MjpegHandler,), {"loop": loop})
    # 127.0.0.1 로만 연다. 역터널이 그랬듯 이 포트는 nginx 만 부르면 되고,
    # 밖에서 직접 닿을 이유가 없다.
    server = ReusableServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"[{loop.name}] 127.0.0.1:{port} 대기", flush=True)
    return thread


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--frames-dir", required=True,
                        help="카메라별 하위 디렉터리를 담은 뿌리")
    parser.add_argument("--bind", action="append", default=None,
                        metavar="PORT:SUBDIR",
                        help="포트와 프레임 하위 경로. 반복 지정한다")
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS)
    args = parser.parse_args(argv)

    if args.bind:
        binds = []
        for entry in args.bind:
            port, _, subdir = entry.partition(":")
            if not subdir:
                print(f"--bind 형식은 PORT:SUBDIR 이다: {entry}", file=sys.stderr)
                return 2
            binds.append((int(port), subdir))
    else:
        binds = DEFAULT_BINDS

    root = Path(args.frames_dir)
    try:
        loops = {port: FrameLoop.load(subdir, root / subdir, args.fps)
                 for port, subdir in binds}
    except FileNotFoundError as exc:
        print(f"[프레임 없음] {exc}", file=sys.stderr)
        return 1

    for port, loop in loops.items():
        serve(port, loop)

    print(f"[가짜 카메라] {len(loops)}대 가동 — {args.fps:g} fps", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("[가짜 카메라] 종료", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
