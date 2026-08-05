# Nav2 Diagnostics

Nav2 파라미터를 실주행으로 비교하기 위한 **개발 전용 도구**입니다.
환자 안내 기능의 운영 ROS 패키지에는 포함하지 않습니다.

## 역할

- 실험마다 지도·Git 커밋·Nav2 파라미터를 SQLite에 스냅샷으로 남긴다.
- 목표 전송 결과, recovery, 속도 명령, 위치 추정, 라이다 최소 거리 등의
  수치를 기록한다.
- 원본 토픽은 선택적으로 rosbag에 남기고, SQLite에는 rosbag 경로만 연결한다.
- Foxglove는 실시간 관찰과 실패 장면 분석에 사용한다.

## 구조

```text
tools/nav2_diagnostics/
├── run_experiment.sh  # 실험 세션 시작·종료 보조
├── recorder.py        # ROS 토픽을 구독하는 기록 노드
├── report.py          # SQLite 실험 결과 요약·비교
├── schema.sql         # SQLite 스키마
└── profiles/          # 파라미터 실험 프로파일
```

실행 결과는 Git에 올리지 않고 다음 경로에 둡니다.

```text
artifacts/nav2_diagnostics/<run_id>.sqlite
artifacts/nav2_diagnostics/<run_id>/rosbag/
```

## 구현 순서

1. `run_experiment.sh`가 실험 ID와 SQLite 파일을 만들고 기준 정보를 저장한다.
2. `recorder.py`가 `/amcl_pose`, `/odom`, `/cmd_vel`, `/scan`, Nav2 action
   feedback·status, `/rosout`을 구독해 요약 수치를 기록한다.
3. 시작·종료 시 Nav2 파라미터와 맵 파일 해시를 `parameter_snapshots`에 저장한다.
4. `report.py`가 성공률, 소요 시간, recovery 수, 최소 장애물 거리를 비교한다.
5. 검증된 값만 `pinky/pinky_navigation/params/nav2_params.yaml`에 반영한다.

## 원칙

- 한 실험에서는 한 파라미터 묶음만 바꾼다.
- 실험 프로파일은 실제 `nav2_params.yaml`을 직접 덮어쓰지 않는다.
- SQLite에 Costmap·LaserScan 원본을 계속 넣지 않는다. 큰 원본은 rosbag으로
  남긴다.
- 실험 도구는 `mingky_ros/`의 운영 launch에 자동으로 포함하지 않는다.
