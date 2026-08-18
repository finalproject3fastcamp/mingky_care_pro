# mingky_navigation_manager

환자 안내 세션과 무관한 엔지니어용 Waypoint 시험 주행을 Nav2에 전달합니다.

## 책임

- 저장 Waypoint와 화면에서 만든 임시 좌표를 `NavigateToPose` 목표로 변환
- 동시에 하나의 시험 목표만 허용
- `/guide_manager/state`를 확인해 환자 안내 중 시험 주행 차단
- 저전압·비상정지·환자 세션 시작 시 진행 중인 시험 목표 취소
- 시작·성공·실패 결과를 `/navigation_manager/result`로 반환
- 통합 실행에서는 Nav2 실패 시 프로젝트 Adaptive Recovery로 원래 목표 재시도

환자 안내 순서, 검사실 goal에서 waiting 지점으로 이어지는 상태 전이, 충전소
복귀는 `mingky_guide_manager`의 임상 흐름으로 유지합니다. 두 노드는
`/navigation_manager/active`를 공유해 시험 주행과 환자 안내가 동시에 Nav2 목표를
보내지 않도록 중재합니다.

## 토픽

| 방향 | 토픽 | 형식 |
| --- | --- | --- |
| 입력 | `/navigation_manager/goto` | 저장 Waypoint 이름 (`std_msgs/String`) |
| 입력 | `/navigation_manager/goto_pose` | `name`, `x`, `y`, `yaw` JSON (`std_msgs/String`) |
| 입력 | `/navigation_manager/cancel` | 상위 작업 취소 요청 (`std_msgs/Bool`) |
| 입력 | `/scan` | Adaptive Recovery 탈출 후보 계산 (`sensor_msgs/LaserScan`) |
| 출력 | `/navigation_manager/active` | 시험 목표 진행 여부 (`std_msgs/Bool`) |
| 출력 | `/navigation_manager/result` | 시작·성공·실패 JSON (`std_msgs/String`) |

## Adaptive Recovery

`mingky_system.launch.xml`은 환자 안내와 동일한 `recovery_mode:=adaptive`를
시험주행에도 전달합니다. Nav2가 목표를 중단하면 `mingky_smart_recovery`가
LiDAR로 만든 탈출 후보를 `ComputePathToPose`로 먼저 검증하고, 임시 지점에
도착한 뒤 원래 Waypoint를 다시 보냅니다.

단독 실행 기본값은 기존 동작을 보존하는 `default`입니다. 엔지니어 시험이
무한히 이어지지 않도록 Adaptive Recovery는 기본 최대 3회
(`recovery_max_attempts`)까지만 실행합니다. 환자 안내·저전압·비상정지·AMCL
재탐색이 시작되면 복구 목표와 예약된 재시도도 함께 취소합니다.
