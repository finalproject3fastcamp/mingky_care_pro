"""통합 launch에서 안내 핵심 노드가 빠지는 회귀를 막는다."""

from pathlib import Path
import xml.etree.ElementTree as ET


LAUNCH_FILE = (
    Path(__file__).resolve().parents[1] / 'launch' / 'mingky_system.launch.xml'
)


def _root():
    return ET.parse(LAUNCH_FILE).getroot()


def _argument(root, name: str):
    return next(item for item in root.findall('arg') if item.get('name') == name)


def test_qr_reader_is_enabled_for_robot_operation() -> None:
    root = _root()

    assert _argument(root, 'start_qr_reader').get('default') == 'true'
    assert _argument(root, 'qr_source').get('default') == 'csi'

    include = next(
        item for item in root.findall('include')
        if item.get('file', '').endswith('/launch/qr_reader.launch.py')
    )
    assert include.get('if') == '$(var start_qr_reader)'
    forwarded = {
        item.get('name'): item.get('value') for item in include.findall('arg')
    }
    assert forwarded == {
        'source': '$(var qr_source)',
        'robot_id': '$(var robot_id)',
        'backend_url': '$(var backend_url)',
        'preview_port': '$(var qr_preview_port)',
    }


def test_adaptive_recovery_is_the_integrated_default() -> None:
    root = _root()

    assert _argument(root, 'recovery_mode').get('default') == 'adaptive'
    assert _argument(root, 'planner_mode').get('default') == 'navfn'
