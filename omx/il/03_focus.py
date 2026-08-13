#!/usr/bin/env python
"""[3단계 보조] 렌즈 초점을 맞추는 동안 선명도를 실시간으로 본다.

  python 03_focus.py            # 카메라를 고르라고 물어본다
  python 03_focus.py top        # cams.env 의 top 카메라
  python 03_focus.py 1          # 목록의 1번
  python 03_focus.py /dev/v4l/by-id/...

왜 필요한가 — 렌즈 링을 돌리면서 화면만 보면 "어디가 제일 선명한지"를 눈으로 못 고른다.
숫자를 띄워놓고 돌리면 최고점을 지나치는 순간이 바로 보인다.
맞춘 뒤에는 **링을 테이프로 고정**한다. 안 하면 며칠 뒤 다시 풀린다.

여기서 재는 값은 `03_check_cameras.py` 와 **같은 방법·같은 해상도**다 (V4L2 + MJPG 640x480).
그래야 나중에 "녹화 때와 같은가"를 비교할 수 있다. 열기 함수를 직접 가져다 쓰는 이유다.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import cv2

HERE = Path(__file__).resolve().parent
ENV = HERE / "cams.env"

# 03_check_cameras.py 는 숫자로 시작해서 일반 import 가 안 된다. 파일에서 직접 읽는다.
# 복사해 두면 한쪽만 고쳤을 때 두 스크립트의 숫자가 달라진다 — 그러면 비교가 무의미해진다.
_spec = importlib.util.spec_from_file_location("_check_cameras", HERE / "03_check_cameras.py")
_cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cc)

GOOD = 100.0  # 03_check_cameras.py 의 "많이 흐림" 기준과 같은 값


def read_cams_env() -> dict[str, str]:
    """cams.env 에서 top/wrist 경로를 읽는다. 없으면 빈 딕셔너리."""
    named: dict[str, str] = {}
    if not ENV.exists():
        return named
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip().strip('"')
        if key == "TOP_CAM":
            named["top"] = val
        elif key == "WRIST_CAM":
            named["wrist"] = val
    return named


def resolve(arg: str | None, cams: list[str], named: dict[str, str]) -> tuple[str, str]:
    """인자를 실제 카메라 경로로 바꾼다. (경로, 표시이름) 을 돌려준다."""
    if arg in named:
        return named[arg], arg
    if arg and arg.startswith("/"):
        return arg, Path(arg).name[:30]
    if arg is not None and arg.isdigit() and int(arg) < len(cams):
        i = int(arg)
        return cams[i], f"[{i}]"

    if arg is not None:
        print(f"'{arg}' 를 못 찾았습니다.")

    # 물어본다.
    print("=" * 64)
    print(" 초점을 맞출 카메라")
    print("=" * 64)
    for i, p in enumerate(cams):
        tag = ""
        for name, path in named.items():
            if path == p:
                tag = f"  ({name})"
        print(f"  [{i}] {Path(p).name[:52]}{tag}")
    print()
    while True:
        s = input("번호? ").strip()
        if s.isdigit() and int(s) < len(cams):
            i = int(s)
            label = next((n for n, p in named.items() if p == cams[i]), f"[{i}]")
            return cams[i], label
        print("  그런 번호가 없습니다.")


def bar(value: float, peak: float, width: int = 24) -> str:
    """지금 값이 최고점 대비 어디쯤인지. 최고점을 지났는지 눈으로 보려는 것이다."""
    if peak <= 0:
        return " " * width
    filled = int(width * min(value / peak, 1.0))
    return "#" * filled + "-" * (width - filled)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("camera", nargs="?", help="top | wrist | 번호 | /dev/... 경로")
    ap.add_argument("--no-view", action="store_true", help="영상 창 없이 숫자만")
    args = ap.parse_args()

    cams = _cc.find_cams()
    if not cams:
        print("카메라가 없습니다. USB 를 꽂고:  ls /dev/v4l/by-id/")
        return 1

    path, label = resolve(args.camera, cams, read_cams_env())
    cap = _cc.open_cam(path)
    if not cap.isOpened():
        print(f"열기 실패: {path}")
        print("  다른 창이 카메라를 잡고 있거나 USB 접촉 불량입니다.")
        print("  방금 udevadm trigger 를 돌렸다면 몇 초 기다렸다 다시 해보세요.")
        return 1

    name = Path(path).name[:44]
    print()
    print("=" * 64)
    print(f" {label}  {name}" if label not in name else f" {name}")
    print("=" * 64)
    print(" 렌즈 링을 **천천히** 돌리세요. 숫자가 제일 커지는 지점이 초점입니다.")
    print(f" 기준: {GOOD:.0f} 이상이면 통과. 잘 맞은 카메라는 1000~2000 도 나옵니다.")
    print()
    print("   최고점을 지나면 막대가 줄어듭니다. 줄어들면 반대로 조금 되돌리세요.")

    # lerobot 이 opencv-python-headless 를 요구해서 imshow 가 없는 경우가 많다.
    # 숫자만으로도 초점은 맞출 수 있으므로, 창은 없이 가고 대안을 알려준다.
    show = not args.no_view and _cc.has_gui()
    if show:
        print("   r = 최고 기록 초기화     q / ESC = 끝내기")
        print("   ※ 키는 **영상 창**을 클릭한 뒤 누르세요.")
    else:
        if not args.no_view:
            print()
            print("   (OpenCV 에 GUI 가 없어 영상 창은 띄우지 않습니다. 숫자만 봐도 됩니다)")
            print("   화면도 같이 보고 싶으면 **다른 터미널**에서:")
            print(f"     ffplay -f v4l2 -input_format mjpeg -video_size 640x480 -i {path}")
        print("   Ctrl+C = 끝내기")
    print()

    smooth = 0.0   # 값이 프레임마다 튀어서 그냥 보면 어느 쪽으로 돌릴지 모른다
    peak = 0.0
    last = 0.0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("\n프레임을 못 읽었습니다 — USB 접촉을 확인하세요.")
                break

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            value = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            # 지수이동평균. 손으로 돌리는 속도에는 이 정도가 읽기 좋다.
            smooth = value if smooth == 0.0 else smooth * 0.7 + value * 0.3
            peak = max(peak, smooth)
            last = smooth

            mark = "OK " if smooth >= GOOD else "흐림"
            near = " <= 최고점 근처" if peak > 0 and smooth >= peak * 0.95 else ""
            sys.stdout.write(
                f"\r  선명도 {smooth:8.1f}  최고 {peak:8.1f}  "
                f"[{bar(smooth, peak)}] {mark}{near}   "
            )
            sys.stdout.flush()

            if show:
                shown = frame.copy()
                color = (0, 200, 0) if smooth >= GOOD else (0, 165, 255)
                cv2.putText(shown, f"{smooth:.0f}", (12, 46),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.4, color, 3)
                cv2.putText(shown, f"peak {peak:.0f}", (12, 76),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)
                cv2.imshow(f"focus: {label}", shown)
                key = cv2.waitKey(1) & 0xFF
            else:
                key = 255

            if key in (ord("q"), 27):
                break
            if key == ord("r"):
                peak = smooth
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        # headless 빌드에서는 destroyAllWindows 자체가 예외를 던진다.
        try:
            cv2.destroyAllWindows()
        except cv2.error:
            pass

    print()
    print()
    if last >= GOOD:
        print(f"  선명도 {last:.1f} — 기준({GOOD:.0f})을 넘겼습니다.")
        if peak > last * 1.15:
            print(f"  다만 최고 {peak:.1f} 까지 갔었습니다. 조금 더 맞출 여지가 있습니다.")
        print()
        print("  ★ 지금 **렌즈 링을 테이프로 고정**하세요. 안 하면 며칠 뒤 다시 풀립니다.")
        print("  ★ 이 숫자를 TASK.md 의 세팅 기록표에 적어두세요.")
        print("    나중에 '녹화 때와 같은가' 를 판단하는 유일한 근거가 됩니다.")
    else:
        print(f"  선명도 {last:.1f} — 아직 기준({GOOD:.0f}) 아래입니다.")
        print("  링이 끝까지 돌아갔는데도 안 오르면:")
        print("   · 렌즈에 먼지·지문이 없는지 (마른 천으로 닦기)")
        print("   · 피사체가 너무 가까운지 (최소 초점 거리보다 가까우면 안 맞습니다)")
        print("   · 조명이 너무 어두운지 (어두우면 선명도 값도 같이 떨어집니다)")
    print()
    print("  다음:  bash 03_check_cameras.sh --view   (top/wrist 를 정해 cams.env 저장)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
