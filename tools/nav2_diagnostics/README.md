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

## 실행 순서

1. Pinky SSH 터미널에서 로봇 bringup을 실행하고 계속 켜 둡니다.

   ```bash
   source /opt/ros/jazzy/setup.bash
   source ~/pinky_pro/install/setup.bash
   export ROS_DOMAIN_ID=21
   ros2 launch pinky_bringup bringup_robot.launch.xml
   ```

2. PC 터미널에서 실험을 시작합니다.

   ```bash
   cd /home/wmk/Documents/mingky_care_pro
   tools/nav2_diagnostics/run_experiment.sh \
     --profile tools/nav2_diagnostics/profiles/01_costmap_resolution_0025.yaml \
     --label costmap-resolution-0025
   ```

3. RViz에서 `2D Pose Estimate`로 현재 로봇 위치와 방향을 지정한 뒤,
   `Nav2 Goal`로 시험할 목표를 하나 이상 보냅니다.

4. 실험을 마치려면 RViz를 닫거나, 실험 터미널에서 `Ctrl+C`를 누릅니다.
   Nav2와 기록기가 종료되고 결과 요약이 출력됩니다.

### 옵션 의미

- `--profile <파일>`: 기본 `nav2_params.yaml`에 이번 실험 동안만 덮어쓸 값입니다.
  예를 들어 `01_costmap_resolution_0025.yaml`은 global/local costmap 해상도만
  `0.025m`로 바꿉니다. 원본 파라미터 파일은 수정하지 않습니다.
- `--label <이름>`: 실험의 목적을 사람이 구분하기 위한 이름입니다. 결과 폴더 이름과
  SQLite의 `runs.label`에만 사용하며 Nav2 동작에는 영향을 주지 않습니다.
- `--map <파일>`: 기본 지도 대신 특정 지도 YAML을 선택합니다.
- `--notes <메모>`: 시험 구간이나 관찰 목적을 SQLite에 함께 남깁니다.
- `--sample-hz <Hz>`: 위치·속도·LiDAR 요약을 기록하는 초당 횟수입니다. 기본값은 `5`입니다.

### 프로파일 예시

아래처럼 새 프로파일을 작성하면 벽 주변 비용 영향 범위만 `0.25m`로 시험할 수
있습니다.

```yaml
# profiles/03_inflation_radius_025.yaml
local_costmap:
  local_costmap:
    ros__parameters:
      inflation_layer:
        inflation_radius: 0.25

global_costmap:
  global_costmap:
    ros__parameters:
      inflation_layer:
        inflation_radius: 0.25
```

이 프로파일은 `cost_scaling_factor`, footprint 등 다른 값은 기본 설정을 그대로
사용합니다. 새 파일은 한 번에 검증할 파라미터 묶음 하나만 변경하도록 작성합니다.

### 종료 후 저장되는 결과

각 실험은 `artifacts/nav2_diagnostics/<run_id>/`에 저장됩니다.

- `effective_nav2_params.yaml`: 기본 설정과 프로파일을 합친, 해당 실험에 실제 적용한 설정
- `run.sqlite`: 지도·Git 커밋·프로파일·유효 파라미터 원문, 목표별 결과, 위치·속도·LiDAR
  요약, Nav2 경고/오류를 저장한 DB
- `run.sqlite` 내부 스냅샷: abort, recovery, planner/controller 오류 때의
  global/local costmap과 경로

나중에 실험 결과를 다시 요약하려면 다음을 사용합니다.

```bash
python3 tools/nav2_diagnostics/report.py \
  artifacts/nav2_diagnostics/<run_id>/run.sqlite
```

기록 대상은 `/amcl_pose`, `/odom`, `/cmd_vel`, `/scan`, global/local plan,
global/local costmap, `/goal_pose`, NavigateToPose 상태, `/rosout`입니다.

## Foxglove로 실시간 보기

Foxglove은 기본 실험 실행에 자동으로 포함하지 않습니다. 실시간 시각화가 필요할
때만 별도 PC 터미널에서 Bridge를 실행합니다.

```bash
cd /home/wmk/Documents/mingky_care_pro
source /opt/ros/jazzy/setup.bash
source install/setup.bash
export ROS_DOMAIN_ID=21

ros2 launch mingky_bringup foxglove.launch.py
```

Foxglove Desktop 또는 웹 앱에서 `ws://localhost:8765`으로 연결합니다.
다른 PC에서 접속할 경우에는 Bridge를 실행한 PC의 IP를 사용합니다.

## 원칙

- 한 실험에서는 한 파라미터 묶음만 바꾼다.
- 실험 프로파일은 실제 `nav2_params.yaml`을 직접 덮어쓰지 않는다.
- rosbag은 만들지 않는다. costmap·경로는 실패 시점만 압축해 SQLite에 저장한다.
- 실험 도구는 `mingky_ros/`의 운영 launch에 자동으로 포함하지 않는다.
