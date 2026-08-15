"""X-Actor 정규화.

`app/actor.py` 는 감사 로그의 입구다. 여기서 나온 (name, source) 가 그대로
control_audit 두 컬럼이 되므로, 이 모듈이 틀리면 두 가지가 동시에 무너진다 —
011 의 짝 제약에 걸려 INSERT 가 죽거나(제어 명령이 500 이 된다), 익명 행이
이름 있는 행으로 위장해 익명 비율이 거짓이 되거나.

그래서 확인하는 계약은 "잘 파싱한다" 가 아니라 아래 셋이다.

  1. 어떤 입력에도 예외를 던지지 않는다 — 감사가 제어를 막으면 안 된다.
  2. (name is None) 과 (source == 'absent') 가 항상 같이 움직인다 — 011 의 CHECK.
  3. 헤더로 오는 도중 망가진 것을 되살린다 — latin-1 디코딩과 길이 초과.
"""

from pathlib import Path

import pytest

from app import actor as actor_module
from app.actor import (
    MAX_ACTOR_LEN,
    SOURCE_ABSENT,
    SOURCE_HEADER,
    actor_from_header,
    actor_from_query,
    normalize,
)

MIGRATION = (
    Path(__file__).parents[2] / "database" / "migrations" / "011_control_audit.sql"
)

# 정상적인 이름이 아닌 것들. 전부 익명으로 접혀야 한다.
BLANK_INPUTS = [
    None,
    "",
    "   ",
    "\t\n",
    "\xa0",          # non-breaking space. 눈에는 공백인데 str.strip() 이 못 지운다
    "\x00\x07",      # 제어문자만
]


@pytest.mark.parametrize("raw", BLANK_INPUTS, ids=repr)
def test_blank_input_is_anonymous(raw):
    """이름이 아닌 것은 전부 익명이다.

    빈 문자열을 그대로 통과시키면 011 의 짝 제약은 통과하지만
    `WHERE actor IS NULL` 집계에서 빠져나간다. 익명 비율을 보려고 만든 장치가
    익명을 못 세게 된다.
    """
    result = normalize(raw)

    assert result.name is None
    assert result.source == SOURCE_ABSENT
    assert result.anonymous


@pytest.mark.parametrize(
    "raw",
    BLANK_INPUTS + [
        "jbh",
        "  Jang   Byeonghyeong  ",
        "가" * 200,
        "정민경".encode().decode("latin-1"),
        "\U0001f600",                 # 이모지
        "'; DROP TABLE control_audit; --",
    ],
    ids=repr,
)
def test_never_raises_and_keeps_the_pair_consistent(raw):
    """무슨 값이 와도 예외 없이, 짝이 어긋나지 않게 나온다.

    이 모듈이 던지는 예외는 곧 제어 명령의 500 이다. 조작자가 비상정지를
    못 누르는 경로가 여기서 시작된다.

    짝이 어긋나면 INSERT 가 011 의 CHECK 에 걸려 같은 결과가 된다. 검사를
    개별 케이스마다 쓰지 않고 여기 한 번에 몰아 두는 이유는, 입력을 추가할 때
    불변식 검사를 빠뜨리지 않게 하려는 것이다.
    """
    result = normalize(raw)

    assert (result.name is None) == (result.source == SOURCE_ABSENT)
    assert result.source in (SOURCE_HEADER, SOURCE_ABSENT)
    assert result.name is None or 0 < len(result.name) <= MAX_ACTOR_LEN


def test_latin1_header_is_recovered_as_utf8():
    """ASGI 가 latin-1 로 읽은 헤더에서 원래 이름을 되살린다.

    HTTP 헤더에는 인코딩 협상이 없어서 Starlette 은 바이트를 latin-1 로
    디코딩한다. 브라우저가 UTF-8 로 실어 보낸 한글 이름은 서버에
    'ì •ë¯¼ê²½' 으로 도착한다. 이름이 깨져 남는 감사 로그는 없는 것과 같다.

    본문(JSON)으로 받았으면 없었을 문제다. 헤더 방식의 실제 비용이 이거다.
    """
    mangled = "정민경".encode().decode("latin-1")

    assert normalize(mangled).name == "정민경"


def test_ascii_survives_the_recovery_untouched():
    """영문 아이디만 쓰는 배포에서는 복구가 항등이어야 한다.

    ASCII 는 latin-1 과 UTF-8 양쪽의 부분집합이다. 여기가 깨지면 되살리려다
    멀쩡한 이름을 망가뜨리고 있다는 뜻이다.
    """
    assert normalize("nurse-02").name == "nurse-02"


def test_control_characters_become_spaces_instead_of_vanishing():
    """개행을 지우지 않고 공백으로 바꾼다.

    지우면 'a\\nb' 가 'ab' 로 붙어 없던 이름이 만들어진다. 그대로 통과시키면
    감사 로그 한 줄이 두 줄이 되어, 로그를 읽는 쪽이 위조된 항목을 진짜로
    읽는다 — 아래 입력이 정확히 그 시도다.
    """
    injected = "jbh\n2026-08-15 ERROR estop by admin"

    name = normalize(injected).name

    assert "\n" not in name
    assert name == "jbh 2026-08-15 ERROR estop by admin"


def test_long_korean_name_is_truncated_by_characters_not_bytes():
    """50 은 문자 수다. 한글이 17자에서 잘리면 바이트로 세고 있는 것이다.

    PostgreSQL 의 VARCHAR(n) 도 문자를 세므로 파이썬 len() 과 기준이 같다.
    바이트로 자르면 한글 이름만 조용히 짧아지고, 최악의 경우 잘린 자리에서
    UTF-8 시퀀스가 깨진다.
    """
    result = normalize("가" * 60)

    assert len(result.name) == MAX_ACTOR_LEN
    assert result.name == "가" * MAX_ACTOR_LEN
    # 잘렸어도 이름은 왔다. absent 로 강등하면 익명 비율이 부풀려진다.
    assert result.source == SOURCE_HEADER


def test_oversized_name_is_truncated_rather_than_rejected():
    """길이로 거부하지 않는다.

    거부하지 않기로 정해놓고 길이에서만 막으면, DB 가
    StringDataRightTruncation 을 던져 제어 명령이 500 이 된다. 결과적으로
    헤더를 길게 보낸 사람은 로봇을 세울 수 없다.
    """
    assert normalize("x" * (MAX_ACTOR_LEN + 1)).source == SOURCE_HEADER


def test_header_and_query_paths_agree():
    """전달 수단은 갈려도 규칙은 하나다.

    teleop 만 쿼리인 것은 브라우저 WebSocket 이 커스텀 헤더를 못 싣기
    때문이다. 두 경로가 다른 정규화를 갖게 되면 같은 사람이 HTTP 와 teleop
    에서 다른 이름으로 기록된다.
    """
    for raw in (None, "  jbh  ", "가" * 60):
        assert actor_from_header(raw) == actor_from_query(raw)


def test_max_length_matches_the_column_it_writes_into():
    """상수와 스키마가 갈라지지 않게 묶어둔다.

    011 의 VARCHAR 를 늘리면서 이 상수를 안 고치면 이름이 필요 이상으로
    잘리고, 반대로 상수만 늘리면 INSERT 가 죽는다. 어느 쪽도 배포 전에는
    안 보인다.
    """
    sql = MIGRATION.read_text(encoding="utf-8")

    assert f"actor        VARCHAR({MAX_ACTOR_LEN})" in sql


def test_anonymous_singleton_is_not_mutated_by_normalize():
    """익명 결과를 공유 상수로 돌려주므로 불변이어야 한다.

    frozen dataclass 라 지금은 깨질 수 없지만, 나중에 필드를 추가하면서
    frozen 을 떼면 한 요청이 고친 값이 다음 요청에 그대로 나간다.
    """
    normalize(None)

    assert actor_module.ANONYMOUS.name is None
    assert actor_module.ANONYMOUS.source == SOURCE_ABSENT
