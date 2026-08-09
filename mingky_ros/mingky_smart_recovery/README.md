# mingky_smart_recovery

Nav2가 기존 목표에 도달하지 못했을 때 주변 LiDAR 공간에서 임시 탈출 지점
후보를 만드는 프로젝트 전용 패키지입니다.

`selector.py`가 로봇 기준 전·좌·우·후방 후보를 만들고 다음 기준으로
정렬합니다.

- 후보 방향의 장애물 여유 거리
- 원래 목적지 방향과의 일치도
- 회전·후진 비용
- 이전 실패 횟수

`guide_manager`는 상위 후보를 map 좌표로 변환하고 Nav2
`ComputePathToPose`로 검증한 뒤에만 임시 목표로 사용합니다. 임시 지점에
도착하면 원래 안내 목표를 다시 보냅니다. 따라서 LiDAR 한 방향이 비어
보인다는 이유만으로 로봇을 바로 움직이지 않습니다.

기존 동작이 기본값입니다. 실제 로봇에서 단계적으로 활성화할 때만 다음처럼
선택합니다.

```bash
ros2 launch mingky_bringup mingky_system.launch.xml recovery_mode:=adaptive
```

전역 경로 계획기는 독립적으로 선택할 수 있습니다. `navfn`이 기본이며,
복도 모서리에서 더 자연스러운 경로를 시험할 때만 `smac2d`를 선택합니다.

```bash
ros2 launch mingky_bringup mingky_system.launch.xml \
  recovery_mode:=adaptive planner_mode:=smac2d
```

적응형 모드의 첫 주행에는 Spin·Backup이 없는 behavior tree를 사용합니다.
탈출 후보를 모두 소진한 뒤에만 기존 Nav2 복구 트리를 마지막으로 실행합니다.

LiDAR나 Nav2 위치 피드백이 1초 이상 오래됐거나, 저전압·비상정지 상태이면
적응형 복구를 시작하지 않습니다. 기본 최대 복구 횟수는 3회입니다.

```bash
cd mingky_ros/mingky_smart_recovery
pytest -q
```
