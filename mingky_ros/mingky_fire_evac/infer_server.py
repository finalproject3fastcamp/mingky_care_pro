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
import io
import threading

from flask import Flask, jsonify, request
import numpy as np
from PIL import Image
from ultralytics import YOLO
from waitress import serve

app = Flask(__name__)
model = None
fire_class_id = None
inference_lock = threading.Lock()

# 전등/조명 오탐 방지용 색상 검증 (시연 요건: 라이터 불꽃 외엔 절대 안 걸려야 함).
# YOLO가 fire 박스를 잡아도, 박스 영역이 실제 불꽃 색(채도 높은 주황~빨강)이
# 아니면 버린다 -- 전등은 밝지만(명도 V 높음) 채도(S)는 낮은 흰색/미색이라 이
# 조건으로 걸러진다. PIL 'HSV' 모드는 H/S/V 모두 0-255 스케일이고, H=0이
# 빨강이라 0-360도 기준 0~45도(주황~노랑 경계) 구간은 대략 0-32에 해당한다.
FIRE_HUE_MAX = 22
FIRE_MIN_SATURATION = 110
FIRE_MIN_VALUE = 140
FIRE_PIXEL_RATIO = 0.12


def _looks_like_flame(image: Image.Image, xyxy) -> bool:
    x1, y1, x2, y2 = (max(0, int(v)) for v in xyxy)
    crop = image.crop((x1, y1, x2, y2))
    if crop.width == 0 or crop.height == 0:
        return False
    hsv = np.asarray(crop.convert('HSV'), dtype=np.int16)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    flame_pixels = (
        (h <= FIRE_HUE_MAX)
        & (s >= FIRE_MIN_SATURATION)
        & (v >= FIRE_MIN_VALUE)
    )
    return float(np.mean(flame_pixels)) >= FIRE_PIXEL_RATIO


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
    confirmed = [b for b in boxes if _looks_like_flame(image, b.xyxy[0])]
    return jsonify({
        'fire': len(confirmed) > 0,
        'detections': [
            {'conf': float(b.conf)} for b in confirmed
        ],
        # YOLO는 잡았지만 색상 검증에서 걸러진 개수 -- 현장에서 임계값
        # 튜닝할 때 (raw > 0 인데 fire가 계속 false면 조명이 필터에 안
        # 걸리는 것) 참고용.
        'raw_detections': len(boxes),
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
