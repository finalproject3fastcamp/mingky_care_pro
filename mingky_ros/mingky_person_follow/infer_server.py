"""핑키 후방 카메라용 손님(인형) 검출 추론 서버.

mingky_fire_evac 의 infer_server.py 와 같은 구조 (ROS2 아님, 평범한 Flask).
화재 감지와 다른 점: "있다/없다"가 아니라 검출된 각 박스의 위치·크기·클래스를
전부 돌려준다 -- person_follow_node 가 "직전에 잠갔던 것과 같은 클래스인지"
판단하려면(target_lock.py 참고) 클래스 이름과 박스 좌표가 그대로 필요하다.

## color 필드

YOLO 클래스 라벨은 모델이 잘못 판단하면(각도·조명에 따라 인형 세 종류를
헷갈리는 경우) 그대로 틀린 값을 준다 -- target_lock.py 는 이 라벨을
그대로 믿고 같은 클래스인지만 비교하기 때문에, 라벨 자체가 틀리면 걸러낼
방법이 없었다. 그래서 각 박스의 평균 RGB 색상(`color`)도 같이 돌려준다.
인형 세 종류(분홍 돼지/회백색 펭귄/노란 병아리)가 색이 뚜렷이 달라서,
robot 쪽에서 "직전 프레임과 색이 갑자기 크게 달라지면 다른 대상"으로
보고 걸러낼 수 있다 (`target_lock.py`의 `max_color_distance` 참고).
"""

import argparse
import io

from flask import Flask, jsonify, request
import numpy as np
from PIL import Image
from ultralytics import YOLO

app = Flask(__name__)
model = None


def _mean_color(image: Image.Image, xyxy) -> list[float]:
    """박스 영역의 평균 RGB. 대상이 바뀌었는지 검증하는 색상 지문으로 쓴다."""
    x1, y1, x2, y2 = (max(0, int(v)) for v in xyxy)
    crop = image.crop((x1, y1, x2, y2))
    if crop.width == 0 or crop.height == 0:
        return [0.0, 0.0, 0.0]
    arr = np.asarray(crop, dtype=np.float32)
    return [float(arr[..., channel].mean()) for channel in range(3)]


@app.post('/infer')
def infer():
    if 'image' not in request.files:
        return jsonify({'error': 'image 파일 파트가 없습니다'}), 400
    conf = float(request.form.get('conf', 0.4))
    image_bytes = request.files['image'].read()
    image = Image.open(io.BytesIO(image_bytes)).convert('RGB')
    results = model.predict(image, conf=conf, verbose=False)
    boxes = results[0].boxes
    names = results[0].names
    detections = []
    for b in boxes:
        x1, y1, x2, y2 = [float(v) for v in b.xyxy[0]]
        detections.append({
            'class': names[int(b.cls[0])],
            'conf': float(b.conf[0]),
            # 중심좌표+크기로 준다 (위치 잠금 계산에 바로 쓰기 편한 형태).
            'x': (x1 + x2) / 2,
            'y': (y1 + y2) / 2,
            'w': x2 - x1,
            'h': y2 - y1,
            'color': _mean_color(image, b.xyxy[0]),
        })
    return jsonify({
        'detections': detections,
        'image_width': image.width,
        'image_height': image.height,
    })


@app.get('/health')
def health():
    return jsonify({'status': 'ok', 'device': str(model.device)})


def main():
    global model
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    parser.add_argument('--port', type=int, default=5001)
    parser.add_argument('--host', default='0.0.0.0')
    args = parser.parse_args()

    print(f'모델 로딩 중: {args.model}')
    model = YOLO(args.model)
    if __import__('torch').cuda.is_available():
        model.to('cuda:0')
    print(f'로딩 완료 (device={model.device}, classes={model.names})')

    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == '__main__':
    main()
