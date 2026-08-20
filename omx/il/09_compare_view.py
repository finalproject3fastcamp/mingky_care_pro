#!/usr/bin/env python
"""녹화 당시 화각과 지금 화각을 나란히 붙여 본다 — 읽기 전용, 팔을 건드리지 않는다.

## 왜 필요한가

07_run.sh 가 맨 앞에서 경고하는 그것이다 — **카메라 위치·각도가 녹화할 때와
같아야 한다.** 조금만 밀려도 정책이 엉뚱한 데로 간다. 화각이 6px 밀린 걸 모르고
평가하다 하루를 날린 적이 있다 (README 3단계).

그런데 "같은지" 를 눈으로만 보면 못 고른다. 지금 화면만 띄우는 03_check_cameras
로는 **녹화 때와 비교**할 수 없다. 그래서 학습 데이터셋의 첫 프레임을 꺼내
지금 프레임과 좌우로 붙이고, 픽셀 차이도 같이 숫자로 낸다.

## 사용

    ~/venv/il/bin/python 09_compare_view.py
    ~/venv/il/bin/python 09_compare_view.py --episode 3 --out /tmp/비교

정답은 "차이 0" 이 아니다 — 물체 위치는 회차마다 다르다. 봐야 할 것은
**배경·작업대 모서리·팔 베이스가 같은 자리에 있는가** 다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

DATASET = Path.home() / ".cache/huggingface/lerobot/mingky/pill_bottle_v1"
CAMS_ENV = Path(__file__).resolve().parent / "cams.env"


def read_cams_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def recorded_frame(cam_key: str, episode: int) -> np.ndarray | None:
    """데이터셋 영상에서 한 프레임. 회차 시작 자세라 화각 비교에 쓰기 좋다.

    lerobot 이 AV1(libsvtav1) 로 인코딩하는데 OpenCV 의 ffmpeg 빌드는 이걸 못 연다
    ("Missing Sequence Header" 만 찍고 빈 프레임을 준다). lerobot 자신이 쓰는
    torchcodec 으로 읽는다.
    """
    videos = sorted((DATASET / "videos" / f"observation.images.{cam_key}").rglob("*.mp4"))
    if not videos:
        return None
    # 파일 하나에 여러 회차가 이어 붙어 있다. 회차 경계를 정확히 몰라도
    # 화각 비교에는 충분하므로 파일을 고르고 앞쪽 프레임을 쓴다.
    path = videos[min(episode, len(videos) - 1)]
    try:
        from torchcodec.decoders import VideoDecoder
    except ImportError:
        return None
    try:
        frame = VideoDecoder(str(path))[0]          # CHW, uint8, RGB
    except Exception:  # noqa: BLE001
        return None
    rgb = frame.permute(1, 2, 0).cpu().numpy()
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def live_frame(device: str, warmup: int = 30) -> np.ndarray | None:
    """지금 카메라. 자동노출이 잡힐 때까지 흘려보낸 뒤의 프레임을 쓴다."""
    cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    got = None
    for _ in range(warmup):
        ok, f = cap.read()
        if ok:
            got = f
    cap.release()
    return got


def sharpness(img: np.ndarray) -> float:
    """03_focus.py 와 같은 지표 — 라플라시안 분산.

    주의: 녹화 프레임은 AV1 로 압축됐다 되살린 것이라 값이 낮게 나온다. 지금
    화면이 더 선명하게 찍히는 것은 정상이고 렌즈 문제가 아니다. 두 값을 직접
    비교하지 말고, 지금 값이 03_focus.py 기준을 넘는지만 본다.
    """
    return float(cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())


def shift_px(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """두 프레임 사이의 화각 이동량(px). 이것이 이 도구의 핵심 숫자다.

    눈으로는 못 고른다 — 물체가 조금만 다르게 놓여 있어도 "밀린 것 같다" 로
    보인다(실제로 15~20px 밀렸다고 잘못 읽은 적이 있는데 재보니 0.8px 이었다).
    위상 상관은 배경 질감 전체를 보고 부화소 단위로 이동량을 낸다.
    """
    g1 = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(np.float32)
    g2 = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY).astype(np.float32)
    win = cv2.createHanningWindow(g1.shape[::-1], cv2.CV_32F)
    (dx, dy), _ = cv2.phaseCorrelate(g1 * win, g2 * win)
    return dx, dy


def label(img: np.ndarray, text: str) -> np.ndarray:
    out = img.copy()
    cv2.rectangle(out, (0, 0), (out.shape[1], 28), (0, 0, 0), -1)
    cv2.putText(out, text, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1,
                cv2.LINE_AA)
    return out


def episode_starts(cam_key: str) -> list[tuple[Path, float]]:
    """회차마다 (영상 파일, 시작 시각). 메타데이터에 경계가 들어 있다.

    파일 하나에 여러 회차가 이어 붙어 있어서 앞 프레임만 봐서는 한 회차밖에
    못 본다. `from_timestamp` 로 각 회차의 첫 프레임을 정확히 집는다.
    """
    import pandas as pd

    files = sorted((DATASET / "meta" / "episodes").rglob("*.parquet"))
    if not files:
        return []
    df = pd.concat([pd.read_parquet(f) for f in files]).sort_values("episode_index")
    col = f"videos/observation.images.{cam_key}"
    out = []
    for _, row in df.iterrows():
        path = (DATASET / "videos" / f"observation.images.{cam_key}"
                / f"chunk-{int(row[f'{col}/chunk_index']):03d}"
                / f"file-{int(row[f'{col}/file_index']):03d}.mp4")
        out.append((path, float(row[f"{col}/from_timestamp"])))
    return out


def frame_at(path: Path, ts: float) -> np.ndarray | None:
    try:
        from torchcodec.decoders import VideoDecoder
        frame = VideoDecoder(str(path)).get_frame_played_at(ts).data
    except Exception:  # noqa: BLE001
        return None
    rgb = frame.permute(1, 2, 0).cpu().numpy()
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def spread(cam_key: str, outdir: Path, live: np.ndarray | None, cols: int = 5) -> int:
    """학습 회차들의 시작 장면을 한 장에 펼친다.

    "지금 장면이 학습 때와 다른가" 는 회차 **하나**와 비교해서는 답이 안 나온다.
    회차마다 약통·봉투를 조금씩 다르게 놓고 찍었기 때문이다. 봐야 할 것은
    "그 흩어진 범위 **안에** 들어오는가" 다.
    """
    starts = episode_starts(cam_key)
    if not starts:
        print("회차 메타데이터를 읽지 못했습니다")
        return 1

    tiles = []
    for i, (path, ts) in enumerate(starts):
        f = frame_at(path, ts)
        if f is not None:
            tiles.append(label(cv2.resize(f, (320, 240)), f"ep{i}"))
    if not tiles:
        print("회차 프레임을 하나도 읽지 못했습니다")
        return 1

    if live is not None:
        # 지금 장면은 맨 앞에 두고 테두리로 구분한다.
        now = cv2.resize(live, (320, 240))
        now = cv2.copyMakeBorder(now[4:236, 4:316], 4, 4, 4, 4,
                                 cv2.BORDER_CONSTANT, value=(0, 0, 255))
        tiles.insert(0, label(now, "NOW"))

    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [cv2.hconcat(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
    path = outdir / f"spread_{cam_key}.jpg"
    cv2.imwrite(str(path), cv2.vconcat(rows))
    print(f"[{cam_key}] 회차 {len(starts)}개 → {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode", type=int, default=0)
    ap.add_argument("--out", default="/tmp/view_compare")
    ap.add_argument("--spread", action="store_true",
                    help="학습 회차들의 시작 장면을 펼쳐 본다 (분포 확인)")
    ap.add_argument("--no-live", action="store_true",
                    help="카메라를 열지 않는다 (다른 프로그램이 쓰는 중일 때)")
    args = ap.parse_args()

    if args.spread:
        cams = read_cams_env(CAMS_ENV)
        outdir = Path(args.out); outdir.mkdir(parents=True, exist_ok=True)
        live = None
        if not args.no_live:
            live = live_frame(cams.get("TOP_CAM", ""))
            if live is None:
                print("! 지금 화면을 못 읽었습니다 — 다른 프로그램이 카메라를 "
                      "쓰고 있을 수 있습니다 (ffplay 등). 학습 회차만 펼칩니다.")
        return spread("top", outdir, live)

    if not DATASET.is_dir():
        print(f"학습 데이터셋이 없습니다: {DATASET}")
        return 1

    cams = read_cams_env(CAMS_ENV)
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    worst = 0.0
    for key, env_key in (("top", "TOP_CAM"), ("wrist", "WRIST_CAM")):
        device = cams.get(env_key)
        if not device:
            print(f"[{key}] cams.env 에 {env_key} 가 없습니다")
            return 1
        if not Path(device).exists():
            print(f"[{key}] 카메라가 없습니다: {device}")
            return 1

        rec = recorded_frame(key, args.episode)
        now = live_frame(device)
        if rec is None:
            print(f"[{key}] 녹화 프레임을 읽지 못했습니다")
            return 1
        if now is None:
            print(f"[{key}] 지금 프레임을 읽지 못했습니다 — {device}")
            return 1

        if rec.shape != now.shape:
            now = cv2.resize(now, (rec.shape[1], rec.shape[0]))

        # 회색조 절대차의 평균. 물체가 움직인 것도 잡히므로 절대값보다는
        # top/wrist 사이 비교와 회차별 추이로 본다.
        diff = float(np.mean(cv2.absdiff(cv2.cvtColor(rec, cv2.COLOR_BGR2GRAY),
                                         cv2.cvtColor(now, cv2.COLOR_BGR2GRAY))))
        worst = max(worst, diff)

        dx, dy = shift_px(rec, now)
        move = float(np.hypot(dx, dy))

        combo = cv2.hconcat([label(rec, f"{key}  RECORDED (ep{args.episode})"),
                             label(now, f"{key}  NOW")])
        path = outdir / f"{key}.jpg"
        cv2.imwrite(str(path), combo)
        판정 = "OK" if move < 5 else ("주의" if move < 15 else "밀림")
        print(f"[{key}] 화각 이동 {move:5.1f}px (dx{dx:+.1f} dy{dy:+.1f}) {판정}"
              f" | 픽셀차 {diff:5.1f} | 선명도 지금 {sharpness(now):7.1f} | {path}")

    print()
    print("  화각 이동이 5px 아래면 카메라는 그대로다. wrist 는 팔 자세에 따라")
    print("  배경이 달라지므로, 프레임 속 **그리퍼 위치**가 같은지로 본다.")
    print()
    print("  카메라가 멀쩡해도 **장면**이 다르면 정책은 실패한다. 두 장을 열어")
    print("  약통·봉투가 녹화 때와 같은 자리·같은 자세인지 함께 확인할 것.")
    if worst > 60:
        print(f"  ! 픽셀차가 큽니다({worst:.0f}) — 물체 배치나 조명을 확인하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
