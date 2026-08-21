# mingky_navigation_manager

환자 안내 세션과 무관한 엔지니어용 Waypoint 시험 주행을 Nav2에 전달합니다.

## 책임

- 저장 Waypoint와 화면에서 만든 임시 좌표를 `NavigateToPose` 목표로 변환
- 동시에 하나의 시험 목표만 실행하며 새 목표는 기존 목표·복구를 취소하고 교체
- `/guide_manager/state`를 확인해 환자 안내 중 시험 주행 차단
- 저전압·비상정지·환자 세션 시작 시 진행 중인 시험 목표 취소
- 시작·성공·실패·교체 취소 결과를 `/navigation_manager/result`로 반환
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
| 출력 | `/navigation_manager/result` | 시작·성공·실패·취소 JSON (`std_msgs/String`) |
| 출력 | `/navigation_manager/route_plan` | 원래 목적지까지의 경로 (`nav_msgs/Path`) |
| 출력 | `/navigation_manager/recovery_plan` | 실제 이동 중인 임시 복구 경로 (`nav_msgs/Path`) |

## Adaptive Recovery

`mingky_system.launch.xml`은 환자 안내와 동일한 `recovery_mode:=adaptive`를
시험주행에도 전달합니다. Nav2가 목표를 중단하면 `mingky_smart_recovery`가
LiDAR로 만든 탈출 후보를 `ComputePathToPose`로 먼저 검증하고, 임시 지점에
도착한 뒤 원래 Waypoint를 다시 보냅니다.

후보 검증만 한 경로는 관제로 내보내지 않습니다. 원래 경로는 파란색으로
유지하고, 실제 선택된 복구 경로만 주황색 점선으로 별도 표시할 수 있도록 두
출력 토픽을 분리합니다. 복구 후보는 전방·측방뿐 아니라 로봇 뒤쪽의 빈 공간도
검토하되 전방 후보에 더 높은 점수를 줍니다. 주행 컨트롤러 자체는 음수
선속도를 허용하지 않아 뒤쪽 목표도 제자리에서 방향을 맞춘 뒤 전진으로
접근합니다.

다만 `/low_obstacle/observation`이 현재 또는 이번 주행에 보존된 확정 장애물을
보고하는 동안에는 전방 반구의 탈출 후보를 제외합니다. 이때는 좌우·후방
후보만 실제 경로 검증하며, 주행 작업 종료로 보존 표식이 지워지면 일반 후보
선택으로 자동 복귀합니다.

단독 실행 기본값은 기존 동작을 보존하는 `default`입니다. 통합 실행의
Adaptive Recovery는 일시적인 동적 장애물이 사라질 때까지 0.3초 간격으로
횟수 제한 없이 다시 판단합니다. 전역 planner가 현재 셀을 occupied로 판단해
모든 후보 경로를 거부하면 대기 시간을 늘리지 않고, LiDAR가 확보한 방향으로
Nav2 충돌 검사를 거쳐 최대 12cm만 즉시 회전·전진한 뒤 원래 목표를 다시
계산합니다. 명시적 취소, 환자 안내 시작, 저전압,
비상정지 또는 AMCL 재탐색이 시작되면 복구 목표와 예약된 재시도도 함께
취소합니다.

원형 주행은 특정 waypoint 이름이나 목표까지의 임의 거리로 가로채지 않습니다.
전역 경로는 시간 기준 1Hz가 아니라 로봇이 8cm 전진한 뒤에만 다시 계산합니다.
따라서 제자리 회전 중 좌·우 우회 경로가 번갈아 들어와 Rotation Shim이 방향을
계속 바꾸는 현상을 막으면서, 이동 중에는 계속 새 장애물을 반영합니다. 경로가
로봇 뒤쪽으로 바뀌면 MPPI에 넘기기 전에 제자리 회전하며, 목표의 10cm 위치
허용 범위에 들어온 뒤에는 같은 Shim이 최종 yaw를 맞춥니다.
