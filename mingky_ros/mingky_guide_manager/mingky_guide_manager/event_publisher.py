"""Event 발행 헬퍼.

config/event_codes.yaml 을 읽어 미등록 event_code 를 발행 단계에서 막는다.
수집 서버(FastAPI)가 다시 검증하지만, 개발 중에는 여기서 먼저 걸리는 편이
원인을 찾기 훨씬 빠르다.

이 모듈이 event_codes.yaml 을 실제로 읽는 첫 코드다. 그 파일이 정본으로서
의미를 갖는 지점이므로, 이벤트를 추가할 때는 yaml 부터 고쳐야 한다.
"""

import json
import time
import uuid
from pathlib import Path
from typing import Optional

import yaml
from ament_index_python.packages import PackageNotFoundError, get_package_share_directory
from builtin_interfaces.msg import Time
from mingky_interfaces.msg import Event

_LEVELS = {
    'info': Event.LEVEL_INFO,
    'warning': Event.LEVEL_WARNING,
    'error': Event.LEVEL_ERROR,
}

# 수집 서버가 미등록 코드를 받았을 때 쓰는 코드. 발행 측에서도 같은 걸 쓴다.
UNKNOWN_CODE = 'system.unknown_event_code'


def _find_codes_file(explicit: str = '') -> Optional[Path]:
    """event_codes.yaml 위치를 찾는다.

    1) 파라미터로 준 경로
    2) 설치된 패키지 share  (ros2 run 으로 띄우는 정상 경로)
    3) 소스 트리를 거슬러 올라가며 탐색  (colcon build 전 개발 편의)
    """
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None

    try:
        share = Path(get_package_share_directory('mingky_guide_manager'))
        candidate = share / 'config' / 'event_codes.yaml'
        if candidate.is_file():
            return candidate
    except PackageNotFoundError:
        pass

    for parent in Path(__file__).resolve().parents:
        candidate = parent / 'config' / 'event_codes.yaml'
        if candidate.is_file():
            return candidate
    return None


def wall_clock_now() -> Time:
    """벽시계 시각.

    node.get_clock() 을 쓰지 않는다. use_sim_time 이 켜진 환경에서는
    시뮬레이션 시간이 들어가고, occurred_at 이 1970년으로 저장되면
    이벤트 타임라인이 통째로 거짓이 된다.
    """
    ns = time.time_ns()
    return Time(sec=ns // 1_000_000_000, nanosec=ns % 1_000_000_000)


class EventPublisher:
    """노드 하나가 들고 쓰는 Event 발행기."""

    def __init__(self, node, robot_id: str, codes_file: str = '', topic: str = '/events'):
        self._node = node
        self._robot_id = robot_id
        self._pub = node.create_publisher(Event, topic, 10)

        path = _find_codes_file(codes_file)
        if path is None:
            # 죽이지는 않는다. yaml 하나 때문에 로봇이 안 뜨면 더 나쁘다.
            # 대신 검증이 꺼졌다는 사실은 크게 남긴다.
            node.get_logger().error(
                'event_codes.yaml 을 찾지 못했습니다. event_code 검증이 비활성화됩니다. '
                'event_codes_file 파라미터로 경로를 지정하세요.'
            )
            self._codes = None
        else:
            with open(path, encoding='utf-8') as f:
                self._codes = yaml.safe_load(f) or {}
            node.get_logger().info(
                f'event_codes.yaml 로드: {path} (코드 {len(self._codes)}개)'
            )

    def publish(self, event_code: str, payload: Optional[dict] = None,
                session_id: int = 0, level: Optional[str] = None) -> None:
        """이벤트 한 건을 발행한다.

        level 을 생략하면 event_codes.yaml 의 값을 쓴다.
        같은 값을 코드와 yaml 두 곳에 적어두면 언젠가 갈라지기 때문이다.
        """
        spec = self._codes.get(event_code) if self._codes is not None else None

        if self._codes is not None and spec is None:
            self._node.get_logger().error(
                f"미등록 event_code '{event_code}'. "
                'config/event_codes.yaml 에 먼저 추가하세요.'
            )
            # 기록을 버리지 않는다. 대신 누락 사실이 대시보드에 드러나게 한다.
            if event_code != UNKNOWN_CODE:
                self.publish(UNKNOWN_CODE, {'received_code': event_code}, session_id)
            return

        msg = Event()
        msg.event_id = str(uuid.uuid4())
        msg.robot_id = self._robot_id
        msg.session_id = session_id
        msg.occurred_at = wall_clock_now()
        msg.level = _LEVELS.get(level or (spec or {}).get('level', 'info'), Event.LEVEL_INFO)
        msg.event_code = event_code
        msg.source_node = self._node.get_name()
        msg.payload = json.dumps(payload or {}, ensure_ascii=False, separators=(',', ':'))

        self._pub.publish(msg)
