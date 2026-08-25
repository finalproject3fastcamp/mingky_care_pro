"""로봇 위치의 서버 측 정본 — 관제가 여러 대를 한 화면에서 본다.

지금까지 위치는 **서버를 스쳐 지나가기만 했다.** 로봇의 조작 브리지가
`/robots/{id}/teleop/robot` 으로 올리면 `routers/teleop.py` 가 그 로봇에
붙어 있는 조작자 소켓으로 그대로 흘려보냈다. 서버는 어느 로봇이 어디
있는지 몰랐고, 화면은 자기가 고른 한 대만 볼 수 있었다.

## 왜 조작 소켓으로 두 대를 보면 안 되는가

`routers/teleop.py` 의 조작자 소켓은 붙는 순간 `control_audit` 에
`teleop_attach` 를 남긴다. 그 행동은 `INTERVENTION_ACTIONS` 에 들어 있어
`slo.py` 가 **그 세션을 개입으로 판정한다**(§1.1 의 "teleop 없음").

두 번째 로봇을 보려고 조작자 소켓을 하나 더 열면, 보기만 했는데 그 로봇의
안내 세션이 SLO 실패가 된다. 관측과 조작은 **다른 권한이고 다른 사실**이라
채널을 나눈다. 이 모듈이 그 관측 채널의 상태를 갖는다.

## 저장하지 않는다

0.5초마다 덮어쓰는 값이다. 003 이 "화면 표시용 실시간 상태는 DB 에 저장하지
않는다" 로 정해둔 것과 성격이 같아 `robot_runtime` · `qr_runtime` 과 같은
층에 인메모리로 둔다. 추이가 필요하면 events 에 남길 일이지 이 표에 쌓을
일이 아니다.

**워커 1개 전제다.** `main.py` 의 advisory lock 이 그 전제를 이미 지키고
있다. 늘려야 할 때가 오면 teleop 중계와 함께 Redis pub/sub 으로 옮긴다.

## 낡은 위치를 지우지 않는다

로봇이 끊겨도 마지막으로 본 자리를 지우지 않는다. 지우면 화면에서 로봇이
사라지는데, 그건 "위치를 모른다" 가 아니라 "로봇이 없다" 로 읽힌다. 대신
`observed_at` 을 같이 내려보내고 **판정은 화면이 한다** — 나이를 보여주는
규칙이 이미 `lib/freshness.ts` 에 하나로 모여 있고, 메시지 사이의 침묵도
거기서만 알아챌 수 있다. 서버가 한 번 더 판정하면 같은 사실에 답이 둘이 된다.

## 왜 큐로 나눠 보내는가

브로드캐스트를 로봇 소켓의 수신 루프에서 곧바로 `await send_text` 하면,
느린 관전자 하나가 **로봇의 수신을 멈춘다.** 그러면 그 로봇에 조작 명령이
안 들어가므로, 관전 때문에 조작이 막히는 구조가 된다.

관전자마다 짧은 큐를 두고 넘치면 **오래된 것부터 버린다.** 위치는 마지막
값만 의미가 있어서, 밀린 좌표를 지키는 것보다 최신 좌표가 늦지 않는 편이
언제나 낫다.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

# 관전자 한 명이 밀어둘 수 있는 메시지 수. 로봇 4대 × 몇 프레임이면 충분하다.
# 크게 잡을 이유가 없다 — 밀린 좌표는 어차피 버릴 값이다.
WATCHER_QUEUE_SIZE = 16


@dataclass(frozen=True)
class PoseSample:
    """AMCL 이 말한 지도 좌표. 미터·라디안, 맵 프레임 기준이다."""

    x: float
    y: float
    yaw: float
    observed_at: datetime


_poses: dict[str, PoseSample] = {}
_watchers: set[asyncio.Queue] = set()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def update(robot_id: str, x: float, y: float, yaw: float) -> PoseSample:
    """로봇이 올린 위치를 기록하고 관전자들에게 알린다.

    시각은 로봇 시계가 아니라 **서버 수신 시각**이다. 전송 지연을 숨기지
    않아야 화면의 나이 판정이 정직하다 (battery · robot_inventory 와 같은 규칙).
    """
    sample = PoseSample(x=x, y=y, yaw=yaw, observed_at=_now())
    _poses[robot_id] = sample
    _publish({"type": "pose", **as_message(robot_id, sample)})
    return sample


def get(robot_id: str) -> PoseSample | None:
    return _poses.get(robot_id)


def snapshot() -> dict[str, PoseSample]:
    return dict(_poses)


def as_message(robot_id: str, sample: PoseSample) -> dict:
    """전송 형태. HTTP 스냅샷과 WS 프레임이 같은 모양이어야 한다.

    좌표를 소수점 셋째 자리에서 자른다. 1 mm 다. 브리지가 이미 같은 자리에서
    자르고 있고(teleop_bridge), AMCL 의 실제 오차는 그보다 두 자릿수 크다.
    """
    return {
        "robot_id": robot_id,
        "x": round(sample.x, 3),
        "y": round(sample.y, 3),
        "yaw": round(sample.yaw, 4),
        "observed_at": sample.observed_at.isoformat(),
    }


def snapshot_message() -> dict:
    """관전자가 붙는 즉시 보내는 첫 프레임.

    이게 없으면 화면은 다음 좌표가 올 때까지 빈 지도를 본다. 서 있는 로봇은
    좌표가 바뀌지 않아도 계속 올라오므로 최대 0.5초지만, 브리지가 끊긴
    로봇은 **영영 안 온다.** 마지막으로 본 자리조차 못 그리게 된다.
    """
    return {
        "type": "snapshot",
        "poses": [as_message(robot_id, sample)
                  for robot_id, sample in sorted(_poses.items())],
    }


# ----------------------------------------------------------------- 관전자

def subscribe() -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=WATCHER_QUEUE_SIZE)
    _watchers.add(queue)
    return queue


def unsubscribe(queue: asyncio.Queue) -> None:
    _watchers.discard(queue)


def watcher_count() -> int:
    return len(_watchers)


def _publish(message: dict) -> None:
    """모든 관전자에게 넣는다. 넘치면 오래된 것부터 버린다.

    동기 함수인 것이 요점이다. `update()` 를 부르는 쪽은 로봇 소켓의 수신
    루프이고, 거기서 관전자를 기다리면 안 된다 (모듈 주석 참고).
    """
    for queue in list(_watchers):
        while True:
            try:
                queue.put_nowait(message)
                break
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    # 그 사이 관전자가 다 비웠다. 다시 넣어 보면 들어간다.
                    continue


def reset() -> None:
    _poses.clear()
    _watchers.clear()
