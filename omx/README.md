# OMX — 약품 자동 조제 파트

OpenManipulator-X 로봇팔로 처방된 약품을 집어 트레이에 담는 파트입니다.
모방학습(Imitation Learning)으로 Pick & Place 동작을 학습합니다.

## 구성

```
src/omx/
├── teleop.sh                     # 리더-팔로워 텔레오퍼레이션 실행
├── teleop_cam.sh                 # 텔레옵 + 카메라 2대 + rerun 시각화
├── src/
│   └── omx_f_keyboard_teleop.py  # 키보드 텔레옵 (팔로워 단독 제어)
└── backups/                      # 캘리브레이션 및 드라이버 백업
```

- 리더 암 - 팔로워 암 텔레오퍼레이션으로 시연 데이터를 수집합니다.
- 카메라 2대: 고정 오버헤드(`top`) + 로봇 장착(`wrist`).
  `/dev/videoN` 번호는 재부팅마다 바뀌므로 `/dev/v4l/by-id/` 고정 경로를 사용합니다.
- 데이터 수집 → 정책 학습 → 실제 로봇 평가 순으로 진행합니다.

## 환경 준비

```bash
# 1. 가상환경
python3 -m venv ~/venv/il && source ~/venv/il/bin/activate

# 2. LeRobot v0.4.4 설치 (이 레포에 포함하지 않음)
git clone https://github.com/huggingface/lerobot.git src/lerobot
cd src/lerobot && git checkout v0.4.4 && pip install -e .
```

- Ubuntu 24.04
- LeRobot **v0.4.4** 고정 (최신 버전과 CLI가 달라 업그레이드하지 않음)
- OMX 지원(`omx_follower` / `omx_leader`)은 LeRobot v0.4.4에 포함되어 있습니다.

## 실행

```bash
./teleop.sh        # 텔레옵만
./teleop_cam.sh    # 텔레옵 + 카메라 2대
```

## 출처

- 학습 프레임워크: [huggingface/lerobot](https://github.com/huggingface/lerobot) v0.4.4 (Apache-2.0)
- 모터 제어: [ROBOTIS-GIT/DynamixelSDK](https://github.com/ROBOTIS-GIT/DynamixelSDK) (Apache-2.0)

> LeRobot은 upstream 그대로 사용하므로 이 레포에 포함하지 않고 위 절차대로 설치합니다.
> 이 디렉터리에는 프로젝트에서 직접 작성한 스크립트와 설정만 올립니다.
