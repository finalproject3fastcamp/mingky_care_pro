"""Safe battery CLI installation contract tests."""

import os
from pathlib import Path
import subprocess


PACKAGE = Path(__file__).resolve().parents[1]
BATTERY_CLI = PACKAGE / 'scripts' / 'battery_status.py'
INSTALLER = PACKAGE / 'scripts' / 'install_battery_command.sh'
CMAKE = PACKAGE / 'CMakeLists.txt'


def test_safe_cli_does_not_open_lcd_or_adc() -> None:
    text = BATTERY_CLI.read_text(encoding='utf-8')

    assert 'pinky_lcd' not in text
    assert 'pinkylib' not in text
    assert 'Battery()' not in text
    assert "'/battery/voltage'" in text
    assert "'/battery/percent'" in text


def test_installer_preserves_explicit_legacy_lcd_command() -> None:
    text = INSTALLER.read_text(encoding='utf-8')

    assert 'SHELL_CONFIG="${HOME}/.bashrc"' in text
    assert "alias battery='ros2 run mingky_bringup battery_status.py'" in text
    assert "alias battery-lcd='/home/pinky/ap/check_battery.py'" in text
    assert 'BEGIN MINGKY SAFE BATTERY COMMAND' in text
    assert 'END MINGKY SAFE BATTERY COMMAND' in text


def test_scripts_are_installed_with_bringup() -> None:
    text = CMAKE.read_text(encoding='utf-8')

    assert 'scripts/battery_status.py' in text
    assert 'scripts/install_battery_command.sh' in text


def test_installer_is_idempotent_and_overrides_old_alias(tmp_path) -> None:
    bashrc = tmp_path / '.bashrc'
    bashrc.write_text(
        "alias battery='/home/pinky/ap/check_battery.py'\n",
        encoding='utf-8',
    )
    env = {**os.environ, 'HOME': str(tmp_path)}

    subprocess.run([str(INSTALLER)], check=True, env=env)
    subprocess.run([str(INSTALLER)], check=True, env=env)

    text = bashrc.read_text(encoding='utf-8')
    assert text.count('# BEGIN MINGKY SAFE BATTERY COMMAND') == 1
    assert text.count('# END MINGKY SAFE BATTERY COMMAND') == 1
    assert text.rfind("alias battery='ros2 run mingky_bringup battery_status.py'") > (
        text.rfind("alias battery='/home/pinky/ap/check_battery.py'")
    )
