#!/usr/bin/env python
"""트레이 알약 개수를 JSON 한 줄로 찍는다 — 관제 백엔드가 subprocess 로 부른다.

## 왜 별도 프로세스인가

트레이를 세는 것은 실제 조제 파트(`~/omx_pill_project/pharmacy.py`)의
`count_pills()` 다. 그런데 그 모듈은 import 만 해도 `prescribe` · `run_policy` 를
끌어오고, 그것들이 다시 lerobot · torch · cv2 를 끌어온다. 관제 백엔드
(`backend/requirements.txt`) 에는 그 스택이 없다 — FastAPI · asyncpg 뿐이다.

그래서 조제(`run.sh`) 와 같은 방식을 쓴다. **il venv 파이썬으로 이 스크립트를
띄우고, stdout 의 JSON 한 줄만 읽는다.** 백엔드는 카메라도 lerobot 도 모르고,
조제 파트는 웹을 모른다.

## 계약

마지막 줄에 `TRAY_JSON ` 로 시작하는 한 줄을 찍는다. 앞줄에 lerobot 로그가
섞여도 백엔드가 이 접두어로 골라낸다.

    TRAY_JSON {"개수": {"red": 2, "yellow": 1, "green": 3}}
    TRAY_JSON {"오류": "top 카메라가 검은 화면만 줍니다 — USB 를 다시 꽂아 주세요"}

**빈 dict 를 개수 0 으로 바꾸지 않는다.** `count_pills()` 는 카메라가 검은
화면만 줄 때 `{}` 를 돌려준다 (자동노출이 잡히기 전 프레임). 그것을 "알약이
하나도 없다" 로 옮기면 화면이 트레이가 빈 것처럼 보이고, 무작위 처방이
"트레이가 비었습니다" 로 잘못 막힌다.

## 사용

    ~/venv/il/bin/python count_tray.py --root ~/omx_pill_project --frames 5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MARKER = "TRAY_JSON"


def _emit(payload: dict) -> None:
    print(f"{MARKER} {json.dumps(payload, ensure_ascii=False)}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="트레이의 색깔별 알약 개수")
    ap.add_argument("--root", required=True, help="조제 파트 경로 (pharmacy.py 가 있는 곳)")
    ap.add_argument("--frames", type=int, default=5, help="최빈값을 낼 장 수")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not (root / "pharmacy.py").is_file():
        _emit({"오류": f"조제 파트를 찾지 못했습니다: {root}/pharmacy.py"})
        return 0

    sys.path.insert(0, str(root))
    try:
        from pharmacy import count_pills  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        _emit({"오류": f"조제 파트를 불러오지 못했습니다 — {type(e).__name__}: {e}"})
        return 0

    try:
        n = count_pills(args.frames)
    except Exception as e:  # noqa: BLE001
        _emit({"오류": f"트레이를 읽지 못했습니다 — {type(e).__name__}: {e}"})
        return 0

    if not n:
        _emit({"오류": "top 카메라가 검은 화면만 줍니다 — USB 를 다시 꽂아 주세요"})
        return 0

    _emit({"개수": {c: int(v) for c, v in n.items()}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
