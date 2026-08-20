"""포장 중 작업 영역에 사람이 들어오면 팔을 세우는 감시자 (FC-14).

pack_run.py 의 30fps 제어 루프 안에서 호출된다. 별도 프로세스로 만들지 않는
이유는 카메라 때문이다 — top/wrist 는 V4L2 로 러너가 독점하고 있어서, 두 번째
프로세스는 같은 카메라를 열 수 없다. 루프가 이미 매 프레임 쥐고 있는 관측
이미지를 그대로 검사하는 것이 배선도 지연도 가장 짧다.

검출은 팀의 기존 방식 그대로 HTTP 추론 서버에 묻는다. person_follow ·
fire_evac 과 같은 `infer_server.py` 를 COCO 가중치로 한 대 더 띄우면 된다
(새 서버 코드가 없다):

    python mingky_ros/mingky_person_follow/infer_server.py \
        --model yolov8n.pt --port 5003

## 판정 규칙

- **N프레임마다 한 번**만 검사한다. 30fps 제어 주기를 흔들지 않기 위해서다.
- `person` 이 기준 확신 이상으로 **연속 2회** 보여야 세운다. 한 프레임의
  오검출로 시연 중인 팔이 서 버리면, 그 다음부터 아무도 이 기능을 켜지
  않는다. 연속 2회면 검사 주기 기준 0.2~0.3초 안에 선다.
- 서버가 죽어 있으면 **경고를 남기고 계속 간다** (fail-open). 시연에서 감시
  서버 하나 때문에 조제 전체가 서는 것을 막기 위한 선택이다. 반대가 필요한
  자리(무인 운영)는 `required=True` 로 바꾸면 연결이 끊긴 순간 세운다.

여기는 판정만 있다. 이미지 인코딩(cv2)과 HTTP(requests)는 호출부가 주입할
수 있게 열어 둬서, 로봇도 GPU 도 없는 노트북에서 단위 테스트가 돈다.
"""

from __future__ import annotations

from typing import Any, Callable


class SafetyWatch:
    """관측 이미지에서 사람을 찾고, 연속으로 보이면 정지를 명한다."""

    def __init__(
        self,
        server_url: str,
        *,
        every: int = 3,
        conf: float = 0.35,
        hits_needed: int = 2,
        classes: tuple[str, ...] = ('person',),
        timeout_sec: float = 0.4,
        required: bool = False,
        post: Callable[[str, bytes, float], list[dict]] | None = None,
        encode: Callable[[Any], bytes] | None = None,
        warn: Callable[[str], None] | None = None,
    ) -> None:
        self.server_url = server_url
        self.every = max(1, int(every))
        self.conf = float(conf)
        self.hits_needed = max(1, int(hits_needed))
        self.classes = tuple(classes)
        self.timeout_sec = float(timeout_sec)
        self.required = bool(required)
        self._post = post or _http_post
        self._encode = encode or _jpeg_encode
        self._warn = warn or (lambda msg: None)

        self._hits = 0
        self._fail_streak = 0
        self._warned_down = False

    # ------------------------------------------------------------------

    def check(self, observation: dict[str, Any], frame_no: int) -> dict | None:
        """정지해야 하면 사유 dict, 아니면 None.

        `observation` 은 robot.get_observation() 그대로다. 이미지(3차원
        배열)만 골라 쓰고 관절값은 무시하므로, 카메라 이름을 여기서 몰라도
        된다 — top 이든 wrist 든 사람이 보이면 선다.
        """
        if frame_no % self.every != 0:
            return None

        frames = {
            name: value for name, value in observation.items()
            if hasattr(value, 'ndim') and getattr(value, 'ndim', 0) == 3
        }
        if not frames:
            return self._server_trouble('관측에 카메라 이미지가 없습니다')

        seen: dict | None = None
        for name, image in frames.items():
            try:
                detections = self._post(
                    self.server_url, self._encode(image), self.timeout_sec)
            except Exception as e:  # noqa: BLE001 — 어떤 실패든 같은 취급
                return self._server_trouble(f'{type(e).__name__}: {e}')
            for det in detections:
                if (det.get('class') in self.classes
                        and float(det.get('conf', 0.0)) >= self.conf):
                    if seen is None or float(det['conf']) > seen['확신']:
                        seen = {'카메라': name, '확신': float(det['conf'])}

        # 서버가 응답했다 — 연결 경고 상태를 푼다.
        self._fail_streak = 0
        if self._warned_down:
            self._warned_down = False
            self._warn('안전 감시 서버가 다시 응답합니다')

        if seen is None:
            self._hits = 0          # 연속이 끊겼다. 처음부터 다시 센다.
            return None
        self._hits += 1
        if self._hits < self.hits_needed:
            return None
        return {'이유': '작업 영역에서 사람이 감지되었습니다', **seen}

    # ------------------------------------------------------------------

    def _server_trouble(self, detail: str) -> dict | None:
        """검사를 못 한 경우. 세울지는 required 가 정한다."""
        self._fail_streak += 1
        self._hits = 0              # 못 본 프레임을 '본 것' 으로 잇지 않는다
        if self.required:
            return {'이유': f'안전 감시를 할 수 없습니다 — {detail}'}
        if not self._warned_down and self._fail_streak >= 2:
            self._warned_down = True
            self._warn(f'안전 감시 서버 연결 실패 — 감시 없이 계속합니다 ({detail})')
        return None


# ── 기본 구현 (il venv 전용 — 테스트는 주입으로 대체) ──────────────────

def _jpeg_encode(image: Any) -> bytes:
    import cv2  # noqa: PLC0415 — 무거운 import 를 쓰는 순간까지 미룬다
    ok, buf = cv2.imencode('.jpg', image[:, :, ::-1])  # RGB → BGR
    if not ok:
        raise RuntimeError('JPEG 인코딩 실패')
    return bytes(buf)


def _http_post(url: str, jpeg: bytes, timeout_sec: float) -> list[dict]:
    import requests  # noqa: PLC0415
    resp = requests.post(
        url, files={'image': ('frame.jpg', jpeg, 'image/jpeg')},
        timeout=timeout_sec)
    resp.raise_for_status()
    return resp.json().get('detections', [])
