"""핑키 후방 카메라용 손님(인형) 검출 추론 서버.

mingky_fire_evac 의 infer_server.py 와 같은 구조 (ROS2 아님, 평범한 Flask).
화재 감지와 다른 점: "있다/없다"가 아니라 검출된 각 박스의 위치·크기·클래스를
전부 돌려준다 -- person_follow_node 가 "직전에 잠갔던 것과 같은 클래스인지"
판단하려면(target_lock.py 참고) 클래스 이름과 박스 좌표가 그대로 필요하다.
"""

import argparse
import io

from flask import Flask, jsonify, request
from PIL import Image
from ultralytics import YOLO

app = Flask(__name__)
model = None


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
