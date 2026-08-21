"""Pinky 기본 부저로 화재 경보음을 재생한다."""

import time
from typing import Callable


def play_fire_alarm(
        buzzer_factory: Callable | None = None,
        sleep: Callable[[float], None] = time.sleep) -> None:
    """784Hz 위험음을 짧게 세 번 울리고 GPIO를 반드시 반납한다."""
    if buzzer_factory is None:
        from pinkylib.buzzer import Buzzer

        buzzer_factory = Buzzer

    buzzer = buzzer_factory()
    started = False
    try:
        buzzer.buzzer_start(784)
        started = True
        for index in range(3):
            buzzer.set_buzzer_duty(50)
            sleep(0.12)
            buzzer.set_buzzer_duty(0)
            if index < 2:
                sleep(0.08)
    finally:
        if started:
            buzzer.buzzer_stop()
        buzzer.close()
