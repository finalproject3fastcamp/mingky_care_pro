#!/usr/bin/env python
"""[5.5단계] 학습 전에 데이터 점검 — 06_train.sh 돌리기 전에 한 번 보세요.

  python 08_check_data.py                  # _common.sh 의 REPO 를 봅니다
  python 08_check_data.py mingky/pill_bottle_v1  # 데이터셋 직접 지정

왜 하나 — 학습을 몇 시간 돌린 **뒤에야** 데이터 문제를 발견해서 사이클을 통째로
버린 적이 여러 번 있습니다. 여기서 미리 잡습니다:

  · 에피소드 수가 생각보다 적음 (중간에 죽어서 저장이 안 된 것)
  · 유난히 짧은 에피소드 (실수로 → 를 두 번 눌러 몇 프레임만 찍힌 것)
  · 유난히 긴 에피소드 (헤매다 시간 상한에 걸린 것 — 학습에 안 좋습니다)
  · 카메라 키가 도중에 바뀜 (top/wrist 이름을 다르게 준 채로 이어 찍은 것)
"""

from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path.home() / ".cache/huggingface/lerobot"


def repo_from_common() -> str:
    common = Path(__file__).resolve().parent / "_common.sh"
    m = re.search(r"REPO=\$\{REPO:-(.+?)\}", common.read_text()) if common.exists() else None
    return m.group(1).strip() if m else ""


def main() -> int:
    repo = sys.argv[1] if len(sys.argv) > 1 else repo_from_common()
    if not repo:
        print("데이터셋 이름을 알 수 없습니다:  python 08_check_data.py <계정/이름>")
        return 1

    root = ROOT / repo
    info_path = root / "meta" / "info.json"
    if not info_path.exists():
        print(f"데이터셋이 없습니다: {root}")
        print("찍은 게 있는지 확인:  ls ~/.cache/huggingface/lerobot/*/")
        return 1

    info = json.loads(info_path.read_text())
    fps = info.get("fps", 30)
    cams = [k for k in info.get("features", {}) if k.startswith("observation.images.")]

    print("=" * 60)
    print(f" {repo}")
    print("=" * 60)
    print(f"  에피소드   {info.get('total_episodes', 0)} 개")
    print(f"  프레임     {info.get('total_frames', 0):,} ({info.get('total_frames', 0) / fps / 60:.1f}분)")
    print(f"  fps        {fps}")
    print(f"  카메라     {', '.join(c.rsplit('.', 1)[-1] for c in cams) or '없음 (!)'}")

    try:
        import pandas as pd
    except ImportError:
        print("\n  (pandas 가 없어 에피소드별 검사는 건너뜁니다)")
        return 0

    files = sorted(glob.glob(str(root / "meta/episodes/**/*.parquet"), recursive=True))
    if not files:
        print("\n  에피소드 메타가 없습니다 — 녹화가 제대로 저장되지 않았을 수 있습니다.")
        return 1

    df = pd.concat([pd.read_parquet(f) for f in files]).sort_values("episode_index")
    lengths = df["length"]
    med = lengths.median()

    print()
    print(f"  길이       중앙값 {med / fps:.1f}초 (최소 {lengths.min() / fps:.1f} / 최대 {lengths.max() / fps:.1f})")

    if "tasks" in df.columns:
        tasks = {}
        for t in df["tasks"]:
            key = t[0] if isinstance(t, (list, tuple)) and len(t) else str(t)
            tasks[key] = tasks.get(key, 0) + 1
        print("  작업       " + ", ".join(f"{k} ({v}개)" for k, v in tasks.items()))
        if len(tasks) > 1:
            print("             ! 한 데이터셋에 작업이 여러 개 섞여 있습니다. 의도한 게 맞나요?")

    short = df[lengths < med * 0.3]
    long = df[lengths > med * 2.0]
    print()
    if len(short):
        print(f"  ! 너무 짧은 에피소드 {len(short)}개: {list(short['episode_index'])[:12]}")
        print("    → 실수로 바로 넘긴 것일 수 있습니다. 영상을 확인하고 필요하면 다시 찍으세요")
    if len(long):
        print(f"  ! 너무 긴 에피소드 {len(long)}개: {list(long['episode_index'])[:12]}")
        print("    → 헤매다 시간 상한에 걸린 것입니다. 이런 게 많으면 학습이 나빠집니다")
    if not len(short) and not len(long):
        print("  길이는 고른 편입니다. 좋습니다.")

    n = info.get("total_episodes", 0)
    print()
    if n < 20:
        print(f"  에피소드가 {n}개입니다. 30~50개는 모으고 학습하는 걸 권합니다.")
    else:
        print(f"  {n}개면 학습해볼 만합니다:  bash 06_train.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
