# Nav2 Diagnostics

Nav2 파라미터를 실주행으로 비교하기 위한 **개발 전용 도구**입니다.
환자 안내 기능의 운영 ROS 패키지에는 포함하지 않습니다.

## 역할

- 실험마다 지도·Git 커밋·Nav2 파라미터를 SQLite에 스냅샷으로 남긴다.
- 목표 전송 결과, recovery, 속도 명령, 위치 추정, 라이다 최소 거리 등의
  수치를 기록한다.
- 5Hz 주행 요약과 Nav2 경고·목표 결과를 SQLite에 남긴다.
- abort, recovery, planner/controller 오류 시점에는 costmap과 경로를 압축 스냅샷으로 남긴다.
- Foxglove는 실시간 관찰과 실패 장면 분석에 사용한다.

## 구조

```text
tools/nav2_diagnostics/
├── run_experiment.sh  # 실험 실행, 유효 파라미터 생성, 기록 시작
├── merge_params.py    # 기본 설정과 프로파일을 깊게 병합
├── recorder.py        # ROS 토픽·Nav2 오류·실패 스냅샷 기록
├── report.py          # SQLite 실험 결과 요약
├── schema.sql         # SQLite 스키마
└── profiles/          # 한 가지 가설씩 검증하는 파라미터 프로파일
```

실행 결과는 Git에 올리지 않고 다음 경로에 둡니다.

```text
artifacts/nav2_diagnostics/<run_id>/run.sqlite
artifacts/nav2_diagnostics/<run_id>/effective_nav2_params.yaml
```

## 구현 순서

## 사용법

Pinky bringup을 별도 터미널에서 실행한 뒤 PC에서 실행합니다.

```bash
cd /home/wmk/Documents/mingky_care_pro
tools/nav2_diagnostics/run_experiment.sh \
  --profile tools/nav2_diagnostics/profiles/01_costmap_resolution_0025.yaml \
  --label costmap-resolution-0025
```

RViz에서 `2D Pose Estimate`를 먼저 지정하고 `Nav2 Goal`로 목표를 클릭합니다.
종료하면 요약이 출력됩니다. 나중에 다시 볼 때는 다음을 사용합니다.

```bash
python3 tools/nav2_diagnostics/report.py \
  artifacts/nav2_diagnostics/<run_id>/run.sqlite
```

기록 대상은 `/amcl_pose`, `/odom`, `/cmd_vel`, `/scan`, global/local plan,
global/local costmap, `/goal_pose`, NavigateToPose 상태, `/rosout`입니다.

## 원칙

- 한 실험에서는 한 파라미터 묶음만 바꾼다.
- 실험 프로파일은 실제 `nav2_params.yaml`을 직접 덮어쓰지 않는다.
- rosbag은 만들지 않는다. costmap·경로는 실패 시점만 압축해 SQLite에 저장한다.
- 실험 도구는 `mingky_ros/`의 운영 launch에 자동으로 포함하지 않는다.
