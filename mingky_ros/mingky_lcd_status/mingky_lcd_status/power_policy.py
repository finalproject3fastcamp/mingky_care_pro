"""Pure LCD backlight policy shared by the node and unit tests."""


def should_dim_display(
    *,
    robot_state: str,
    session_state: str,
    mode: str,
    evacuating: bool,
) -> bool:
    """Return whether the LCD may enter its low-power brightness.

    Warnings, manual control and every active clinical session stay bright.  Only
    an idle/charging robot with no session is dimmed.
    """
    if evacuating or mode != 'auto' or session_state != 'none':
        return False
    return robot_state in ('idle', 'charging')
