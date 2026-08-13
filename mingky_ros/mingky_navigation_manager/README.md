# mingky_navigation_manager

환자 안내 세션과 무관한 엔지니어용 Waypoint 시험 주행을 Nav2에 전달합니다.

## 책임

- 저장 Waypoint와 화면에서 만든 임시 좌표를 `NavigateToPose` 목표로 변환
- 동시에 하나의 시험 목표만 허용
- `/guide_manager/state`를 확인해 환자 안내 중 시험 주행 차단
- 저전압·비상정지·환자 세션 시작 시 진행 중인 시험 목표 취소
- 시작·성공·실패 결과를 `/navigation_manager/result`로 반환

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
| 출력 | `/navigation_manager/active` | 시험 목표 진행 여부 (`std_msgs/Bool`) |
| 출력 | `/navigation_manager/result` | 시작·성공·실패 JSON (`std_msgs/String`) |
