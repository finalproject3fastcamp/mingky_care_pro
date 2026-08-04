"""이벤트 코드 정본을 앱 전체에서 공유한다.

main.py 의 lifespan 이 기동 시 한 번 로드하고, 라우터는 의존성으로 받는다.
라우터가 직접 파일을 읽으면 요청마다 디스크를 건드리게 된다.
"""

from .event_codes import EventCodeRegistry

_registry: EventCodeRegistry | None = None


def load() -> EventCodeRegistry:
    global _registry
    _registry = EventCodeRegistry.load()
    return _registry


def get_registry() -> EventCodeRegistry:
    if _registry is None:
        raise RuntimeError("이벤트 코드 정본이 아직 로드되지 않았습니다.")
    return _registry
