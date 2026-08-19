"""모드 요청과 limiter 적용 상태의 시간 기반 일치 판정."""


class ModeAlignmentMonitor:
    """짧은 전달 지연은 넘기고 지속되는 불일치 전이만 돌려준다."""

    def __init__(self, grace_sec: float):
        self.grace_sec = max(0.0, grace_sec)
        self._mismatch_since: float | None = None
        self._reported = False

    def check(
        self, requested: str, applied: str | None, now: float,
    ) -> str | None:
        if applied == requested:
            recovered = self._reported
            self._mismatch_since = None
            self._reported = False
            return "recovered" if recovered else None

        if self._mismatch_since is None:
            self._mismatch_since = now

        if not self._reported and now - self._mismatch_since >= self.grace_sec:
            self._reported = True
            return "mismatch"
        return None
