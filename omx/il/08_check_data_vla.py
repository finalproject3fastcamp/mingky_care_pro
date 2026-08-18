#!/usr/bin/env python
"""[dry-run] SmolVLA 학습 전 데이터셋·정책 호환성 점검.

06_train_vla.sh 를 몇 시간 돌린 뒤에 로더/스키마 오류로 뻗는 사고를 미리 잡는 스크립트.
08_check_data.py 는 정적 검사(에피소드 수·길이 분포)라 v0.4.4 기준으로 충분했지만,
SmolVLA 는 v0.6.x 로더 + 정책이 데이터셋을 실제로 받아먹을 수 있는지가 다른 문제다.

  python 08_check_data_vla.py                           # _common.sh 의 REPO 사용
  python 08_check_data_vla.py mingky/pill_bottle_v1     # 데이터셋 직접 지정
  python 08_check_data_vla.py --skip-policy             # 정책 로딩만 생략 (오프라인)
  python 08_check_data_vla.py --skip-forward            # forward 는 안 함 (import 단계만)

무엇을 잡나:
  · LeRobot v0.4.4 로 녹화한 데이터셋을 v0.6.x 로더가 그대로 여는지
  · 카메라 키(top/wrist) 가 정책이 기대하는 이름과 일치하는지
  · 관절 상태·액션 차원, 이미지 해상도가 SmolVLA base 체크포인트와 맞는지
  · TASK 문자열이 아이템에 실제로 들어 있는지 (SmolVLA 는 언어 조건화 정책)

실행 전제:
  이 스크립트는 il_vla venv 에서 돌아야 한다.  기존 il venv 에서 돌리면 v0.4.4 로 판독해
  실제 학습에서 어떻게 될지는 알 수 없다.
    source ~/venv/il_vla/bin/activate
    python 08_check_data_vla.py
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


def repo_from_common() -> str:
    common = Path(__file__).resolve().parent / "_common.sh"
    if not common.exists():
        return ""
    m = re.search(r"REPO=\$\{REPO:-(.+?)\}", common.read_text())
    return m.group(1).strip() if m else ""


def die(msg: str, code: int = 1) -> None:
    print(f"  ! {msg}")
    sys.exit(code)


def check_venv() -> None:
    venv = os.environ.get("VIRTUAL_ENV", "")
    # 이름이 정확히 il_vla 로 끝나야 함. il 이나 다른 venv 에서 돌리면 무의미하다.
    if not venv.rstrip("/").endswith("il_vla"):
        die(
            f"VIRTUAL_ENV={venv or '(없음)'} — il_vla venv 에서 실행하세요.\n"
            "    source ~/venv/il_vla/bin/activate"
        )


def check_lerobot_version() -> str:
    try:
        import lerobot
    except ImportError:
        die("lerobot import 실패. 먼저 bash 01_install_vla.sh 를 돌리세요.")
    ver = getattr(lerobot, "__version__", "?")
    print(f"  lerobot {ver}")
    if not (ver.startswith("0.6") or ver.startswith("0.7")):
        print(f"    ! 0.6.x 또는 0.7.x 를 기대했습니다. SmolVLA 지원 여부 확인 필요.")
    return ver


def cache_root() -> Path:
    return Path(os.environ.get("HF_HOME", str(Path.home() / ".cache/huggingface"))) / "lerobot"


def load_dataset(repo: str):
    print(f"\n== 데이터셋 로드: {repo} ==")
    disk = cache_root() / repo
    if not disk.exists():
        print(f"  ! 로컬 캐시가 없음: {disk}")
        print("    05_record.sh 로 녹화한 로컬 데이터셋이어야 이 점검이 의미가 있습니다.")
        die("데이터셋 파일이 없어 진행 불가.")

    # LeRobot 릴리스마다 import 경로가 바뀐다. 알려진 후보 두 개를 시도.
    try:
        from lerobot.common.datasets.lerobot_dataset import LeRobotDataset
    except ImportError:
        try:
            from lerobot.datasets.lerobot_dataset import LeRobotDataset
        except ImportError as e:
            die(
                f"LeRobotDataset import 실패: {e}\n"
                "    이 배포판의 실제 경로를 찾아 08_check_data_vla.py 상단을 수정하세요."
            )

    try:
        ds = LeRobotDataset(repo)
    except Exception as e:
        die(f"데이터셋 오픈 실패: {e}\n    v0.4.4 스키마와 v0.6.x 로더가 불일치일 수 있음.")

    n_ep = getattr(ds, "num_episodes", None)
    n_fr = getattr(ds, "num_frames", None)
    fps = getattr(ds, "fps", None)
    print(f"  에피소드: {n_ep}")
    print(f"  프레임:   {n_fr:,}" if isinstance(n_fr, int) else f"  프레임:   {n_fr}")
    print(f"  fps:      {fps}")
    return ds


def probe_one_item(ds):
    print("\n== 첫 아이템 구조 ==")
    try:
        item = ds[0]
    except Exception as e:
        die(f"ds[0] 실패: {e}\n    parquet/video 파일이 손상됐거나 v0.6.x 가 못 읽는 필드.")

    for k, v in item.items():
        shape = getattr(v, "shape", None)
        dtype = getattr(v, "dtype", type(v).__name__)
        preview = ""
        if isinstance(v, str):
            preview = f"  = {v!r}"
        elif shape is None and not isinstance(v, (list, tuple, dict)):
            preview = f"  = {v!r}"
        print(f"  {k}: shape={shape} dtype={dtype}{preview}")

    # SmolVLA 는 언어 조건화 — task/instruction 필드가 있어야 함.
    task_keys = [k for k in item if "task" in k.lower() or "instruction" in k.lower()]
    if not task_keys:
        print("  ! 아이템에 task/instruction 필드가 없음. SmolVLA 학습 시 문제 될 수 있음.")
    else:
        print(f"  task 필드 발견: {task_keys}")
    return item


def check_camera_keys(ds, item) -> None:
    print("\n== 카메라 키 확인 ==")
    cams = [k for k in item if k.startswith("observation.images.")]
    got = {c.rsplit(".", 1)[-1] for c in cams}
    expected = {"top", "wrist"}
    print(f"  발견: {sorted(got) or '(없음)'}")
    if got != expected:
        print(f"    ! 예상({sorted(expected)}) 과 다름. cams.env 나 녹화 시 이름을 확인.")
    else:
        print("  OK.")


def check_policy(item, skip_forward: bool) -> None:
    print("\n== SmolVLA 정책 로드 ==")
    SmolVLAPolicy = None
    for path in (
        "lerobot.common.policies.smolvla.modeling_smolvla",
        "lerobot.policies.smolvla.modeling_smolvla",
    ):
        try:
            mod = __import__(path, fromlist=["SmolVLAPolicy"])
            SmolVLAPolicy = getattr(mod, "SmolVLAPolicy")
            print(f"  import OK: {path}")
            break
        except (ImportError, AttributeError):
            continue
    if SmolVLAPolicy is None:
        die(
            "SmolVLAPolicy import 실패. 이 배포판의 경로가 다릅니다.\n"
            "    'lerobot-train --policy.type=smolvla --help' 로 실제 이름을 확인하고\n"
            "    이 스크립트의 후보 경로 목록에 추가하세요."
        )

    try:
        policy = SmolVLAPolicy.from_pretrained("lerobot/smolvla_base")
        print(f"  from_pretrained OK: {type(policy).__name__}")
    except Exception as e:
        die(f"from_pretrained 실패: {e}\n    HF 접근 실패거나 체크포인트 이름이 바뀌었을 수 있음.")

    cfg = getattr(policy, "config", None)
    if cfg is not None and hasattr(cfg, "input_features"):
        print("  정책이 기대하는 입력 features:")
        for k, spec in cfg.input_features.items():
            print(f"    {k}: {spec}")
        got = set(item.keys())
        missing = set(cfg.input_features.keys()) - got
        if missing:
            print(f"  ! 데이터셋에 없는 정책 입력: {sorted(missing)}")
            print("    카메라 키 이름 불일치가 제일 흔한 원인.")
        else:
            print("  데이터셋이 정책 입력을 다 커버합니다.")

    if skip_forward:
        return

    # forward 한 번 태워보기 — 실제 shape 오류를 여기서 잡는다.
    print("\n== forward 한 번 시도 ==")
    try:
        import torch
    except ImportError:
        print("  torch 없음, forward 생략.")
        return

    device = "cuda" if torch.cuda.is_available() else "cpu"
    policy.to(device).eval()

    # 아이템을 배치 차원 붙여 넣는다. 문자열은 그대로.
    batch = {}
    for k, v in item.items():
        if isinstance(v, torch.Tensor):
            batch[k] = v.unsqueeze(0).to(device)
        else:
            batch[k] = [v] if not isinstance(v, list) else v

    try:
        with torch.no_grad():
            # SmolVLA 는 select_action(batch) 를 노출한다 (릴리스마다 이름 다를 수 있음).
            fn = getattr(policy, "select_action", None) or getattr(policy, "forward", None)
            if fn is None:
                print("  ! select_action / forward 둘 다 없음. 정책 API 를 확인하세요.")
                return
            out = fn(batch)
        shape = getattr(out, "shape", None) or (
            {k: getattr(v, "shape", None) for k, v in out.items()} if isinstance(out, dict) else "?"
        )
        print(f"  forward OK. 출력 shape={shape}")
    except Exception as e:
        die(
            f"forward 실패: {e}\n"
            "    상태·액션 차원이 정책이 사전학습된 로봇과 다를 수 있음.\n"
            "    파인튜닝은 되지만 초기 손실이 크게 시작될 수 있음 — 사전학습 이점이 반감."
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", nargs="?", default=None, help="예: mingky/pill_bottle_v1")
    ap.add_argument("--skip-policy", action="store_true", help="정책 로딩 이후 단계 생략")
    ap.add_argument("--skip-forward", action="store_true", help="forward 만 생략")
    args = ap.parse_args()

    print("== 환경 확인 ==")
    check_venv()
    check_lerobot_version()

    repo = args.repo or repo_from_common()
    if not repo:
        die("데이터셋 이름을 알 수 없습니다:  python 08_check_data_vla.py <계정/이름>")

    ds = load_dataset(repo)
    item = probe_one_item(ds)
    check_camera_keys(ds, item)
    if not args.skip_policy:
        check_policy(item, args.skip_forward)

    print("\n" + "=" * 60)
    print(" 점검 끝. 위에 ! 표시가 없으면 06_train_vla.sh 로 진행해도 됩니다.")
    print(" ! 가 있다면 학습 전에 먼저 해결하세요 — 학습 몇 시간을 날립니다.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
