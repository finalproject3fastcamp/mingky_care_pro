# mingky_fire_evac

전방 카메라에 불(fire)이 잡히면 로봇을 대피 지점으로 자동 이동시키는 실험 단계 기능.

> 아직 관제 대시보드 이벤트(`event_codes.yaml`)는 연동 안 했다. 로그로만 확인 가능.

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
pip install ultralytics flask pillow

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

### 2) 핑키에서 fire_evac_node 실행

`mingky_system.launch.xml`에는 **포함돼 있지 않다** — 아직 실험 단계라 상시 기동 대상이 아니다. 필요할 때
수동으로 띄운다:

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
5. 도착(또는 실패) 시 `/fire_evac/active`를 다시 `false`로.

## 테스트

카메라 앞에 실제로 불을 갖다 대지 않고 Nav2 이동 로직만 검증하고 싶을 때:

```bash
ros2 service call fire_evac/trigger_test std_srvs/srv/Trigger
```

감지 없이 바로 대피 이동을 시작한다.

## 검증 이력

- 실기(pinky-02), 2026-08-12~13: 실제 라이터 불꽃으로 감지 → 대피소 도착 2회 성공.
- `fire` 클래스만 사용 (`smoke`는 오탐과 잘 안 갈라져서 제외 — 검증 데이터 기준 `fire`는 오탐 0건,
  `smoke`는 노을·조명 등에서 비슷한 신뢰도로 오탐 발생).

## 남은 일

- 관제 대시보드 이벤트(`event_codes.yaml`) 연동 안 됨.
- `mingky_system.launch.xml`에 포함 안 됨 — 상시 기동하려면 launch 인자로 `infer_server_url`을 받도록 추가
  필요.
- 추론 서버가 현재 특정 개인 컴퓨터에서만 수동으로 실행되는 상태 — 상시 운영하려면 GPU 컴퓨터에 서비스로
  등록하는 게 필요.
