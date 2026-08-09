"""대시보드가 로봇 위치를 그릴 바탕 지도.

    GET /map        해상도·원점 (픽셀 ↔ 미터 변환에 필요)
    GET /map/image  PNG

## 왜 PGM 을 그대로 안 주나

Nav2 가 쓰는 지도는 PGM 이고 브라우저가 못 읽는다. 그렇다고 이미지 라이브러리를
새로 넣지 않았다 — 회색조 PNG 하나를 만드는 데 필요한 것은 zlib 뿐이고, 그건
표준 라이브러리다. 배포 이미지를 키우지 않으려고 직접 만든다.

## 왜 파일에서 읽나

지도는 로봇마다 다르지 않고 자주 바뀌지도 않는다. DB 에 넣으면 마이그레이션과
적재 절차가 생기는데, 저장소에 이미 있는 파일을 그대로 쓰면 그럴 일이 없다.
지도를 바꾸면 재배포한다 — 어차피 로봇 쪽 map_server 도 같이 바꿔야 한다.
"""

from __future__ import annotations

import os
import struct
import zlib
from functools import lru_cache
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

router = APIRouter(tags=["map"])

# 이미지 옆에 yaml 이 같이 있는 Nav2 규약을 따른다.
MAP_YAML = Path(os.environ.get(
    "MAP_YAML_FILE", "/srv/mingky/map/yun_map_highres_clean.yaml"))


class MapMeta(BaseModel):
    """픽셀 좌표를 미터로 옮기는 데 필요한 값들.

    화면은 로봇 pose(미터)를 지도 위 픽셀로 바꿔야 한다. 그 변환에 필요한 것이
    resolution 과 origin 이라 함께 내려준다. 규약은 Nav2 map_server 와 같다.
    """

    width: int
    height: int
    resolution: float
    origin: list[float]
    image_url: str = "/map/image"


def _read_pgm(path: Path) -> tuple[int, int, bytes]:
    """P5(binary) PGM 만 읽는다. Nav2 가 내보내는 형식이다."""
    raw = path.read_bytes()
    if not raw.startswith(b"P5"):
        raise ValueError("P5 PGM 이 아니다")

    # 헤더는 공백으로 구분된 토큰 3개(width, height, maxval)다.
    # '#' 주석이 섞일 수 있어 한 토큰씩 건너뛰며 읽는다.
    fields: list[int] = []
    i = 2
    while len(fields) < 3:
        while i < len(raw) and raw[i : i + 1].isspace():
            i += 1
        if raw[i : i + 1] == b"#":
            while i < len(raw) and raw[i : i + 1] not in (b"\n", b"\r"):
                i += 1
            continue
        start = i
        while i < len(raw) and not raw[i : i + 1].isspace():
            i += 1
        fields.append(int(raw[start:i]))
    i += 1  # maxval 뒤 공백 한 칸

    width, height, _maxval = fields
    return width, height, raw[i : i + width * height]


def _png(width: int, height: int, gray: bytes) -> bytes:
    """8bit 회색조 PNG. 각 행 앞에 필터 바이트 0 을 붙이는 게 전부다."""
    rows = b"".join(
        b"\x00" + gray[y * width : (y + 1) * width] for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 6))
        + chunk(b"IEND", b"")
    )


@lru_cache(maxsize=1)
def _load() -> tuple[MapMeta, bytes]:
    """한 번 읽어 메모리에 둔다. 파일이 바뀌면 재배포한다."""
    if not MAP_YAML.exists():
        raise FileNotFoundError(MAP_YAML)

    meta = yaml.safe_load(MAP_YAML.read_text())
    width, height, gray = _read_pgm(MAP_YAML.parent / meta["image"])

    return (
        MapMeta(
            width=width,
            height=height,
            resolution=float(meta["resolution"]),
            origin=[float(v) for v in meta["origin"]],
        ),
        _png(width, height, gray),
    )


@router.get("/map", response_model=MapMeta)
async def get_map_meta() -> MapMeta:
    try:
        return _load()[0]
    except (FileNotFoundError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=404, detail=f"지도를 읽지 못했다: {exc}")


@router.get("/map/image")
async def get_map_image() -> Response:
    try:
        png = _load()[1]
    except (FileNotFoundError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=404, detail=f"지도를 읽지 못했다: {exc}")

    # 지도는 재배포 전까지 안 바뀐다. 매번 받을 이유가 없다.
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})
