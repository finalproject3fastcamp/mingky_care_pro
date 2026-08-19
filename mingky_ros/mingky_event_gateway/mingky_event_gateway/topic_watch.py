"""감시 중인 토픽의 마지막 수신 시각과 최근 주기.

monitoring-spec.md §7.2 · 로드맵 9.

## 왜 필요한가

`system` 탭은 systemd 유닛 상태만 본다. 실전 장애 모드는 **유닛은 active 인데
`/scan` 이 안 나오는 것**이다. 라이다 USB 가 죽어도 노드 프로세스는 살아 있고
systemd 는 초록이다.

## 도착하는 데이터가 아니라 경과 시간으로 판정한다

두절 판정(백엔드 §3.3)과 같은 구조다. 콜백에서는 **시각만 갱신**하고, 메시지
내용은 보지 않는다. 그래서 어떤 타입이든 상관없고 비용도 콜백 한 번이다.

`ros2 topic hz` 를 서브프로세스로 돌리지 않는다 — 호출마다 노드를 새로 띄우고,
5초 주기에서는 그 자체가 부하다.

## 나이만 보내지 않는 이유

라이다가 USB 대역 부족으로 10Hz → 3Hz 가 되면 마지막 수신은 0.33초 전이라
어떤 나이 임계에도 안 걸린다. 창 안의 수신 횟수로 주기를 같이 재서 보낸다.

## 판정은 하지 않는다

"몇 Hz 면 정상인가" 는 서버가 정한다(config/topic_watch.yaml). 로봇은 사실만
보고한다 — 임계를 바꾸려고 로봇을 재배포하는 상황을 만들지 않는다.
"""

import threading
import time
from collections import deque

# 주기를 재는 창. 짧으면 한 번 걸러진 것만으로 Hz 가 출렁이고, 길면 저하를
# 늦게 안다. heartbeat 주기(5초)와 맞춘다 — 한 번 보낼 때마다 한 창이다.
DEFAULT_WINDOW_SEC = 5.0

# 토픽당 들고 있을 수신 시각 상한. 창 × 최대 주기를 잡아도 이 안이고, 혹시
# 아주 빠른 토픽을 감시 목록에 넣어도 메모리가 늘지 않는다. 상한에 걸려도
# Hz 계산은 남은 표본으로 그대로 성립한다(간격의 평균이다).
MAX_STAMPS = 256


class TopicAges:
    """토픽별 마지막 수신 시각과 최근 주기.

    시간 원천을 주입받는다. 벽시계를 쓰면 NTP 가 시각을 당기는 순간 나이가
    음수가 되거나 갑자기 커진다. 기본은 monotonic 이고, 테스트는 가짜 시계를
    넣어 창 경계를 정확히 재현한다.
    """

    def __init__(self, topics, window_sec: float = DEFAULT_WINDOW_SEC,
                 clock=time.monotonic):
        self._clock = clock
        self._window = max(0.1, float(window_sec))
        # 감시를 시작한 시각. 한 번도 못 받은 토픽의 나이를 여기서부터 잰다.
        #
        # None 을 보내지 않는 이유가 이것이다 — '부팅 이후 라이다가 한 번도
        # 안 떴다' 가 시간이 갈수록 나빠지는 값으로 드러나야, 서버의 같은
        # 임계 하나가 '죽었다' 와 '아예 안 떴다' 를 함께 잡는다.
        self._started = self._clock()
        self._lock = threading.Lock()
        self._stamps = {topic: deque(maxlen=MAX_STAMPS) for topic in topics}

    @property
    def topics(self):
        return tuple(self._stamps)

    def record(self, topic: str) -> None:
        """수신. ROS 콜백이 부른다. 메시지는 보지 않는다."""
        now = self._clock()
        with self._lock:
            stamps = self._stamps.get(topic)
            if stamps is None:
                return
            stamps.append(now)

    def snapshot(self) -> dict:
        """heartbeat 에 실을 본문. `{topic: {age_sec, hz}}`.

        hz 는 창 안에 표본이 둘 이상일 때만 낸다. 하나로는 간격을 잴 수 없고,
        그때 0 을 보내면 서버가 '주기 저하' 로 읽는다. 없는 값은 없는 채로
        보내고 나이로 판정하게 둔다.
        """
        now = self._clock()
        with self._lock:
            result = {}
            for topic, stamps in self._stamps.items():
                recent = [t for t in stamps if now - t <= self._window]
                last = stamps[-1] if stamps else None
                span = (recent[-1] - recent[0]) if len(recent) >= 2 else 0.0
                result[topic] = {
                    "age_sec": round(
                        now - (last if last is not None else self._started), 3),
                    "hz": round((len(recent) - 1) / span, 2) if span > 0 else None,
                }
            return result


def parse_watch_spec(entries):
    """`"/scan:sensor_msgs/msg/LaserScan"` 목록을 (토픽, 타입)으로 가른다.

    타입을 파라미터로 받는 이유는 감시 대상을 코드 수정 없이 바꾸기 위해서다.
    로봇마다 켜는 토픽이 다르고(팔에는 아예 없다), 목록을 코드에 박으면
    토픽 하나 추가에 재배포가 필요해진다.

    형식이 틀린 항목은 조용히 버리지 않고 걸러서 돌려준다. 오타 하나로 감시가
    빠진 채 정상으로 보이는 것이 가장 나쁜 결과다 — 호출부가 로그로 남긴다.
    """
    parsed, bad = [], []
    for entry in entries or []:
        topic, _, msg_type = str(entry).partition(":")
        topic, msg_type = topic.strip(), msg_type.strip()
        if not topic or not msg_type:
            bad.append(str(entry))
            continue
        parsed.append((topic, msg_type))
    return parsed, bad
