"""config/event_codes.yaml 로드와 검증.

그 파일이 발행 가능한 event_code 의 정본이다.
payload 를 JSONB 로 저장하는 대신 DB 가 내용을 검증하지 않기로 했으므로,
검증 책임이 이 모듈과 yaml 에 있다.

로봇 쪽(mingky_guide_manager/event_publisher.py)도 같은 파일을 읽는다.
발행 측에서 한 번 거르지만, DB 로 들어오는 마지막 관문은 여기다.
"""

import os
from pathlib import Path

import yaml

# 수집 서버가 미등록 코드를 받았을 때 추가로 남기는 코드.
UNKNOWN_CODE = "system.unknown_event_code"

_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "event_codes.yaml"


class EventCodeRegistry:
    """yaml 을 읽어 코드 조회를 제공한다."""

    def __init__(self, codes: dict, source: Path):
        self._codes = codes
        self.source = source

    @classmethod
    def load(cls, explicit: str = "") -> "EventCodeRegistry":
        path = Path(explicit or os.environ.get("EVENT_CODES_FILE") or _DEFAULT_PATH)
        if not path.is_file():
            raise FileNotFoundError(
                f"이벤트 코드 정본을 찾지 못했습니다: {path}. "
                "EVENT_CODES_FILE 환경 변수로 경로를 지정하세요."
            )
        with path.open(encoding="utf-8") as f:
            codes = yaml.safe_load(f) or {}
        if UNKNOWN_CODE not in codes:
            # 이 코드가 없으면 미등록 이벤트를 기록할 방법이 없다.
            raise ValueError(f"{path} 에 {UNKNOWN_CODE} 가 정의되어 있어야 합니다.")
        return cls(codes, path)

    def __len__(self) -> int:
        return len(self._codes)

    def is_known(self, code: str) -> bool:
        return code in self._codes

    def level_of(self, code: str, default: str = "info") -> str:
        return (self._codes.get(code) or {}).get("level", default)

    def applies_to(self, code: str) -> str | None:
        """이 이벤트가 갱신하는 DB 대상. 로그 전용이면 None."""
        return (self._codes.get(code) or {}).get("applies_to")

    def allowed_robot_types(self, code: str) -> list[str]:
        """이 이벤트를 발행할 수 있는 로봇 타입 목록."""
        return (self._codes.get(code) or {}).get("robot_types", [])
