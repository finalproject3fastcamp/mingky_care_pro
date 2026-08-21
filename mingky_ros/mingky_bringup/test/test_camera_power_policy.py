import importlib.util
from pathlib import Path

from mingky_interfaces.msg import GuideState


SCRIPT = Path(__file__).parents[1] / 'scripts' / 'camera_power_manager.py'
SPEC = importlib.util.spec_from_file_location('camera_power_manager', SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_rear_camera_is_only_needed_for_guidance_or_preview():
    assert MODULE.rear_camera_needed(GuideState.SESSION_GUIDING, False)
    assert MODULE.rear_camera_needed(GuideState.SESSION_NONE, True)
    assert not MODULE.rear_camera_needed(GuideState.SESSION_NONE, False)
    assert not MODULE.rear_camera_needed(GuideState.SESSION_IN_ROOM, False)
