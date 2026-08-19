"""토픽 나이·주기 측정 (monitoring-spec.md §7.2).

로봇 쪽 절반이다. 판정(임계 대조)은 서버가 하고 여기서는 사실만 만든다.
그래서 이 파일이 잠그는 것은 "사실이 정확한가" 뿐이다.

가짜 시계를 쓴다. 실제 sleep 으로 창 경계를 재현하면 테스트가 느려지고
러너 부하에 따라 간헐적으로 깨진다.
"""

from mingky_event_gateway.topic_watch import TopicAges, parse_watch_spec


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_topic_never_received_ages_from_the_start_of_watching():
    """None 을 보내지 않는다. '부팅 이후 라이다가 한 번도 안 떴다' 가 시간이
    갈수록 나빠지는 값으로 드러나야 서버의 같은 임계 하나로 잡힌다."""
    clock = FakeClock()
    ages = TopicAges(["/scan"], clock=clock)

    clock.advance(30.0)

    assert ages.snapshot()["/scan"]["age_sec"] == 30.0


def test_age_is_measured_from_the_last_message():
    clock = FakeClock()
    ages = TopicAges(["/scan"], clock=clock)

    clock.advance(10.0)
    ages.record("/scan")
    clock.advance(0.4)

    assert ages.snapshot()["/scan"]["age_sec"] == 0.4


def test_rate_is_measured_over_the_window():
    clock = FakeClock()
    ages = TopicAges(["/scan"], window_sec=5.0, clock=clock)

    for _ in range(11):          # 0.1초 간격 11회 = 간격 10개
        ages.record("/scan")
        clock.advance(0.1)

    assert ages.snapshot()["/scan"]["hz"] == 10.0


def test_rate_is_absent_when_it_cannot_be_measured():
    """표본 하나로는 간격을 잴 수 없다. 그때 0 을 보내면 서버가 '주기 저하'
    로 읽는다 — 없는 값은 없는 채로 보내고 나이로 판정하게 둔다."""
    clock = FakeClock()
    ages = TopicAges(["/scan"], window_sec=5.0, clock=clock)

    ages.record("/scan")

    assert ages.snapshot()["/scan"]["hz"] is None


def test_old_samples_leave_the_window_so_a_slowdown_shows_up():
    """창을 안 비우면 라이다가 멈춰도 Hz 가 과거 평균으로 남는다."""
    clock = FakeClock()
    ages = TopicAges(["/scan"], window_sec=5.0, clock=clock)

    for _ in range(11):
        ages.record("/scan")
        clock.advance(0.1)
    clock.advance(30.0)

    snapshot = ages.snapshot()["/scan"]
    assert snapshot["hz"] is None
    # 마지막 수신 뒤 루프가 0.1초를 더 흘려보낸다.
    assert snapshot["age_sec"] == 30.1


def test_messages_on_topics_we_do_not_watch_are_ignored():
    # 감시 목록에 없는 이름이 들어와도 딕셔너리가 자라면 안 된다.
    ages = TopicAges(["/scan"], clock=FakeClock())

    ages.record("/odom")

    assert set(ages.snapshot()) == {"/scan"}


def test_watch_spec_splits_topic_and_type():
    parsed, bad = parse_watch_spec(["/scan:sensor_msgs/msg/LaserScan"])

    assert parsed == [("/scan", "sensor_msgs/msg/LaserScan")]
    assert bad == []


def test_malformed_watch_spec_is_returned_not_swallowed():
    """오타 하나로 감시가 빠진 채 정상으로 보이는 것이 가장 나쁜 결과다."""
    parsed, bad = parse_watch_spec(["/scan", "", ":LaserScan"])

    assert parsed == []
    assert len(bad) == 3
