# mingky_person_follow

후방 카메라로 안내받는 손님(인형)이 잘 따라오고 있는지 보고, Nav2 주행
속도를 조절하는 기능이다. 조향/목적지는 전혀 건드리지 않는다 -- 손님이
보이면 정상 속도로 계속 가고, 안 보이면 제자리에서 기다린다.

## 왜 조향이 아니라 속도만 건드리는가

병원 안내로봇은 이미 검증된 경로(Nav2 웨이포인트)를 타야 한다. 손님 위치를
따라 로봇이 스스로 진로를 바꾸면 오히려 정해진 동선을 벗어난다. 그래서 이
노드는 손님이 "따라오고 있는지"만 확인하고, 못 따라오고 있으면
`/speed_limit` (nav2_msgs/SpeedLimit, `velocity_smoother`가 구독)에 낮은
값을 걸어 로봇을 세운다. Nav2 goal 자체는 안 건드리므로 손님이 다시
나타나면 원래 경로를 그대로 이어서 간다.

## 구조 (mingky_fire_evac 과 동일한 이유)

핑키(Raspberry Pi)에는 GPU가 없고, 이 프로젝트 와이파이는 기기 간 순수
UDP(ROS2/DDS 디스커버리가 쓰는 것)를 막아놔서 두 기기를 ROS2로 직접 못
붙인다. 그래서 `mingky_fire_evac`과 같은 구조를 재사용한다:

```
[핑키]  person_follow_node (ROS2)                [GPU 컴퓨터]  infer_server.py (Flask, ROS2 아님)
  ├─ 후방 카메라 구독 (로컬)                             │
  ├─ /speed_limit 발행 (로컬, Nav2 velocity_smoother가 구독)
  └─ JPEG 프레임 ──── HTTP POST /infer ──────────────→  YOLO 추론 (GPU)
     검출 목록(클래스+좌표) ←─────────────────────────  └─ 응답만 돌려줌
```

화재 감지와 다른 점: "있다/없다" 하나가 아니라 검출된 각 박스의
클래스·좌표·크기를 전부 돌려받는다 -- 같은 손님을 계속 같은 손님으로
잡아두는 잠금 계산에 필요하다.

## 여러 손님 구분 (핵심 설계 포인트)

인형(손님 역할)이 p001/p002/p003 세 클래스로 나뉘어 있다. 안내 도중 다른
손님이 화면의 비슷한 위치로 끼어들면, 원래 안내받던 손님인 척 계속
따라가면 안 된다. 그래서 `target_lock.pick_target`은 위치만 보지 않는다:

1. 처음 잠글 때는 화면 중앙에 가장 가까운 검출을 아무 클래스나 잠근다.
2. 이후에는 **직전에 잠겼던 것과 같은 클래스**이면서 `max_jump_px` 이내인
   검출만 후보로 삼는다. 클래스가 다르면 위치가 아무리 가까워도 후보에서
   제외한다.

실제로 위치만 보고 잠갔을 때, 손님 A가 화면을 벗어나고 비슷한 자리에
손님 B가 들어오면 그대로 B를 계속 따라가는 문제를 겪었다 -- 이 조건은
그걸 막으려고 넣었다.

## 실기에서 걸린 함정 두 가지

### 1) `SpeedLimit.speed_limit = 0.0` 은 "정지"가 아니라 "무제한"

`nav2_msgs/SpeedLimit` 메시지 정의는 `speed_limit=0.0`을 "제한 없음"의
특수값으로 쓴다. 정지를 표현하려면 0.0이 아니라 작은 양수를 보내야 한다
(`stop_speed_percent` 파라미터, 기본값 `0.1`). 처음에 이걸 몰라서 0.0을
보냈다가 손님이 없는데도 로봇이 그냥 출발해버리는 걸 실기에서 확인했다.

### 2) 정지가 길어지면 Nav2 recovery(제자리 회전)가 끼어든다

`controller_server`의 `progress_checker`(`nav2_params.yaml`)는 "이 시간
안에 최소 이 거리는 움직여야 한다"를 감시한다(기본 `movement_time_allowance:
10.0`초). 이 노드가 손님을 오래 못 찾아 속도를 낮게 걸어두면, Nav2 는
"고장나서 못 움직이는 상태"로 오해하고 자체 recovery(제자리 회전 등)를
시작한다 -- 회전하는 동안은 후방 카메라가 손님 반대쪽을 보게 돼서 상황을
더 꼬이게 만든다.

**이 패키지는 `nav2_params.yaml`을 건드리지 않는다** (팀 공용 설정이라
임의로 안 바꿈). 이 기능을 실제로 켜는 로봇에서는 운영자가
`progress_checker.movement_time_allowance`를 손님이 없어질 수 있는 최대
시간보다 넉넉하게(실기 검증 시 60초로 임시 조정) 잡아둬야 한다:

```bash
ros2 param set /controller_server progress_checker.movement_time_allowance 60.0
```

(재부팅하면 `nav2_params.yaml`의 원래 값으로 돌아간다 -- 영구 적용하려면
그 파일 자체를 팀과 상의해서 고쳐야 한다.)

## 실행

### 1) GPU 컴퓨터에서 추론 서버 띄우기

```bash
python3 -m venv person_follow_venv
source person_follow_venv/bin/activate
pip install ultralytics flask pillow

python3 infer_server.py --model <가중치.pt 경로> --port 5001
```

떠 있는지 확인:

```bash
curl http://<이 컴퓨터의 IP>:5001/health
# {"device":"cuda:0","status":"ok"}
```

### 2) 핑키에서 노드 실행

```bash
ros2 run mingky_person_follow person_follow_node --ros-args \
  -p infer_server_url:=http://<GPU-PC-IP>:5001/infer
```

`infer_server_url`은 기본값이 없는 필수 파라미터라, 안 주면 노드가 바로
에러를 내고 죽는다.

## 파라미터 (`person_follow_node`)

| 이름 | 기본값 | 설명 |
| --- | --- | --- |
| `robot_id` | `pinky-01` | 로봇 ID |
| `image_topic` | `/rear_camera/image_raw/compressed` | 구독할 후방 카메라 압축 이미지 토픽 |
| `frame_max_age_sec` | `2.0` | 이보다 오래된 프레임은 판단에 안 씀 |
| `infer_server_url` | *(필수)* | 추론 서버 주소, 예: `http://192.168.129.41:5001/infer` |
| `infer_timeout_sec` | `2.0` | 추론 요청 타임아웃. 실패해도 노드는 안 죽고 그 프레임만 미검출 처리 |
| `conf_threshold` | `0.25` | YOLO confidence 임계값 (실측: 흑백·후방 각도에서 신뢰도가 0.3 안팎까지 낮게 나온 사례가 있어 여유 있게 낮춤) |
| `window_size` | `7` | 최근 몇 프레임을 볼지 |
| `required_detections` | `5` | 최근 `window_size`장 중 몇 장 이상 검출돼야 FOLLOWING/STOPPED를 확정할지 |
| `max_jump_px` | `200.0` | 직전 잠금 위치에서 이 픽셀 이상 벗어난 같은 클래스 검출은 다른 개체로 봄 |
| `stop_speed_percent` | `0.1` | 정지 시 `/speed_limit` 값(%). 0.0 금지(무제한 특수값) |
| `follow_speed_percent` | `100.0` | 팔로잉 중 `/speed_limit` 값(%) |

## 동작

1. 후방 카메라 프레임을 그대로(디코드 없이) HTTP로 GPU 컴퓨터에 전달.
2. 클래스 잠금 + 위치 근접도로 이번 프레임의 대상을 고른다(`target_lock.py`).
3. 최근 `window_size`프레임 중 `required_detections`프레임 이상 검출/미검출
   이어야 FOLLOWING/STOPPED 상태를 바꾼다(`follow_state.py`) -- 순간적인
   오탐·단일 미검출로 속도가 홱홱 바뀌는 걸 막는다.
4. 상태가 바뀔 때만 `/speed_limit`을 다시 발행한다(단, 시작 시 한 번은
   무조건 발행 -- Nav2가 기본값(무제한)으로 아는 상태를 방지).
5. `/person_follow/following` (std_msgs/Bool)과 `/events`에도 상태 전환을
   같이 알린다.

## 검증 이력

- 실기(pinky-02), 2026-08-13~14: 후방 카메라 + YOLO11s(로컬 학습, 80
  epoch, mAP50 0.970, 클래스 p001/p002/p003)로 실제 Nav2 안내 주행 중
  손님 유무에 따른 정지/재개를 여러 차례 확인. `progress_checker` 조정
  전에는 정지 구간에서 Nav2 자체 recovery(제자리 회전)가 끼어들어 후방
  카메라가 손님을 놓치는 문제가 있었음 -- 위 "실기에서 걸린 함정" 항목
  참고.
- 클래스 잠금 조건(같은 클래스만 후보) 추가 전에는 위치만으로 잠가서 다른
  인형으로 잠금이 넘어가는 문제를 겪음.

## 운영 확인

```bash
# GPU 컴퓨터
curl http://127.0.0.1:5001/health

# 핑키
ros2 node list | grep person_follow
ros2 topic echo /person_follow/following
ros2 topic echo /speed_limit
```
