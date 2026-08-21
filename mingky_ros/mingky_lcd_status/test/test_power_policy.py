from mingky_lcd_status.power_policy import should_dim_display


def test_idle_and_charging_without_session_are_dimmed():
    for state in ('idle', 'charging'):
        assert should_dim_display(
            robot_state=state, session_state='none', mode='auto',
            evacuating=False)


def test_active_session_manual_mode_and_emergency_stay_bright():
    assert not should_dim_display(
        robot_state='idle', session_state='qr_scanning', mode='auto',
        evacuating=False)
    assert not should_dim_display(
        robot_state='idle', session_state='none', mode='manual',
        evacuating=False)
    assert not should_dim_display(
        robot_state='idle', session_state='none', mode='auto',
        evacuating=True)
    assert not should_dim_display(
        robot_state='battery_low', session_state='none', mode='auto',
        evacuating=False)
