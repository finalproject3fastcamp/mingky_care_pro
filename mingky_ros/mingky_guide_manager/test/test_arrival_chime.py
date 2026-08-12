"""도착음 패턴은 하드웨어 없이 검증한다."""

from mingky_guide_manager.arrival_chime import play_arrival_chime


class FakeBuzzer:
    """Record buzzer operations without accessing Raspberry Pi GPIO."""

    def __init__(self):
        self.calls = []

    def buzzer_start(self, frequency):
        self.calls.append(('start', frequency))

    def set_buzzer_duty(self, duty):
        self.calls.append(('duty', duty))

    def set_buzzer_freq(self, frequency):
        self.calls.append(('frequency', frequency))

    def buzzer_stop(self):
        self.calls.append(('stop',))

    def close(self):
        self.calls.append(('close',))


def test_arrival_chime_is_a_short_rising_two_note_pattern():
    buzzer = FakeBuzzer()
    pauses = []

    play_arrival_chime(lambda: buzzer, pauses.append)

    assert buzzer.calls == [
        ('start', 523),
        ('duty', 50),
        ('duty', 0),
        ('frequency', 659),
        ('duty', 50),
        ('duty', 0),
        ('stop',),
        ('close',),
    ]
    assert pauses == [0.12, 0.06, 0.18]
