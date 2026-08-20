# mingky_person_follow

안내 중인 환자 ID와 같은 YOLO 인형 클래스를 추적해 Nav2 주행을
정상·감속·대기 상태로 조절한다. 13cm 인형 높이와 카메라
보정값으로 절대거리를 근사하고, QR 거리가 보이면 추정값을 다시 보정한다.

## 안전 원칙

- `/guide_manager/state`가 `guiding`일 때 현재 `patient_id`와 같은
  YOLO 클래스 또는 QR data의 거리만 인정한다.
- YOLO는 현재 `patient_id`와 같은 `p001`~`p003` 클래스만 사용한다.
- 화면 상·하단에 잘린 바운딩 박스는 거리 판단에서 제외한다.
- QR과 YOLO가 순간적으로 흔들려도 2초까지 직전 주행 상태를 유지한다.
  주행 중 2초를 초과하면 `waiting`이 된다.
- 벽에 밀착한 waiting point에서 출발할 때는 최대 4초 또는
  0.30m까지 35% 속도로 시야를 확보한다. 한계 안에 첫 환자 검출을
  못하면 `waiting`이 된다.
- 안내가 아니면 속도 제한을 100%로 풀어 충전소 복귀·Waypoint
  시험에 간섭하지 않는다.
- 환자 대기는 Nav2 실패가 아니다. Guide Manager가 목표를 정상
  취소하고 환자가 돌아오면 같은 Waypoint를 다시 보낸다.

## 데이터 흐름

```text
/rear_camera/image_raw
  └─ mingky_qr_distance
       └─ /rear_qr/observation (visible, patient_id, distance, QR 픽셀 중심)
              └─ person_follow_node
                    ├─ /speed_limit          정상·감속·임시 정지
                    └─ /person_follow/state Guide Manager heartbeat

/rear_camera/image_raw/compressed
  └─ HTTP YOLO (선택)
       └─ 세션 patient_id와 같은 인형 박스를 계속 추적
```

통합 실행에서는 `/person_follow/processing_active`가 `guiding` 세션 전체를
표시한다. 영상 압축과 QR 검출은 이 구간에서만 계산하지만, `waiting`도 활성
구간에 포함되므로 환자가 다시 보일 때의 자동 재검출 동작은 그대로 유지된다.

QR 기준이 없으면 `fy × 0.13m / 박스 높이` 공식으로 거리를
계산한다. QR이 보이면 그 시점의 QR 거리와 박스 높이를 새
기준으로 저장해 YOLO 박스 여백과 인형별 편차를 보정한다.

추론 응답의 검출 클래스가 바뀌거나 검출/미검출 상태가 전환되면
로봇 로그에 즉시 남긴다. 같은 결과가 이어지면 5초마다 클래스,
신뢰도, 바운딩 박스 크기를 요약해 기록한다.

### OpenCV QR과 YOLO의 역할

1. OpenCV가 QR 문자열과 네 모서리를 검출한다.
2. 카메라 보정값, QR 실측 크기와 `solvePnP`로 절대 거리를 계산한다.
   이 값이 정상·감속·대기의 주 판단값이다.
3. 세션 `patient_id`와 같은 YOLO 클래스만 환자로 잠그고,
   위치가 갑자기 바뀌는 후보는 제외한다. 동일 영역에 다른 클래스
   박스가 겹치면 세션 클래스가 최고 점수이고 2등보다 0.15 이상
   높을 때만 인정한다. QR 중심이 있으면 첫 대상의 위치 기준으로 쓴다.
4. 새 대상과 잠금 해제 후 재등장한 대상은 3프레임 연속 확인한 뒤
   잠근다. 이미 잠긴 대상은 위치가 이어지면 즉시 계속 추적한다.
5. QR이 없어도 카메라 `fy`, 13cm 실측 높이와 박스 높이로 거리를
   근사한다. QR을 확인한 뒤에는 마지막 QR 거리와 박스 높이를 기준으로
   `현재 거리 = 마지막 QR 거리 × 마지막 박스 높이 / 현재 박스 높이`를
   적용한다. 잘린 박스도 신뢰도 0.70 이상과 위의 클래스·연속성 조건을
   만족하면 저속으로 재개한다.
6. QR과 YOLO가 모두 유실되면 2초 동안 직전 속도를 유지하고,
   그 시간을 넘기면 `waiting`으로 전환한다.
7. 첫 환자 검출 전에는 `acquiring`으로 표시하고 35% 속도로만
   출발한다. 4초 또는 odometry 0.30m 중 한 계에 먼저 도달하면 정지한다.

즉 YOLO 박스로도 첫 절대거리를 계산하고, OpenCV QR이 보이면
실측값으로 추정 스케일을 재보정하는 구조다.

## 거리 정책

| 상태 | 기본 기준 | 동작 |
| --- | --- | --- |
| `normal` | 0.15m 이하 | 100% 주행 |
| `slow` | 0.15m 초과, 0.30m 미만 | 35% 감속 |
| `waiting` | 0.30m 이상 또는 2초 초과 추적 두절 | Nav2 목표 취소 후 대기 |
| `inactive` | 안내 중이 아님 | 속도 제한 해제 |

경계에서 속도가 흔들리지 않도록 기본 0.02m hysteresis와 최근 QR
거리 median 필터를 사용한다. 실로봇 검증 후 로봇별 거리 임계값을
`robot.env`에서 조정한다.

## 통합 실행

현재 실로봇 시험을 위해 통합 실행에서 기본으로 켜져 있다.

```bash
ros2 launch mingky_bringup mingky_system.launch.xml \
  start_patient_follow:=true \
  patient_follow_infer_server_url:=http://<GPU-PC-IP>:5001/infer
```

YOLO 서버 URL을 비우면 QR 거리만으로 동작한다. QR 보완을 켜려면
GPU 컴퓨터에서 모델을 로드한다.

```bash
pip install ultralytics flask pillow
python3 infer_server.py --model <가중치.pt> --port 5001
curl http://127.0.0.1:5001/health
```

## 주요 파라미터

| 이름 | 기본값 | 설명 |
| --- | ---: | --- |
| `slow_distance_m` | `0.15` | 감속 시작 거리 |
| `stop_distance_m` | `0.30` | 환자 대기 전환 거리 |
| `distance_hysteresis_m` | `0.02` | 상태 복귀 여유폭 |
| `qr_stale_sec` | `1.0` | QR 거리 신뢰 시간 |
| `tracking_grace_sec` | `2.0` | QR·YOLO 순간 유실 시 주행 유지 시간 |
| `initial_acquire_grace_sec` | `4.0` | 출발 후 첫 환자 검출 유예 시간 |
| `initial_acquire_max_distance_m` | `0.30` | 첫 검출 전 최대 주행 거리 |
| `target_height_m` | `0.13` | YOLO 절대거리 계산용 인형 높이 |
| `target_min_confidence` | `0.55` | 일반 YOLO 대상 후보의 최소 신뢰도 |
| `target_class_overlap_iou` | `0.50` | 동일 물체의 타 클래스 후보를 비교할 IoU |
| `target_class_confidence_margin` | `0.15` | 겹친 2등 클래스보다 필요한 최소 점수 차이 |
| `target_confirm_frames` | `3` | 새 대상 잠금에 필요한 연속 프레임 수 |
| `target_confirm_max_jump_px` | `80.0` | 연속 확인 중 허용할 중심 이동량 |
| `bbox_edge_margin_px` | `5.0` | 완전한 박스 판정의 화면 가장자리 여유폭 |
| `partial_bbox_max_distance_m` | `0.35` | 잘린 박스를 저속 근접 검출로 인정할 최대 거리 |
| `partial_bbox_conf_threshold` | `0.70` | 잘린 박스 근접 검출의 최소 YOLO 신뢰도 |
| `slow_speed_percent` | `35.0` | 감속 시 Nav2 속도 비율 |
| `stop_speed_percent` | `0.1` | Nav2 취소 전 즉시 정지 속도 비율 |
| `infer_server_url` | 빈 문자열 | 선택 YOLO `/infer` URL |

`SpeedLimit.speed_limit=0.0`은 Nav2에서 제한 없음이므로 정지값으로
사용하지 않는다.

## 검증

```bash
ros2 topic echo /rear_qr/observation
ros2 topic echo /person_follow/state
ros2 topic echo /speed_limit
ros2 topic echo /guide_manager/state
```

실로봇에서는 QR 거리가 실측과 맞는지, 감속 구간, 0.30m 대기, 복귀
후 같은 Waypoint 재개, 사람·카메라·추론 서버 두절을 순서대로 확인한다.
