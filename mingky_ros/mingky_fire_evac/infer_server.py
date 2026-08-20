"""AI 노트북(GPU)에서 돌리는 화재 감지 추론 서버.

ROS2 노드가 아니다 -- 평범한 Flask 앱. fire_evac_node(핑키 위에서 도는
ROS2 노드)가 JPEG 프레임을 POST 하면, YOLO(fire 클래스만)로 판단해서
{'fire': true/false} 만 돌려준다.

## 왜 ROS2로 안 하고 이렇게 HTTP로 분리했는가

원래는 이 추론 자체를 ROS2 노드로 만들어서 핑키와 DDS로 통신하려고 했다.
그런데 실제 와이파이(FASTCAMPUS 공용망)에서 핑키 ↔ AI 노트북 사이에
순수 UDP 유니캐스트조차 안 갔다(직접 테스트: 둘 다 응답 없음) -- ROS2
기본 디스커버리가 UDP 라서 이 네트워크에서는 원천적으로 안 됐다. 반면
TCP(SSH, HTTP)는 같은 두 기기 사이에서 문제없이 통과했다. 그래서 아예
ROS2/DDS를 두 기기 사이에 걸치지 않고, 이미 확인된 TCP(HTTP) 하나로만
건너가게 바꿨다.

## 실행

    source ~/fire_evac_venv/bin/activate
    python3 infer_server.py --model ~/fire_evac_ws/optimized150.pt --port 5000
"""

import argparse
import collections
import io
import threading
import time

from flask import Flask, jsonify, request
import numpy as np
from PIL import Image
from ultralytics import YOLO
from waitress import serve

app = Flask(__name__)
model = None
fire_class_id = None
inference_lock = threading.Lock()

# 전등/조명 오탐 방지 (시연 요건: 라이터 불꽃 외엔 절대 안 걸려야 함).
#
# 처음엔 "색상만" 검증했다(채도 높은 주황~빨강만 통과) -- 그런데 실기
# 검증(2026-08-20, 실제 라이터 영상 20프레임 중 YOLO raw 5, 색상 필터 통과
# 1)에서 드러났듯, **라이터의 노란 불꽃과 백열등류의 따뜻한 노란빛은 HSV
# 색공간에서 사실상 겹친다** -- Hue/Saturation을 아무리 조정해도 색상만으론
# 이 둘을 원리적으로 못 가른다 (합성 색상으로 재확인함: (255,200,30) 노란
# 불꽃과 (255,180,80) 백열등은 어떤 채도/색상 임계값에서도 항상 같이
# 통과되거나 같이 걸러졌다).
#
# 그래서 색상은 "명백히 흰색/저채도인 조명"만 걸러내는 느슨한 1차 필터로만
# 쓰고, 진짜 구분은 **깜박임(flicker)**으로 한다 -- 조명은 밝기가 일정하고,
# 실제 불꽃은 짧은 시간에도 눈에 띄게 흔들린다. 최근 FLICKER_WINDOW_SEC
# 동안의 박스 밝기(V) 표준편차가 FLICKER_MIN_STD 이상이어야 최종 확정한다.
#
# 시연 로봇 1대만 쓰는 걸 가정해 밝기 이력을 전역(글로벌)으로 하나만 든다 --
# 로봇을 여러 대 동시에 쓰면 서로 다른 화재원이 섞여 부정확해질 수 있다.
FLAME_LOOSE_MIN_SATURATION = 40
FLICKER_WINDOW_SEC = 3.0
FLICKER_RESET_GAP_SEC = 2.0
FLICKER_MIN_SAMPLES = 4
FLICKER_MIN_STD = 6.0

_brightness_lock = threading.Lock()
_brightness_history: collections.deque = collections.deque()  # [(ts, mean_v), ...]


def _mean_hsv(image: Image.Image, xyxy) -> tuple[float, float, float] | None:
    x1, y1, x2, y2 = (max(0, int(v)) for v in xyxy)
    crop = image.crop((x1, y1, x2, y2))
    if crop.width == 0 or crop.height == 0:
        return None
    hsv = np.asarray(crop.convert('HSV'), dtype=np.float32)
    return (
        float(hsv[..., 0].mean()),
        float(hsv[..., 1].mean()),
        float(hsv[..., 2].mean()),
    )


def _flicker_std(mean_value: float) -> float:
    """최근 FLICKER_WINDOW_SEC 동안의 밝기 표준편차. 표본이 모자라면 0."""
    now = time.monotonic()
    with _brightness_lock:
        while (_brightness_history
               and now - _brightness_history[0][0] > FLICKER_WINDOW_SEC):
            _brightness_history.popleft()
        if (_brightness_history
                and now - _brightness_history[-1][0] > FLICKER_RESET_GAP_SEC):
            # 한참 끊겼다가 다시 잡힌 것 -- 이전 이력과 섞으면 무의미하다.
            _brightness_history.clear()
        _brightness_history.append((now, mean_value))
        samples = [v for _, v in _brightness_history]
    if len(samples) < FLICKER_MIN_SAMPLES:
        return 0.0
    return float(np.std(samples))


@app.post('/infer')
def infer():
    if 'image' not in request.files:
        return jsonify({'error': 'image 파일 파트가 없습니다'}), 400
    try:
        conf = float(request.form.get('conf', 0.3))
        if not 0.0 < conf <= 1.0:
            raise ValueError
    except ValueError:
        return jsonify({'error': 'conf는 0 초과 1 이하 숫자여야 합니다'}), 400
    try:
        image_bytes = request.files['image'].read()
        image = Image.open(io.BytesIO(image_bytes)).convert('RGB')
    except (OSError, ValueError):
        return jsonify({'error': '올바른 이미지가 아닙니다'}), 400
    # 두 Pinky 요청이 동시에 와도 같은 YOLO/GPU 객체를 병렬 호출하지 않는다.
    with inference_lock:
        results = model.predict(
            image, conf=conf, classes=[fire_class_id], verbose=False)
    boxes = results[0].boxes

    confirmed = []
    diagnostics = []
    for b in boxes:
        mean_hsv = _mean_hsv(image, b.xyxy[0])
        if mean_hsv is None:
            continue
        hue, saturation, value = mean_hsv
        if saturation < FLAME_LOOSE_MIN_SATURATION:
            # 명백히 흰색/저채도인 조명 -- 깜박임 이력에 넣지 않고 바로 컷.
            continue
        std = _flicker_std(value)
        diagnostics.append({
            'conf': float(b.conf), 'hue': hue, 'saturation': saturation,
            'value': value, 'flicker_std': std,
        })
        if std >= FLICKER_MIN_STD:
            confirmed.append(b)

    return jsonify({
        'fire': len(confirmed) > 0,
        'detections': [
            {'conf': float(b.conf)} for b in confirmed
        ],
        # YOLO 원본 검출 개수 -- 현장 튜닝용.
        'raw_detections': len(boxes),
        # 색상 1차 필터는 통과했지만 깜박임 확정 전인 것까지 포함한 진단
        # 정보. flicker_std가 FLICKER_MIN_STD 근처인데 계속 fire=false면
        # FLICKER_MIN_STD를 낮추면 된다.
        'diagnostics': diagnostics,
    })


@app.get('/health')
def health():
    return jsonify({'status': 'ok', 'device': str(model.device)})


def main():
    global model, fire_class_id
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True, help='다운받은 .pt 가중치 경로')
    parser.add_argument('--port', type=int, default=5000)
    parser.add_argument('--host', default='0.0.0.0')
    args = parser.parse_args()

    print(f'모델 로딩 중: {args.model}')
    model = YOLO(args.model)
    fire_class_id = next(i for i, name in model.names.items() if name == 'fire')
    print(f'로딩 완료 (fire 클래스 id={fire_class_id}, device={model.device})')

    serve(app, host=args.host, port=args.port, threads=4)


if __name__ == '__main__':
    main()
