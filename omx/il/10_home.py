#!/usr/bin/env python
"""팔을 학습 시작 자세로 천천히 되돌린다.

## 왜 필요한가

정책 실행이 실패하거나 시간 상한에 걸리면 팔이 뻗은 자세 그대로 멈춘다. 다음
회차는 **학습 회차들이 시작했던 그 자세**에서 출발해야 공정하다 — 시작 자세가
다르면 정책이 처음부터 못 보던 상태를 만난다.

## 목표 자세는 어디서 오나

임의로 정하지 않는다. 데이터셋(`mingky/pill_bottle_v1`) 29개 회차의 **첫 프레임
관절값 중앙값**이다. 사람이 손으로 맞춰 놓고 찍은 그 자세다.

그 값을 아래 `HOME` 에 박아 둔다. 데이터셋(921MB)이 없는 자리에서도 홈 복귀는
되어야 하기 때문이다 — 팔이 뻗은 채 멈췄을 때 제일 먼저 필요한 것이 이 명령인데,
그게 수백 MB 다운로드에 묶여 있으면 곤란하다. 데이터셋이 있으면 `--from-dataset`
으로 다시 계산해 확인할 수 있다.

## 안전

  - 현재 자세에서 목표까지 `--seconds` 동안 나눠 보낸다. 한 번에 뛰지 않는다.
  - `--max-step` 으로 한 틱에 움직일 수 있는 양을 제한한다.
  - 끝나면 토크를 풀고 나온다 (`disable_torque_on_disconnect`).
  - `--dry-run` 은 목표까지의 차이만 찍고 아무것도 보내지 않는다.

## 사용

    ~/venv/il/bin/python 10_home.py --dry-run     # 얼마나 움직일지만 본다
    ~/venv/il/bin/python 10_home.py               # 6초에 걸쳐 복귀
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

DATASET = Path.home() / ".cache/huggingface/lerobot/mingky/pill_bottle_v1"
PORT = "/dev/omx_follower"
ROBOT_ID = "omx_follower_arm"

JOINTS = ["shoulder_pan.pos", "shoulder_lift.pos", "elbow_flex.pos",
          "wrist_flex.pos", "wrist_roll.pos", "gripper.pos"]

# mingky/pill_bottle_v1 29개 회차 첫 프레임의 중앙값 (2026-08-20 산출).
# 회차별 편차가 작아서(pan ±8, lift ±4) 중앙값 하나로 충분하다.
# 데이터를 다시 찍으면 `--from-dataset` 으로 계산해 이 값을 갱신한다.
HOME = np.array([1.98, -63.22, 54.58, 48.33, 0.02, 59.80])


def home_from_dataset() -> tuple[list[str], np.ndarray]:
    """데이터셋에서 다시 계산한다 (`--from-dataset`). 상수 검증용."""
    import glob

    import pandas as pd

    info = json.loads((DATASET / "meta" / "info.json").read_text())
    names = info["features"]["observation.state"]["names"]
    files = sorted(glob.glob(str(DATASET / "data" / "**" / "*.parquet"), recursive=True))
    df = pd.concat([pd.read_parquet(f) for f in files])
    firsts = [
        np.asarray(g.sort_values("frame_index").iloc[0]["observation.state"], dtype=float)
        for _, g in df.groupby("episode_index")
    ]
    return names, np.median(np.stack(firsts), axis=0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--max-step", type=float, default=2.0,
                    help="한 틱에 관절당 최대 이동량")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--from-dataset", action="store_true",
                    help="상수 대신 데이터셋에서 다시 계산한다 (데이터셋 필요)")
    args = ap.parse_args()

    if not Path(PORT).exists():
        print(f"로봇 포트가 없습니다: {PORT}")
        return 1

    if args.from_dataset:
        names, target = home_from_dataset()
        diff = float(np.max(np.abs(target - HOME)))
        print(f"데이터셋에서 재계산 — 상수와 최대 차이 {diff:.2f}")
        if diff > 1.0:
            print(f"  ! HOME 상수를 갱신하세요: {np.round(target, 2).tolist()}")
    else:
        names, target = JOINTS, HOME

    from lerobot.robots.omx_follower import OmxFollower
    from lerobot.robots.omx_follower.config_omx_follower import OmxFollowerConfig

    # 카메라는 열지 않는다 — 관절만 쓰고, 다른 프로그램이 카메라를 쓰고 있어도
    # 홈 복귀는 되어야 한다.
    robot = OmxFollower(OmxFollowerConfig(port=PORT, id=ROBOT_ID, cameras={}))
    robot.connect()
    try:
        obs = robot.get_observation()
        start = np.array([float(obs[n]) for n in names])

        print("관절            지금      목표      차이")
        for i, n in enumerate(names):
            print(f"  {n:16} {start[i]:7.2f}  {target[i]:7.2f}  {target[i]-start[i]:+7.2f}")
        biggest = float(np.max(np.abs(target - start)))
        print(f"\n최대 이동량 {biggest:.2f}")

        if args.dry_run:
            print("--dry-run — 아무것도 보내지 않았습니다.")
            return 0

        ticks = max(1, int(args.seconds * args.fps))
        period = 1.0 / args.fps
        cur = start.copy()
        for k in range(1, ticks + 1):
            want = start + (target - start) * (k / ticks)
            step = np.clip(want - cur, -args.max_step, args.max_step)
            cur = cur + step
            robot.send_action({n: float(cur[i]) for i, n in enumerate(names)})
            time.sleep(period)

        obs = robot.get_observation()
        final = np.array([float(obs[n]) for n in names])
        err = float(np.max(np.abs(final - target)))
        print(f"복귀 완료 — 목표와 최대 오차 {err:.2f}")
        if err > 5:
            print("! 오차가 큽니다. 팔이 무언가에 걸렸는지 확인하세요.")
            return 1
        return 0
    finally:
        robot.disconnect()   # 토크가 풀린다


if __name__ == "__main__":
    sys.exit(main())
