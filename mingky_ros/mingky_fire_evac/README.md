# mingky_fire_evac

전방 카메라에 불(fire)이 잡히면 기존 안내를 안전하게 종료하고 로봇을 대피
지점으로 자동 이동시키는 운영 기능이다. YOLO 추론은 별도 GPU 컴퓨터가 담당한다.

## 왜 로봇 위 노드 + 별도 HTTP 추론 서버로 나뉘어 있는가

핑키(Raspberry Pi)에는 GPU가 없어서 YOLO를 실시간으로 못 돌린다(실측: CPU 1.19s/frame vs GPU 25.9ms/frame).
그래서 추론만 GPU 있는 컴퓨터에 맡기는데, **이 프로젝트가 쓰는 와이파이(FASTCAMPUS 공용망)가 기기 간 UDP를
막아놔서 ROS2(DDS)로 두 기기를 직접 못 붙인다** — 순수 UDP 유니캐스트 테스트가 양쪽 다 타임아웃, TCP(SSH·HTTP)는
정상 통과하는 것까지 직접 확인했다.

그래서 구조를 이렇게 나눴다:

```
[핑키]  fire_evac_node (ROS2)                    [GPU 컴퓨터]  infer_server.py (Flask, ROS2 아님)
  ├─ 카메라 구독 (로컬, /front_camera/...)              │
  ├─ Nav2 명령 (로컬)                                   │
  └─ JPEG 프레임 ──── HTTP POST /infer ──────────────→  YOLO 추론 (GPU)
     {"fire": true/false} ←──────────────────────────  └─ 응답만 돌려줌
```

두 기기 사이엔 **HTTP(TCP) 하나만** 걸치기 때문에 와이파이의 UDP 차단과 완전히 무관하다.

**이 패턴은 화재 감지 전용이 아니다.** 이 와이파이 제약(로봇↔GPU컴퓨터 사이 ROS2 불가)이 바뀌지 않는 한,
앞으로 다른 YOLO/딥러닝 작업(예: 환자 인형 검출)도 같은 구조(로봇=ROS2 노드, GPU컴퓨터=평범한 HTTP 서버)를
그대로 재사용하면 된다 — `infer_server.py`는 모델 파일과 클래스 이름만 바꾸면 다른 감지 작업에도 그대로 쓸 수
있는 범용 템플릿이다.

## 실행 순서

### 1) GPU 있는 컴퓨터에서 추론 서버 띄우기

```bash
python3 -m venv fire_evac_venv
source fire_evac_venv/bin/activate
pip install ultralytics flask pillow waitress

python3 infer_server.py --model <가중치.pt 경로> --port 5000
```

떠 있는지 확인:

```bash
curl http://<이 컴퓨터의 IP>:5000/health
# {"device":"cuda:0","status":"ok"}
```

- `--model` : `fire` 클래스를 포함한 YOLO 가중치. `model.names`에서 `"fire"` 이름의 클래스를 자동으로 찾아서
  그 클래스만 필터링한다 — 이름이 다르면 `infer_server.py`의 `fire_class_id` 탐색 부분을 고쳐야 한다.
- CPU만 있어도 동작은 하지만 프레임당 1초 이상 걸려서 사실상 실시간 감지가 안 된다. GPU 필수.

### 2) 운영 설치

GPU 컴퓨터에는 저장소의 설치기를 실행한다.

```bash
cd deploy/fire-inference
sudo ./install.sh /path/to/fire-model.pt
curl http://127.0.0.1:5000/health
```

각 Pinky에서는 추론 서버 주소를 두 번째 인자로 주고 로봇 설치기를 다시 실행한다.

```bash
cd deploy/robot
sudo ./install.sh pinky-01 http://<GPU-PC-IP>:5000/infer
sudo systemctl restart mingky-system
```

설정은 `/etc/mingky/robot.env`에 보존되며 재부팅 후에도
`mingky-system.service`가 화재 감지 노드를 자동 실행한다.

### 3) 수동 실행

통합 서비스와 별개로 노드만 점검할 때 사용한다:

```bash
ros2 run mingky_fire_evac fire_evac_node --ros-args \
  -p infer_server_url:=http://<1번 컴퓨터 IP>:5000/infer
```

`infer_server_url`은 기본값이 없는 필수 파라미터라, 안 주면 노드가 바로 에러를 내고 죽는다.

## 파라미터 (`fire_evac_node`)

| 이름 | 기본값 | 설명 |
| --- | --- | --- |
| `robot_id` | `pinky-01` | 로봇 ID |
| `image_topic` | `/front_camera/image_raw/compressed` | 구독할 전방 카메라 압축 이미지 토픽 (`qr_reader_node` 발행) |
| `frame_max_age_sec` | `2.0` | 이보다 오래된 프레임은 감지에 안 씀 (카메라/네트워크 끊김 방지) |
| `infer_server_url` | *(필수)* | 추론 서버 주소, 예: `http://192.168.129.41:5000/infer` |
| `infer_timeout_sec` | `2.0` | 추론 요청 타임아웃. 실패해도 노드는 안 죽고 그 프레임만 미감지 처리 |
| `conf_threshold` | `0.3` | YOLO confidence 임계값 |
| `window_size` | `7` | 최근 몇 프레임을 볼지 |
| `required_detections` | `5` | 최근 `window_size`장 중 몇 장 이상 fire여야 확정할지 (순간 오탐·단일 미검출 필터) |
| `shelter_x` / `shelter_y` / `shelter_yaw` | 실측값 | 대피 목표 좌표 (map 프레임). 대피소가 바뀌면 이 셋만 수정 |

## 동작

1. `qr_reader_node`가 이미 JPEG로 압축해서 발행한 프레임을 그대로 HTTP로 전달 (디코드/재인코드 없음).
2. 최근 `window_size`프레임 중 `required_detections`프레임 이상 fire 판정 → 확정.
3. 확정되면 `/navigate_to_pose/_action/cancel_goal`로 진행 중이던 목표(누가 보냈든 상관없이)를 강제 취소하고,
   자체 `NavigateToPose` 액션 클라이언트로 대피 좌표로 이동.
4. 이동 중엔 `/fire_evac/active` (latched Bool)를 `true`로 발행 — `mingky_lcd_status`가 이걸 구독해서 긴급
   안내 화면으로 강제 전환한다.
5. 도착(또는 실패) 시 `/fire_evac/active`를 다시 `false`로 바꾸되 화재 경보는 유지한다.
6. 운영자가 현장 안전을 확인하고 `fire_evac/reset_alarm`을 호출해야 새 화재를 감지한다.

## 테스트

카메라 앞에 실제로 불을 갖다 대지 않고 Nav2 이동 로직만 검증하고 싶을 때:

```bash
ros2 service call fire_evac/trigger_test std_srvs/srv/Trigger
```

감지 없이 바로 대피 이동을 시작한다.

대피가 끝난 뒤 현장 안전을 확인하고 경보를 초기화한다:

```bash
ros2 service call fire_evac/reset_alarm std_srvs/srv/Trigger
```

## 검증 이력

- 실기(pinky-02), 2026-08-12~13: 실제 라이터 불꽃으로 감지 → 대피소 도착 2회 성공.
- `fire` 클래스만 사용 (`smoke`는 오탐과 잘 안 갈라져서 제외 — 검증 데이터 기준 `fire`는 오탐 0건,
  `smoke`는 노을·조명 등에서 비슷한 신뢰도로 오탐 발생).

## 운영 확인

```bash
# GPU 컴퓨터
systemctl status mingky-fire-inference
curl http://127.0.0.1:5000/health

# Pinky
systemctl status mingky-system
ros2 node list | grep fire_evac
```

관제 이벤트에는 화재 감지, 대피 시작·성공·실패와 추론 서버 연결 상실·복구가
기록된다. 비상정지가 걸려 있으면 이를 임의로 해제하지 않고 자동 대피를 거부한다.
