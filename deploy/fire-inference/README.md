# 화재 추론 서버 설치

ROS 2와 Nav2는 Pinky에서 실행하고, YOLO 추론만 GPU 컴퓨터에서 상시 실행한다.

```bash
cd deploy/fire-inference
sudo ./install.sh /absolute/path/to/fire-model.pt
curl http://127.0.0.1:5000/health
```

서비스 확인과 로그:

```bash
sudo systemctl status mingky-fire-inference
sudo journalctl -u mingky-fire-inference -f
```

모델을 교체할 때 같은 설치 명령을 다시 실행하면 파일과 서비스가 갱신된다.
방화벽에서는 Pinky가 있는 내부망에서 TCP 5000번에 접근할 수 있어야 한다.

Pinky에는 GPU 컴퓨터의 내부망 주소를 등록한다.

```bash
cd deploy/robot
sudo ./install.sh pinky-01 http://<GPU-PC-IP>:5000/infer
sudo systemctl restart mingky-system
```
