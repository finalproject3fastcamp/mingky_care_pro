#!/usr/bin/env python
"""[3단계] 카메라 찾기·확인하고 어느 게 top/wrist 인지 정한다.

  python 03_check_cameras.py            # 목록 + 상태 점검
  python 03_check_cameras.py --view     # 화면으로 직접 보고 고르기 (권장)

**장치 이름으로 top/wrist 를 추정하지 마세요.** 우리 쪽에서 이름만 보고 반대로 잡아둔 걸
이틀 뒤에야 화면을 보고 발견했습니다. `--view` 로 실제 화면을 보고 정하세요.

정한 결과는 `cams.env` 에 저장되고, 이후 스크립트(04~07)가 그 파일을 읽습니다.

경로는 반드시 `/dev/v4l/by-id/...` 를 씁니다 — `/dev/videoN` 번호는 재연결마다 바뀝니다
(우리는 하루에 video4 → video5 → video3 으로 옮겨 다닌 적이 있습니다).
"""

from __future__ import annotations

import argparse
import glob
import re
import subprocess
from pathlib import Path

import cv2

HERE = Path(__file__).resolve().parent
ENV = HERE / "cams.env"
# 노트북 내장 웹캠은 작업용이 아니라 목록에서 뺀다 (필요하면 이 목록을 고친다).
# by-id 이름의 일부만 적으면 된다. 내 노트북 것을 모르겠으면 ls /dev/v4l/by-id/ 로 보고,
# 외장 카메라를 다 뽑았을 때 남는 것이 내장캠이다.
SKIP = ("HP_True_Vision", "Integrated_Camera", "Integrated_Webcam", "Bison")


def find_cams() -> list[str]:
    return [p for p in sorted(glob.glob("/dev/v4l/by-id/*-video-index0"))
            if not any(s in p for s in SKIP)]


def has_gui() -> bool:
    """cv2.imshow 를 쓸 수 있나.

    lerobot 이 `opencv-python-headless` 를 요구하기 때문에 보통 **없다.**
    headless 빌드에서 imshow/destroyAllWindows 를 부르면 예외가 난다.
    없으면 ffplay 로 대신 띄운다 (ffmpeg 는 01_install.sh 가 깔아둔다).
    """
    try:
        m = re.search(r"^\s*GUI:\s*(\S+)", cv2.getBuildInformation(), re.M)
        return bool(m) and m.group(1).upper() != "NONE"
    except Exception:
        return False


def ffplay_view(cams: list[str]) -> None:
    """cv2 GUI 가 없을 때 ffplay 로 카메라를 띄운다. 창을 다 띄우고 엔터를 기다린다."""
    procs = []
    for i, p in enumerate(cams):
        procs.append(subprocess.Popen(
            ["ffplay", "-hide_banner", "-loglevel", "error",
             "-f", "v4l2", "-input_format", "mjpeg", "-video_size", "640x480",
             "-i", p, "-window_title", f"[{i}] {Path(p).name[:40]}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ))
    try:
        input("창을 다 보셨으면 여기에 엔터를 누르세요 ")
    finally:
        for pr in procs:
            pr.terminate()
        for pr in procs:
            try:
                pr.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pr.kill()


def open_cam(path: str):
    """V4L2 + MJPG 로 연다. 기본 FFMPEG 백엔드로 열면 해상도·포맷 설정이 안 먹는다."""
    cap = cv2.VideoCapture(path, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    return cap


def probe(path: str) -> dict:
    info = {"path": path, "ok": False}
    cap = open_cam(path)
    try:
        if cap.isOpened():
            frame = None
            for _ in range(5):          # 첫 프레임은 노출이 안 잡혀 있어 버린다
                ok, frame = cap.read()
            if frame is not None:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                info.update(
                    ok=True,
                    wh=(frame.shape[1], frame.shape[0]),
                    bright=float(gray.mean()),
                    focus=float(cv2.Laplacian(gray, cv2.CV_64F).var()),
                )
    finally:
        cap.release()
    return info


def diagnose(info: dict) -> str:
    """증상 → 원인. 우리가 실제로 겪은 것들이다."""
    if not info["ok"]:
        return "열기 실패 — 다른 창이 카메라를 잡고 있거나 USB 접촉 불량"
    if info["bright"] < 5:
        return "화면이 전부 검정 — USB 가 반쯤 빠졌거나 접촉 불량. 다시 꽂으세요"
    if info["focus"] < 100:
        return "많이 흐림 — 렌즈 링을 돌려 초점을 맞추세요"
    return "정상"


def view(cams: list[str]) -> None:
    """두 대를 나란히 띄우고 사람이 직접 top/wrist 를 고른다."""
    print()
    print("창이 뜹니다. 각 창 제목에 번호가 있습니다.")
    print("  top   = 작업대 전체를 위에서 내려보는 고정 카메라")
    print("  wrist = 로봇 팔에 달린 카메라 (팔을 움직이면 화면이 같이 움직임)")
    print("  → 어느 쪽이 wrist 인지 헷갈리면 **팔을 손으로 살짝 움직여 보세요.**")
    print("     같이 움직이는 화면이 wrist 입니다.")

    if not has_gui():
        # opencv-python-headless 라 imshow 가 없다. ffplay 로 띄운다.
        print()
        print("  (OpenCV 에 GUI 가 없어 ffplay 로 띄웁니다)")
        ffplay_view(cams)
        return

    caps = [(p, open_cam(p)) for p in cams]
    print("  q 또는 ESC = 창 닫기 → 그다음 터미널에서 번호를 고릅니다")
    try:
        while True:
            for i, (p, cap) in enumerate(caps):
                ok, frame = cap.read()
                if ok:
                    cv2.imshow(f"[{i}] {Path(p).name[:40]}", frame)
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                break
    finally:
        for _, cap in caps:
            cap.release()
        try:
            cv2.destroyAllWindows()
        except cv2.error:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--view", action="store_true", help="화면을 보고 top/wrist 를 고른다")
    args = ap.parse_args()

    cams = find_cams()
    print("=" * 64)
    print(" 카메라 (노트북 내장캠 제외)")
    print("=" * 64)
    if not cams:
        print("  by-id 경로에 외장 카메라가 없습니다. USB 를 꽂으세요.")
        print("  확인:  ls /dev/v4l/by-id/")
        return 1

    infos = []
    for i, p in enumerate(cams):
        info = probe(p)
        infos.append(info)
        wh = f"{info['wh'][0]}x{info['wh'][1]}" if info["ok"] else "-"
        extra = (f"밝기 {info['bright']:5.1f}  선명도 {info['focus']:7.1f}"
                 if info["ok"] else "")
        print(f"  [{i}] {wh:9s} {extra}")
        print(f"      {p}")
        print(f"      → {diagnose(info)}")
    print()

    if not args.view:
        print("어느 게 top/wrist 인지 화면을 보고 정하려면:")
        print("  python 03_check_cameras.py --view")
        return 0

    view(cams)

    def ask(label: str) -> str:
        while True:
            s = input(f"{label} 카메라 번호? (없으면 엔터) ").strip()
            if s == "":
                return ""
            if s.isdigit() and int(s) < len(cams):
                return cams[int(s)]
            print("  그런 번호가 없습니다.")

    top = ask("top")
    wrist = ask("wrist")
    if not top and not wrist:
        print("아무것도 고르지 않아 저장하지 않았습니다.")
        return 1

    lines = ["# 03_check_cameras.py 가 만든 파일. 04~07 스크립트가 읽습니다.",
             "# 카메라를 다른 USB 포트로 옮겨도 by-id 경로는 그대로라 다시 안 만들어도 됩니다."]
    if top:
        lines.append(f'TOP_CAM="{top}"')
    if wrist:
        lines.append(f'WRIST_CAM="{wrist}"')
    ENV.write_text("\n".join(lines) + "\n")
    print()
    print(f"저장했습니다 → {ENV}")
    print("다음: bash 04_teleop.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
