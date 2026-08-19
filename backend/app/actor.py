"""제어 명령을 실행한 사람 — 요청에서 읽어 정규화한다.

`control_audit` 에 들어갈 (actor, actor_source) 두 컬럼을 만드는 유일한 곳이다.
읽는 자리가 늘어나도 규칙은 여기 하나로 남는다.

## 왜 명령 본문이 아니라 헤더인가

actor 는 명령의 속성이 아니라 요청의 부속물이다. 로봇은 누가 눌렀는지 알
필요가 없고, `POST /robots/{id}/arm` 처럼 **본문이 아예 없는** 제어 경로도
있다. 본문에 넣으면 그런 엔드포인트마다 없던 바디를 만들어야 한다.
헤더면 의존성 하나를 더 매다는 것으로 감사 범위를 넓힐 수 있다.

## 왜 없다고 거부하지 않는가

422 로 막으면 감사 문제가 가용성 문제가 된다. 프론트가 헤더를 빠뜨리는 버그
하나로 조작자가 비상정지를 못 누른다. 게다가 인증이 없으므로(011 참고) 거부는
정직한 누락만 막고 위조는 못 막는다 — 지키려던 것을 지키지도 못하면서 제어만
잃는다.

그래서 이 모듈은 **어떤 입력에도 예외를 던지지 않는다.** 못 쓰는 값은 익명으로
접는다. 길이 초과를 DB 까지 흘려보내면 `StringDataRightTruncation` 이 500 이
되어, 거부하지 않기로 해놓고 길이로 거부하는 꼴이 된다.

깎이는 것은 귀속(attribution)뿐이다. actor 가 비어도 감사 행은 남고, §1.1 은
"사람이 손댔는가" 만 묻지 "누가" 를 묻지 않으므로 SLO 판정은 온전하다.

익명 자체는 여기서 로그하지 않는다. 무슨 명령이었는지는 적재 지점이 알고,
누적된 익명 비율은 fleet 탭이 드러낸다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import Header, Query

log = logging.getLogger("mingky")

HEADER_NAME = "X-Actor"

# 011 의 actor VARCHAR(50).
#
# PostgreSQL 의 VARCHAR(n) 은 바이트가 아니라 **문자** 수를 센다. 한글 이름이
# 3바이트라고 17자에서 잘리지 않으므로 파이썬 len() 과 기준이 같다.
MAX_ACTOR_LEN = 50

SOURCE_HEADER = "header"
SOURCE_ABSENT = "absent"


@dataclass(frozen=True)
class Actor:
    """감사 행에 그대로 들어가는 두 값.

    011 이 `CHECK ((actor IS NULL) = (actor_source = 'absent'))` 로 이 짝을
    강제한다. 여기서 만든 것만 넣으면 그 제약에 걸릴 일이 없다.
    """

    name: str | None
    source: str

    @property
    def anonymous(self) -> bool:
        return self.name is None


ANONYMOUS = Actor(name=None, source=SOURCE_ABSENT)


def _recover_utf8(raw: str) -> str:
    """ASGI 가 latin-1 로 디코딩한 헤더에서 원래 UTF-8 을 되살린다.

    HTTP 헤더에는 인코딩 협상이 없어서 Starlette 은 바이트를 latin-1 로 읽는다.
    브라우저가 '정민경' 을 UTF-8 로 실어 보내면 서버에는 'ì •ë¯¼ê²½' 으로
    도착한다. 감사 로그에 이름이 깨져 남으면 기록이 있으나 마나다.

    ASCII 는 latin-1 과 UTF-8 양쪽의 부분집합이라 왕복해도 그대로다. 영문
    아이디만 쓰는 배포에서는 이 함수가 항등이 된다.
    """
    try:
        return raw.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        # 이미 제대로 디코딩됐거나 UTF-8 이 아니다. 건드리지 않는다 —
        # 되살리려다 멀쩡한 이름을 깨는 쪽이 나쁘다.
        return raw


def normalize(raw: str | None) -> Actor:
    """헤더·쿼리 문자열 하나를 감사 가능한 (name, source) 로 만든다.

    예외를 던지지 않는다. 판단이 서지 않는 입력은 전부 익명이다.
    """
    if raw is None:
        return ANONYMOUS

    # 제어문자를 지우지 않고 공백으로 바꾼 뒤 접는다. 지우면 "a\nb" 가 "ab"
    # 로 붙어 없던 이름이 만들어진다. 개행이 그대로 통과하면 로그 한 줄이
    # 두 줄이 되어 감사 기록을 읽는 쪽이 속는다.
    cleaned = "".join(
        ch if ch.isprintable() else " " for ch in _recover_utf8(raw))
    name = " ".join(cleaned.split())

    # 공백만 보낸 것은 안 보낸 것과 같다. 빈 문자열을 그대로 넣으면 011 의
    # 짝 제약은 통과하지만 익명 비율에서는 빠져나가 구멍이 안 보이게 된다.
    if not name:
        return ANONYMOUS

    if len(name) > MAX_ACTOR_LEN:
        # 잘라서라도 넣는다. 여기서 막으면 제어 명령이 실패한다.
        log.warning("actor 이름이 %d자를 넘어 잘랐습니다: %r",
                    MAX_ACTOR_LEN, name[:MAX_ACTOR_LEN])
        name = name[:MAX_ACTOR_LEN]

    return Actor(name=name, source=SOURCE_HEADER)


def actor_from_header(
    x_actor: str | None = Header(default=None, alias=HEADER_NAME),
) -> Actor:
    """HTTP 제어 경로용 의존성. `Depends(actor_from_header)` 로 붙인다."""
    return normalize(x_actor)


def actor_from_query(actor: str | None = Query(default=None)) -> Actor:
    """teleop WebSocket 용.

    브라우저 `new WebSocket(url)` 에는 커스텀 헤더를 실을 방법이 없다. 그래서
    이 경로만 쿼리로 받는다 — 전달 수단은 갈리지만 정규화 규칙은 위와 같은
    함수를 지나므로, 두 경로에서 다른 값이 나오는 일은 없다.

    쿼리는 URL 에 남는다(브라우저 히스토리·프록시 로그). 자기신고 이름이라
    비밀이 아니지만, 나중에 인증이 붙어 토큰을 싣게 되면 이 자리는 못 쓴다.
    """
    return normalize(actor)
