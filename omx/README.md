# OMX — 약품 자동 조제 파트

OpenManipulator-X 로봇팔로 처방된 약품을 집어 트레이에 담는 파트입니다.
모방학습(Imitation Learning)으로 Pick & Place 동작을 학습합니다.

## 구성

```
omx/
├── teleop.sh                     # 리더-팔로워 텔레오퍼레이션 실행
├── teleop_cam.sh                 # 텔레옵 + 카메라 2대 + rerun 시각화
├── il/                           # 모방학습 파이프라인 (설치 → 녹화 → 학습 → 평가)
├── src/
│   └── omx_f_keyboard_teleop.py  # 키보드 텔레옵 (팔로워 단독 제어)
└── backups/                      # 캘리브레이션 및 드라이버 백업
```

- 리더 암 - 팔로워 암 텔레오퍼레이션으로 시연 데이터를 수집합니다.
- 카메라 2대: 고정 오버헤드(`top`) + 로봇 장착(`wrist`).
  `/dev/videoN` 번호는 재부팅마다 바뀌므로 `/dev/v4l/by-id/` 고정 경로를 사용합니다.
- 데이터 수집 → 정책 학습 → 실제 로봇 평가 순으로 진행합니다.

## 하드웨어

| 구분 | 사양 |
| --- | --- |
| 로봇팔 | ROBOTIS OpenManipulator-X 리더-팔로워 세트 (각 6-DOF + 그리퍼) |
| 모터 | Dynamixel 시리즈 (팔로워 ID 11~16, 리더 ID 1~6, Protocol 2.0) |
| 모터 통신 | U2D2 USB 컨트롤러 × 2 (팔당 1개, `/dev/omx_leader` · `/dev/omx_follower` 로 udev 고정) |
| top 카메라 | Jieli USB Composite (640×480 MJPG 30fps, 작업대 위 오버헤드) |
| wrist 카메라 | Innomaker U20CAM-720P (640×480 MJPG 30fps, 팔 손목 장착) |
| 워크스테이션 | Ubuntu 24.04 (Wayland) / NVIDIA RTX 5080 Laptop 16GB (sm_120) |
| 딥러닝 스택 | PyTorch 2.10.0 + CUDA 12.8 / LeRobot v0.4.4 (+ 로컬 패치) |
| USB 배치 | 카메라 2대는 노트북 포트 직결, 로봇 팔 2대는 별도 4포트 허브 (같은 허브 금지 — 재열거로 카메라가 죽음) |

- **팔로워/리더는 USB 장치 이름이 아니라 모터 ID 로 판별합니다** (팔로워 11~16, 리더 1~6). `02_find_ports.sh` 가 이 판별을 자동화하고 udev 규칙을 씁니다.
- **카메라는 이름만 보고 top/wrist 를 정하지 마세요.** 실화면을 보고 골라야 합니다 (`03_check_cameras.sh --view`).
- USB 자동절전이 켜져 있으면 카메라가 검은 화면이 되므로 배치를 확정한 뒤 끕니다. 재부팅하면 풀리므로 다시 해야 합니다 — 명령은 [il/TASK.md](il/TASK.md) 2절 참고.

## 환경 준비

`il/01_install.sh` 가 가상환경(`~/venv/il`)·LeRobot v0.4.4·로컬 패치까지 한 번에 처리합니다.

```bash
bash il/01_install.sh
# 끝나면 반드시 로그아웃 → 로그인 (dialout 그룹 반영)
```

- Ubuntu 24.04
- LeRobot **v0.4.4** 고정 (최신 0.6.x 는 CLI 가 달라 이 디렉터리의 스크립트가 안 맞음)
- OMX 지원(`omx_follower` / `omx_leader`)은 LeRobot v0.4.4에 포함되어 있습니다.

## 모방학습 파이프라인 (`il/`)

`il/01_install.sh` → `02_find_ports.sh` → `03_check_cameras.sh` → `04_teleop.sh`
→ `05_record.sh` → `06_train.sh` → `07_run.sh` 순서로 실행합니다.
각 단계의 상세·소요 시간은 [il/README.md](il/README.md), 증상별 대처는
[il/TROUBLESHOOTING.md](il/TROUBLESHOOTING.md) 를 봅니다.

작업과 데이터셋 이름은 `il/_common.sh` 맨 아래 `REPO` / `TASK` 두 줄로 정합니다.
**작업을 바꾸면 데이터셋 이름도 반드시 바꿉니다** — 한 데이터셋에 섞이면 되돌릴 수 없습니다.

현재 작업의 정의·성공 기준·세팅 값·진행 기록은 [il/TASK.md](il/TASK.md) 에 있습니다.
지금은 **알약통을 봉투에 넣기**(단일 목표, `mingky/pill_bottle_v1`) 입니다.

이 작업을 어떻게 진행했는지(데이터·학습·평가와 겪은 문제)는
[docs/omx-imitation-learning.md](../docs/omx-imitation-learning.md) 에 정리했습니다.

카메라 경로(`il/cams.env`)는 노트북마다 다르므로 저장소에 올리지 않습니다.
`il/03_check_cameras.sh --view` 로 각자 만듭니다.

## LeRobot 로컬 패치

upstream 을 그대로 쓰지 않고 [il/lerobot_local_v0.4.4.patch](il/lerobot_local_v0.4.4.patch) 를
`01_install.sh` 가 적용합니다. **패치가 안 맞으면 `01_install.sh` 는 멈춥니다** — 로컬
lerobot 에 이미 수정이 들어가 있으면 파일별로 손으로 병합한 뒤 다시 실행하세요.

어떤 사고를 막는 패치이고, 왜 ACT `film_conditioning` 을 이번엔 껐는지는
[docs/omx-imitation-learning.md §4](../docs/omx-imitation-learning.md#4-lerobot-로컬-패치) 에서 봅니다.

## 텔레옵만 따로 실행

```bash
./teleop.sh        # 텔레옵만
./teleop_cam.sh    # 텔레옵 + 카메라 2대
```

`il/04_teleop.sh` 와 같은 일을 하지만, 이쪽은 포트·카메라 경로가 스크립트에 하드코딩되어 있습니다.
`il/` 파이프라인을 쓴다면 `il/04_teleop.sh` 를 쓰세요 (`cams.env` 를 읽습니다).

## 출처

- 학습 프레임워크: [huggingface/lerobot](https://github.com/huggingface/lerobot) v0.4.4 (Apache-2.0)
- 모터 제어: [ROBOTIS-GIT/DynamixelSDK](https://github.com/ROBOTIS-GIT/DynamixelSDK) (Apache-2.0)

> LeRobot 본체는 이 레포에 포함하지 않고 `01_install.sh` 로 설치합니다.
> 이 디렉터리에는 프로젝트에서 직접 작성한 스크립트·설정과, upstream 에 얹는 패치만 올립니다.
