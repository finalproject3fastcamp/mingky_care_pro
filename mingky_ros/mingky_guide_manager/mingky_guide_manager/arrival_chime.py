"""Pinky의 임상 목적지 도착음을 재생한다.

저전압 경고음과 혼동하지 않도록 짧게 상승하는 두 음만 사용한다. 하드웨어
모듈은 실제 Pinky에서만 import하여 개발 PC에서도 패키지를 불러올 수 있게 한다.
"""

import time
from typing import Callable


def play_arrival_chime(
        buzzer_factory: Callable | None = None,
        sleep: Callable[[float], None] = time.sleep) -> None:
    """523Hz와 659Hz를 차례로 울려 짧은 도착음을 만든다."""
    if buzzer_factory is None:
        from pinkylib.buzzer import Buzzer

        buzzer_factory = Buzzer

    buzzer = buzzer_factory()
    started = False
    try:
        buzzer.buzzer_start(523)
        started = True
        buzzer.set_buzzer_duty(50)
        sleep(0.12)
        buzzer.set_buzzer_duty(0)
        sleep(0.06)
        buzzer.set_buzzer_freq(659)
        buzzer.set_buzzer_duty(50)
        sleep(0.18)
        buzzer.set_buzzer_duty(0)
    finally:
        if started:
            buzzer.buzzer_stop()
            buzzer.close()
