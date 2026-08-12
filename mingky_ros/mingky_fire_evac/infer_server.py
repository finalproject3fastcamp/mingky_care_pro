"""AI 노트북(GPU)에서 돌리는 화재 감지 추론 서버.

ROS2 노드가 아니다 -- 평범한 Flask 앱. fire_evac_node(핑키 위에서 도는
ROS2 노드)가 JPEG 프레임을 POST 하면, YOLO(fire 클래스만)로 판단해서
{"fire": true/false} 만 돌려준다.

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

from flask import Flask, jsonify, request
from PIL import Image
from ultralytics import YOLO

app = Flask(__name__)
model = None
fire_class_id = None


@app.post("/infer")
def infer():
    if "image" not in request.files:
        return jsonify({"error": "image 파일 파트가 없습니다"}), 400
    conf = float(request.form.get("conf", 0.3))
    image_bytes = request.files["image"].read()
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    results = model.predict(
        image, conf=conf, classes=[fire_class_id], verbose=False)
    boxes = results[0].boxes
    return jsonify({
        "fire": len(boxes) > 0,
        "detections": [
            {"conf": float(b.conf)} for b in boxes
        ],
    })


@app.get("/health")
def health():
    return jsonify({"status": "ok", "device": str(model.device)})


def main():
    global model, fire_class_id
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="다운받은 .pt 가중치 경로")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    print(f"모델 로딩 중: {args.model}")
    model = YOLO(args.model)
    fire_class_id = next(i for i, name in model.names.items() if name == "fire")
    print(f"로딩 완료 (fire 클래스 id={fire_class_id}, device={model.device})")

    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
