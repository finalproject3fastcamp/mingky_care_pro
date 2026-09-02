"""데모 스택 스크립트가 문법적으로 성립하는지 본다.

## 왜 이것만 보나

`tools/demo_stack` 은 관제 서버에서 systemd 로 도는 별개 프로그램이라 백엔드
테스트가 import 하지 않는다. 그래서 여기 문법 오류가 나면 **배포한 뒤 서비스가
재시작 루프를 도는 것으로** 처음 알게 된다. 상시 데모에서 그건 화면이 조용히
빈 채로 남는다는 뜻이다.

import 가 아니라 `py_compile` 인 것은 `fake_teleop.py` 가 `websockets` 를 module
level 에서 부르기 때문이다. 그 의존성은 데모 스택 전용(tools/demo_stack/
requirements.txt)이고, 백엔드 CI 에 끌어올 이유가 없다. 컴파일은 import 를
실행하지 않으므로 의존성 없이 문법만 본다.

동작까지 보려면 서버에서 돌려야 한다 — 이 파일이 잡는 것은 오타 수준이다.
"""

import py_compile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_STACK = REPO_ROOT / "tools" / "demo_stack"

SCRIPTS = sorted(DEMO_STACK.glob("*.py"))


def test_scripts_exist():
    """glob 이 비면 아래 파라미터 테스트가 조용히 0건이 된다."""
    assert SCRIPTS


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.stem)
def test_demo_stack_script_compiles(path, tmp_path):
    py_compile.compile(
        str(path), cfile=str(tmp_path / f"{path.stem}.pyc"), doraise=True)
