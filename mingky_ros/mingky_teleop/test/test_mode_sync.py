from mingky_teleop.mode_sync import ModeAlignmentMonitor


def test_short_mismatch_is_not_reported():
    monitor = ModeAlignmentMonitor(grace_sec=3.0)

    assert monitor.check("manual", "auto", 10.0) is None
    assert monitor.check("manual", "auto", 12.9) is None
    assert monitor.check("manual", "manual", 13.0) is None


def test_persistent_mismatch_and_recovery_are_each_reported_once():
    monitor = ModeAlignmentMonitor(grace_sec=3.0)

    assert monitor.check("manual", None, 10.0) is None
    assert monitor.check("manual", None, 13.0) == "mismatch"
    assert monitor.check("manual", None, 20.0) is None
    assert monitor.check("manual", "manual", 21.0) == "recovered"
    assert monitor.check("manual", "manual", 22.0) is None


def test_new_mismatch_can_be_reported_after_recovery():
    monitor = ModeAlignmentMonitor(grace_sec=1.0)

    monitor.check("auto", "manual", 1.0)
    assert monitor.check("auto", "manual", 2.0) == "mismatch"
    assert monitor.check("auto", "auto", 3.0) == "recovered"
    monitor.check("manual", "auto", 4.0)
    assert monitor.check("manual", "auto", 5.0) == "mismatch"
