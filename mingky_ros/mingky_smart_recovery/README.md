# mingky_smart_recovery

Nav2가 기존 목표에 도달하지 못했을 때 주변 LiDAR 공간에서 임시 탈출 지점
후보를 만드는 프로젝트 전용 패키지입니다.

현재 단계는 ROS 액션을 직접 실행하지 않습니다. `selector.py`가 로봇 기준
전·좌·우·후방 후보를 만들고 다음 기준으로 정렬합니다.

- 후보 방향의 장애물 여유 거리
- 원래 목적지 방향과의 일치도
- 회전·후진 비용
- 이전 실패 횟수

후속 연결부는 상위 후보를 map 좌표로 변환하고 Nav2 `ComputePathToPose`로
검증한 뒤에만 임시 목표로 사용합니다. 따라서 LiDAR 한 방향이 비어 보인다는
이유만으로 로봇을 바로 움직이지 않습니다.

```bash
cd mingky_ros/mingky_smart_recovery
pytest -q
```
