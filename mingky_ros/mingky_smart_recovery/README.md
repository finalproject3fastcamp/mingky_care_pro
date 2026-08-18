# mingky_smart_recovery

Nav2가 기존 목표에 도달하지 못했을 때 주변 LiDAR 공간에서 임시 탈출 지점
후보를 만드는 프로젝트 전용 패키지입니다.

`selector.py`가 로봇 기준 15도 간격의 24방향 후보를 만들고 다음 기준으로
정렬합니다. 점수 상위 후보 중에서도 각도가 최소 30도 이상 떨어진 4개만
경로 검증에 사용해 비슷한 방향을 반복 검사하지 않습니다.

- 후보 방향의 장애물 여유 거리
- 원래 목적지 방향과의 일치도
- 회전·후진 비용
- 이전 실패 횟수

`guide_manager`는 상위 후보를 map 좌표로 변환하고 Nav2
`ComputePathToPose`로 검증한 뒤에만 임시 목표로 사용합니다. 임시 지점에
도착하면 원래 안내 목표를 다시 보냅니다. 따라서 LiDAR 한 방향이 비어
보인다는 이유만으로 로봇을 바로 움직이지 않습니다.

실로봇용 `mingky_system.launch.xml`은 적응형 복구를 기본으로 사용합니다.
`guide_manager` 노드를 단독 실행할 때는 기존 동작이 기본값이므로 다음처럼
명시해야 합니다.

```bash
ros2 launch mingky_bringup mingky_system.launch.xml recovery_mode:=adaptive
```

통합 launch에서 기존 Nav2 복구만 비교 시험할 때는 반대로 끕니다.

```bash
ros2 launch mingky_bringup mingky_system.launch.xml recovery_mode:=default
```

통합 실행은 전역 경로 계획기로 `smac2d`를 사용합니다. Nav2 컨트롤러는
안내·Waypoint 시험주행 모두 MPPI를 사용해 10Hz 로컬 코스트맵의 동적
장애물을 반영합니다. 단독 노드 실행 시에만 호환성을 위해 `navfn`이 기본입니다.

```bash
ros2 launch mingky_bringup mingky_system.launch.xml \
  recovery_mode:=adaptive planner_mode:=smac2d
```

적응형 모드는 Spin·Wait·Backup이 없는 behavior tree만 사용합니다. 탈출 후보를
모두 소진하면 0.3초 동안 정지한 뒤 최신 LiDAR로 후보를 다시 만들며, 원래 안내
목표를 횟수 제한 없이 유지합니다.

LiDAR나 Nav2 위치 피드백이 1초 이상 오래되면 정지 상태로 기다렸다가 다시
확인합니다. 저전압·비상정지 또는 새 안내 목표가 들어오면 기존 목표의 반복을
중단합니다.

```bash
cd mingky_ros/mingky_smart_recovery
pytest -q
```
