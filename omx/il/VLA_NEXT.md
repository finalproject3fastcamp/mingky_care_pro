# SmolVLA 실험 — 다음에 할 것

지금(2026-08-18) 은 스크립트 초안만 만들어둔 상태다. **AI 노트북에 앉아서** 아래 순서로 진행한다.
ACT 파이프라인(01~07)과 별개로, `*_vla.*` 세트만 굴리면 된다.

---

## 0. 그 전에 (이 노트북에서 미리 하기)

- [ ] 지금 만든 초안 4개 파일을 커밋·푸시
  - `omx/il/01_install_vla.sh`
  - `omx/il/06_train_vla.sh`
  - `omx/il/07_run_vla.sh`
  - `omx/il/08_check_data_vla.py`
  - 이 문서 `omx/il/VLA_NEXT.md`
- [ ] AI 노트북에 `mingky/pill_bottle_v1` 데이터셋이 있는지 확인
  - 경로: `~/.cache/huggingface/lerobot/mingky/pill_bottle_v1/`
  - 없으면 이 노트북에서 옮기든가, 거기서 다시 녹화하든가 결정

## 1. AI 노트북에 앉으면 — 설치 (한 번)

```bash
cd ~/…/mingky_care_pro && git pull
bash omx/il/01_install_vla.sh          # 20~40분
```

- ffmpeg / dialout 그룹이 이미 있어야 함 (기존 `01_install.sh` 로 이미 설정됨)
- `~/venv/il_vla` 를 새로 만들고 `lerobot[smolvla]>=0.6.0` 설치
- `lerobot/smolvla_base` HF 체크포인트를 사전 캐싱

## 2. 학습 전 dry-run (수 분)

```bash
source ~/venv/il_vla/bin/activate
python omx/il/08_check_data_vla.py
```

- **여기서 ! 표시가 하나라도 뜨면 06 으로 넘어가지 말 것.** 학습 몇 시간을 날린다.
- 잡히는 문제:
  - v0.4.4 로 녹화한 데이터셋을 v0.6.x 로더가 못 읽음 (스키마 이동)
  - 카메라 키(`top`/`wrist`) 이름 불일치
  - `SmolVLAPolicy` import 경로가 이 배포판에서 달라짐
  - 정책 forward pass 에서 shape 오류

## 3. 학습 (3~8시간)

```bash
bash omx/il/06_train_vla.sh
# 다른 터미널에서:
tail -f ~/train/smolvla_pill_bottle_v1.log
```

- 기본 20k 스텝 (사전학습이 있어 ACT 40k 보다 짧게 잡음)
- VRAM 에 맞춰 batch_size 자동 결정. OOM 나면 `BATCH_SIZE=8 bash 06_train_vla.sh`
- 첫 실행 시 `--policy.*` 옵션 이름이 릴리스와 안 맞으면
  `lerobot-train --policy.type=smolvla --help` 로 실이름 대조 후 스크립트 수정

## 4. 평가

```bash
bash omx/il/07_run_vla.sh 10           # 10회
```

- 리더-팔로워 둘 다 꽂아야 스페이스바(DAgger) 개입 가능
- **첫 액션까지 20~40초** 걸림 (모델 warm-up). 그동안 손대지 말 것
- 카메라 위치·조명이 녹화 때와 같아야 성능이 살아남

## 5. ACT 와 A/B 비교 기록

같은 시나리오 10회씩 돌린 뒤 [TASK.md](TASK.md) 에 아래 지표를 나란히 적어둔다.

| | ACT | SmolVLA |
|---|---|---|
| 성공 회차 |  /10 |  /10 |
| 평균 완료 시간(초) |  |  |
| 사람 개입 횟수 |  |  |
| 실패 유형 (못 잡음/떨어뜨림/…) |  |  |

**단일 작업(약통 1종) 이라 SmolVLA 가 ACT 를 크게 이기기는 어려움.** 큰 차이 안 나면 그게 정상이고,
진짜 판단은 작업을 2~3종으로 늘렸을 때 해야 한다.

---

## 아직 하지 않은 것 (원하면 이어서)

- `docs/omx-smolvla.md` — A/B 결과 서술식 정리 문서
- 작업 확장 계획 (약통 여러 종·트레이 칸 지정 등, `film_conditioning=true` 로 재학습)
- 스크립트 안 `[TODO]` 주석들 (특히 06_train_vla.sh 의 CLI 옵션 검증)
