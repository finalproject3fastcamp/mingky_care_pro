"""환자를 놓친 채로 얼마나 오래 서 있었는지 재서, 포기할 시점을 판정한다.

로봇은 환자를 놓치면 그 자리에서 **무기한** 기다린다. 그 편이 옳다 --
자리를 옮기면 돌아온 환자가 로봇을 못 찾는다. 다만 아무도 안 오는 경우가
있고, 그때 로봇이 복도 한가운데를 계속 막고 서 있게 된다.

여기서는 시간만 잰다. 실제로 무엇을 할지는 노드가 정한다.
"""

from dataclasses import dataclass


WAITING = 'waiting'


@dataclass(frozen=True)
class DwellPolicy:
    """포기까지 기다릴 시간.

    시연에서는 짧게(십수 초), 운영에서는 길게(몇 분) 쓴다. 관람객이 멈춰 선
    로봇을 3분 동안 볼 수는 없고, 실제 환자는 3분도 짧을 수 있다.
    """

    timeout_sec: float = 180.0

    def __post_init__(self) -> None:
        if not self.timeout_sec > 0.0:
            raise ValueError('timeout_sec 은 0보다 커야 합니다.')


class DwellTimer:
    """`waiting` 이 얼마나 이어졌는지 보고 한 번만 신호를 낸다.

    **한 번만 낸다는 것이 핵심이다.** 시간이 지난 뒤에도 상태는 계속
    `waiting` 이므로, 매번 낸다면 취소 요청이 초당 수십 번 나간다.
    """

    def __init__(self, policy: DwellPolicy) -> None:
        self._policy = policy
        self._since: float | None = None
        self._fired = False

    @property
    def waiting_since(self) -> float | None:
        """대기가 시작된 시각. 대기 중이 아니면 None."""
        return self._since

    def elapsed(self, now: float) -> float:
        """지금까지 기다린 시간(초). 대기 중이 아니면 0."""
        return 0.0 if self._since is None else max(0.0, now - self._since)

    def remaining(self, now: float) -> float:
        """포기까지 남은 시간(초). 대기 중이 아니면 0."""
        if self._since is None:
            return 0.0
        return max(0.0, self._policy.timeout_sec - self.elapsed(now))

    def update(self, state: str | None, now: float) -> bool:
        """상태를 반영하고, **지금 포기해야 하면** True 를 돌려준다.

        `waiting` 에서 벗어나면 시계를 지운다 -- 환자가 돌아왔다가 다시
        놓치면 처음부터 다시 세야 한다. 놓친 시간을 합산하면, 짧게 여러 번
        놓친 것만으로 포기하게 된다.
        """
        if state != WAITING:
            self._since = None
            self._fired = False
            return False

        if self._since is None:
            self._since = now
            self._fired = False
            return False

        # 시계가 뒤로 갔다면(시각 보정 등) 기다린 시간을 믿을 수 없다.
        # 처음부터 다시 센다 -- 그래야 실제보다 일찍 포기하지 않는다.
        if now < self._since:
            self._since = now
            return False

        if self._fired:
            return False
        if now - self._since < self._policy.timeout_sec:
            return False

        self._fired = True
        return True
