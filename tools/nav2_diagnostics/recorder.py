#!/usr/bin/env python3
"""Nav2 실험 ROS 토픽을 SQLite에 기록하는 개발용 노드.

구현 예정: run metadata/parameter snapshot/action 상태/주행 요약 수치 기록.
원본 costmap·scan 데이터는 SQLite가 아닌 선택적 rosbag으로 남긴다.
"""

from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="기록할 SQLite 파일 경로")
    parser.add_argument("--run-id", help="실험 세션 ID")
    parser.add_argument("--help-only", action="store_true", help="구현 상태만 출력")
    args = parser.parse_args()

    if args.help_only:
        print("Nav2 diagnostics recorder: skeleton only")
        return 0

    parser.error("recorder.py는 아직 골격 단계입니다. --help-only로 확인하세요.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
