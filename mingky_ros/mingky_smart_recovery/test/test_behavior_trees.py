from pathlib import Path
import xml.etree.ElementTree as ET


TREE_DIR = Path(__file__).resolve().parents[1] / 'behavior_trees'


def _root(name: str):
    return ET.parse(TREE_DIR / name).getroot()


def _tags(root) -> set[str]:
    return {element.tag for element in root.iter()}


def test_adaptive_navigation_trees_do_not_run_motion_recoveries() -> None:
    for planner in ('navfn', 'smac2d'):
        root = _root(f'navigate_no_recovery_{planner}.xml')

        assert _tags(root).isdisjoint({'Spin', 'BackUp', 'Wait'})


def test_smac_tree_selects_smac_planner() -> None:
    root = _root('navigate_no_recovery_smac2d.xml')
    selector = root.find('.//PlannerSelector')

    assert selector is not None
    assert selector.attrib['default_planner'] == 'Smac2D'
    assert root.find('.//RateController').attrib['hz'] == '2.0'


def test_final_fallback_retains_existing_motion_recoveries() -> None:
    root = _root('navigate_recovery_smac2d.xml')

    assert {'Spin', 'BackUp', 'Wait'} <= _tags(root)
    assert root.find('.//Wait').attrib['wait_duration'] == '0.3'
