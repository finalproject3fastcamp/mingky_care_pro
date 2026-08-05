#!/usr/bin/env python3
"""기본 Nav2 YAML과 실험 프로파일을 깊게 합쳐 유효 설정을 만든다."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """overlay의 mapping 값만 재귀적으로 덮어쓴다."""
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_mapping(path: Path) -> dict[str, Any]:
    content = yaml.safe_load(path.read_text(encoding="utf-8"))
    if content is None:
        return {}
    if not isinstance(content, dict):
        raise ValueError(f"YAML 최상위는 mapping이어야 합니다: {path}")
    return content


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path, help="기본 nav2_params.yaml")
    parser.add_argument("--profile", required=True, type=Path, help="실험 프로파일 YAML")
    parser.add_argument("--output", required=True, type=Path, help="생성할 유효 YAML")
    args = parser.parse_args()

    merged = deep_merge(load_mapping(args.base), load_mapping(args.profile))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        yaml.safe_dump(merged, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
