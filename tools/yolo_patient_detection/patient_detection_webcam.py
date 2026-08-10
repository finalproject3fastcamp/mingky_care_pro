import os

from dotenv import load_dotenv
load_dotenv()

from roboflow import Roboflow
import cv2

API_KEY = os.environ.get("ROBOFLOW_API_KEY")
if not API_KEY:
    raise RuntimeError("환경변수 ROBOFLOW_API_KEY가 설정되지 않았습니다. (.env.example 참고)")

WORKSPACE = "s-workspace-ljele"
PROJECT = "hospital-robot"
VERSION = 2

print("Roboflow 모델 로딩 중...")
rf = Roboflow(api_key=API_KEY)
project = rf.workspace(WORKSPACE).project(PROJECT)
model = project.version(VERSION).models()[0]

import sys

PINKY_HOSTS = {
    "pinky1": "192.168.129.24",
    "pinky2": "192.168.129.26",
}

pinky_name = sys.argv[1] if len(sys.argv) > 1 else "pinky2"
if pinky_name not in PINKY_HOSTS:
    raise RuntimeError(f"알 수 없는 로봇 이름: {pinky_name} (사용 가능: {list(PINKY_HOSTS)})")

PINKY_STREAM_URL = f"http://{PINKY_HOSTS[pinky_name]}:8080/video_feed"
cap = cv2.VideoCapture(PINKY_STREAM_URL)
if not cap.isOpened():
    raise RuntimeError(f"핑키 웹캠 스트림({PINKY_STREAM_URL})을 열 수 없습니다. 핑키 쪽 camera_stream.py가 실행 중인지, 같은 네트워크인지 확인하세요.")

import threading

latest_frame = None
last_predictions = []
frame_lock = threading.Lock()
pred_lock = threading.Lock()
stop_flag = False

def predict_worker():
    global last_predictions
    while not stop_flag:
        with frame_lock:
            frame_to_send = latest_frame.copy() if latest_frame is not None else None
        if frame_to_send is None:
            continue
        rgb_frame = cv2.cvtColor(frame_to_send, cv2.COLOR_BGR2RGB)
        try:
            result = model.predict(rgb_frame, confidence=40, overlap=30).json()
            with pred_lock:
                last_predictions = result["predictions"]
        except Exception as e:
            print(f"인식 요청 실패: {e}")

worker = threading.Thread(target=predict_worker, daemon=True)
worker.start()

print("웹캠 실시간 테스트 시작... (종료: q 키)")
print("※ 인식은 백그라운드 스레드에서 처리되어 영상은 끊기지 않습니다.")

while True:
    ret, frame = cap.read()
    if not ret:
        print("프레임을 읽을 수 없습니다.")
        break

    with frame_lock:
        latest_frame = frame.copy()

    with pred_lock:
        predictions = last_predictions

    for pred in predictions:
        x, y, w, h = pred["x"], pred["y"], pred["width"], pred["height"]
        x1, y1 = int(x - w / 2), int(y - h / 2)
        x2, y2 = int(x + w / 2), int(y + h / 2)
        label = f'{pred["class"]} {pred["confidence"]:.2f}'

        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(frame, label, (x1, max(y1 - 10, 0)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

    cv2.imshow("Roboflow Webcam Test", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        stop_flag = True
        break

cap.release()
cv2.destroyAllWindows()
print("✅ 종료")