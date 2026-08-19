#!/usr/bin/env python
"""꽂혀 있는 OMX 팔을 찾아 팔로워/리더를 구분한다. (02_find_ports.sh 가 부른다)

팔로워/리더는 USB 장치 이름으로는 절대 구분할 수 없다. **모터 ID** 로 판별한다 —
팔로워 11~16, 리더 1~6 (OMX 공장 기본값).

  python omx_scan.py            # 사람이 읽는 표
  python omx_scan.py --export   # 셸이 읽는 형식 (포트/시리얼)
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys

FOLLOWER_IDS = {11, 12, 13, 14, 15, 16}
LEADER_IDS = {1, 2, 3, 4, 5, 6}
BAUDS = [1_000_000, 57_600]


def usb_attr(dev: str, attr: str) -> str:
    """udev 규칙에 쓸 값을 읽는다 (시리얼·벤더ID)."""
    try:
        out = subprocess.run(
            ["udevadm", "info", "-a", "-n", dev],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return ""
    for line in out.splitlines():
        if f"{{{attr}}}" in line and "==" in line:
            return line.split('=="')[-1].strip().rstrip('"')
    return ""


def is_permission_error(err: str) -> bool:
    """포트를 못 연 이유가 권한인가. 케이블 문제와 구분하려고 본다."""
    return bool(err) and ("Permission denied" in err or "Errno 13" in err)


def in_dialout() -> bool:
    """지금 이 프로세스가 dialout 그룹을 갖고 있나.

    usermod 로 등록해도 **로그인할 때** 반영되므로, /etc/group 에 있는 것과
    지금 셸이 갖고 있는 것은 다를 수 있다. 여기서 보는 것은 후자다.
    """
    try:
        import grp

        return grp.getgrnam("dialout").gr_gid in os.getgroups()
    except Exception:
        return False


def scan() -> list[dict]:
    from dynamixel_sdk import PacketHandler, PortHandler

    found = []
    for dev in sorted(glob.glob("/dev/ttyACM*")):
        entry = {"dev": dev, "ids": [], "baud": None, "role": "unknown", "error": ""}
        for baud in BAUDS:
            port = PortHandler(dev)
            ids = []
            try:
                if not port.openPort() or not port.setBaudRate(baud):
                    entry["error"] = "포트를 열 수 없음 (권한? 다른 프로그램이 잡고 있음?)"
                    continue
                entry["error"] = ""
                # broadcastPing 반환값은 {id: [model, firmware]}
                data, _ = PacketHandler(2.0).broadcastPing(port)
                ids = sorted(data.keys()) if data else []
            except Exception as e:
                entry["error"] = str(e)
            finally:
                try:
                    port.closePort()
                except Exception:
                    pass
            if ids:
                entry["ids"], entry["baud"] = ids, baud
                break

        got = set(entry["ids"])
        if got and got <= FOLLOWER_IDS:
            entry["role"] = "follower"
        elif got and got <= LEADER_IDS:
            entry["role"] = "leader"
        entry["serial"] = usb_attr(dev, "serial")
        entry["vendor"] = usb_attr(dev, "idVendor")
        found.append(entry)
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", action="store_true")
    args = ap.parse_args()

    arms = scan()
    follower = next((a for a in arms if a["role"] == "follower"), None)
    leader = next((a for a in arms if a["role"] == "leader"), None)

    if args.export:
        for role, a in (("FOLLOWER", follower), ("LEADER", leader)):
            if a:
                print(f"{role}_PORT={a['dev']}")
                print(f"{role}_SERIAL={a['serial']}")
                print(f"{role}_VENDOR={a['vendor']}")
        return 0 if (follower and leader) else 1

    print("=" * 64)
    print(" 팔 찾기 (팔로워 = 모터 11~16, 리더 = 모터 1~6)")
    print("=" * 64)
    if not arms:
        print("  /dev/ttyACM* 가 없습니다.")
        print("  · USB 를 꽂고 로봇 전원(어댑터)을 켰는지 확인")
        print("  · 꽂았는데도 안 보이면:  groups | grep dialout   (없으면 로그아웃/로그인)")
        return 1

    for a in arms:
        ids = ",".join(str(i) for i in a["ids"]) or "응답 없음"
        baud = f"{a['baud']}bps" if a["baud"] else "-"
        print(f"  {a['dev']:15s} {a['role']:9s} ids=[{ids}]  {baud}")
        if a["serial"]:
            print(f"      serial={a['serial']}  vendor={a['vendor']}")
        if a["error"]:
            print(f"      ! {a['error']}")

    print()
    if follower and leader:
        print("  두 팔 모두 찾았습니다.")
        return 0
    if not follower:
        print("  ! 팔로워(11~16)를 못 찾았습니다.")
    if not leader:
        print("  ! 리더(1~6)를 못 찾았습니다.")

    # 권한 문제를 케이블 문제로 안내하면 멀쩡한 하드웨어를 뜯어보게 된다.
    # 제일 흔한 원인이 dialout 미적용이므로 먼저 가려낸다.
    if any(is_permission_error(a["error"]) for a in arms):
        print()
        print("    원인은 케이블이 아니라 **시리얼 포트 권한**입니다 (Permission denied).")
        if not in_dialout():
            print("    지금 이 셸은 dialout 그룹이 아닙니다. 아래 중 하나를 하세요:")
            print()
            print("      · 이 터미널에만 적용:   newgrp dialout   (그 뒤에 다시 실행)")
            print("      · 영구 적용:            로그아웃 → 로그인")
            print()
            print("    그룹에 아예 등록이 안 되어 있다면 먼저:")
            print(f"      sudo usermod -aG dialout {os.environ.get('USER', '$USER')}")
        else:
            print("    이 셸은 dialout 그룹인데도 못 엽니다 — 다른 프로그램이 포트를 잡고")
            print("    있는지 보세요 (녹화·평가·텔레옵 창을 먼저 끕니다).")
        return 1

    print("    응답이 없는 포트가 있으면 전원과 모터 사이 케이블을 먼저 의심하세요.")
    print("    (우리 쪽에서 '모터가 죽은 것처럼 보인' 경우는 전부 케이블이었습니다)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
