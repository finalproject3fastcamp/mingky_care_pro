"""트레이 계수 — 백엔드와 조제 파트 사이의 계약.

`read_tray()` 는 실제 모드에서 조제 파트(`~/omx_pill_project`)의 `count_pills()`
를 **별도 프로세스**로 돌린다. 관제 백엔드 venv 에 lerobot·torch·cv2 가 없어
in-process import 가 불가능하기 때문이다 (`app/pharmacy.py` 모듈 docstring).

여기서 잠그는 것은 카메라도 로봇도 없이 확인할 수 있는 경계 넷이다.

  - 시뮬 모드는 프로세스를 아예 띄우지 않는다
  - stdout 에 뭐가 섞여 있어도 `TRAY_JSON` 한 줄만 읽는다
  - **조제 중에는 트레이를 읽지 않는다** — top 카메라는 두 번 열리지 않아서,
    읽으려 들면 돌고 있는 조제가 죽는다
  - 못 읽은 것을 개수 0 으로 바꾸지 않는다 (오류는 오류로 올린다)

진짜 카메라를 붙인 확인은 사람이 한다:
`~/venv/il/bin/python omx/web/count_tray.py --root ~/omx_pill_project`
"""

import asyncio
import sys
import textwrap
from pathlib import Path

import pytest

from app import pharmacy


@pytest.fixture(autouse=True)
def _isolated_state():
    """asyncio.Lock 은 처음 await 된 루프에 묶인다.

    테스트마다 `asyncio.run()` 이 새 루프를 만들므로 잠금도 새로 만들어 준다.
    운영에서는 uvicorn 루프 하나뿐이라 해당 없는 문제다.
    """
    pharmacy._TRAY_LOCK = asyncio.Lock()
    pharmacy._JOB_PROC = None
    yield
    pharmacy._JOB_PROC = None


def _wire(monkeypatch, tmp_path: Path, body: str) -> None:
    """실제 모드로 두되, 조제 파트 자리에 가짜 스크립트를 끼운다."""
    script = tmp_path / "fake_count_tray.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    (tmp_path / "pharmacy.py").write_text("", encoding="utf-8")

    monkeypatch.setattr(pharmacy, "REAL_MODE", True)
    monkeypatch.setattr(pharmacy, "_OMX_PROJECT", tmp_path)
    monkeypatch.setattr(pharmacy, "_OMX_PYTHON", Path(sys.executable))
    monkeypatch.setattr(pharmacy, "_TRAY_SCRIPT", script)


def test_simulation_mode_never_spawns_the_counter(monkeypatch):
    monkeypatch.setattr(pharmacy, "REAL_MODE", False)

    async def _boom():
        raise AssertionError("시뮬 모드가 카메라를 열려고 했다")

    monkeypatch.setattr(pharmacy, "_count_pills", _boom)

    tray = asyncio.run(pharmacy.read_tray())

    assert tray["모드"] == "시뮬레이션"
    assert tray["개수"] == {"red": 1, "yellow": 1, "green": 1}
    assert tray["시각"]


def test_only_the_marker_line_is_read(monkeypatch, tmp_path):
    # 조제 파트는 lerobot 로그를 stdout 으로 쏟는다. 그 사이에서 한 줄만 골라야 한다.
    _wire(monkeypatch, tmp_path, """
        import sys
        print("INFO lerobot 로딩 중")
        print("TRAY_JSON {\\"개수\\": {\\"red\\": 1}}")   # 옛 줄 — 뒤엣것이 이긴다
        print('TRAY_JSON {"개수": {"red": 2, "yellow": 1, "green": 3}}')
        sys.exit(0)
    """)

    tray = asyncio.run(pharmacy.read_tray())

    assert tray["모드"] == "실제"
    assert tray["개수"] == {"red": 2, "yellow": 1, "green": 3}
    assert "오류" not in tray


def test_black_camera_stays_an_error(monkeypatch, tmp_path):
    # 검은 화면을 "0개" 로 옮기면 화면이 '트레이가 비었다' 로 읽힌다.
    _wire(monkeypatch, tmp_path, """
        print('TRAY_JSON {"오류": "top 카메라가 검은 화면만 줍니다"}')
    """)

    tray = asyncio.run(pharmacy.read_tray())

    assert tray["오류"] == "top 카메라가 검은 화면만 줍니다"
    assert "개수" not in tray


def test_crash_reports_the_stderr_tail(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, """
        import sys
        print("ImportError: No module named 'lerobot'", file=sys.stderr)
        sys.exit(1)
    """)

    tray = asyncio.run(pharmacy.read_tray())

    assert "lerobot" in tray["오류"]
    assert "개수" not in tray


def test_hung_camera_is_cut_off(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, """
        import time
        time.sleep(30)
    """)
    monkeypatch.setattr(pharmacy, "TRAY_TIMEOUT", 1)

    tray = asyncio.run(pharmacy.read_tray())

    assert "1초" in tray["오류"]


def test_dispense_in_flight_blocks_the_camera(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, """
        raise AssertionError("조제 중에 카메라를 열었다")
    """)
    monkeypatch.setattr(pharmacy, "_JOB_PROC", object())

    tray = asyncio.run(pharmacy.read_tray())

    assert "조제 중" in tray["오류"]


def test_missing_omx_project_says_which_path(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, "pass")
    monkeypatch.setattr(pharmacy, "_OMX_PROJECT", tmp_path / "없는곳")

    tray = asyncio.run(pharmacy.read_tray())

    assert "OMX_PILL_ROOT" in tray["오류"]
    assert str(tmp_path / "없는곳") in tray["오류"]
