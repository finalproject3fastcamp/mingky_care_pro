"""xacro로 펼친 Pinky URDF의 visual mesh를 하나의 GLB로 변환한다."""

from __future__ import annotations

import argparse
from pathlib import Path

from yourdfpy import URDF


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("urdf", type=Path, help="xacro로 펼친 URDF 파일")
    parser.add_argument("output", type=Path, help="저장할 GLB 파일")
    args = parser.parse_args()

    robot = URDF.load(
        args.urdf,
        build_scene_graph=True,
        load_meshes=True,
        build_collision_scene_graph=False,
    )
    if robot.scene is None:
        raise RuntimeError("URDF에서 visual scene을 만들지 못했습니다.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    robot.scene.export(args.output, file_type="glb")


if __name__ == "__main__":
    main()
