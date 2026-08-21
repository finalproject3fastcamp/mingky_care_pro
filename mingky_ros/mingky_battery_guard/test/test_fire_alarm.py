from mingky_battery_guard.fire_alarm import play_fire_alarm


class FakeBuzzer:
    def __init__(self):
        self.calls = []

    def buzzer_start(self, frequency):
        self.calls.append(('start', frequency))

    def set_buzzer_duty(self, duty):
        self.calls.append(('duty', duty))

    def buzzer_stop(self):
        self.calls.append(('stop',))

    def close(self):
        self.calls.append(('close',))


def test_fire_alarm_uses_pinkylib_pattern_and_releases_gpio():
    buzzer = FakeBuzzer()
    pauses = []

    play_fire_alarm(lambda: buzzer, pauses.append)

    assert buzzer.calls == [
        ('start', 784),
        ('duty', 50), ('duty', 0),
        ('duty', 50), ('duty', 0),
        ('duty', 50), ('duty', 0),
        ('stop',), ('close',),
    ]
    assert pauses == [0.12, 0.08, 0.12, 0.08, 0.12]
