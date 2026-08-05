#!/usr/bin/env python3
"""SQLite에 저장된 Nav2 실험 결과를 비교·요약하는 개발 도구."""

from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", nargs="?", help="실험 SQLite 파일 경로")
    args = parser.parse_args()

    if args.db is None:
        parser.print_help()
        return 0

    parser.error("report.py는 아직 골격 단계입니다.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
