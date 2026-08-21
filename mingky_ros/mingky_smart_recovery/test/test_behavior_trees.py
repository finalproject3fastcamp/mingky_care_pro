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

        assert _tags(root).isdisjoint({'Spin', 'BackUp'})


def test_planner_failure_waits_for_refreshed_global_costmap() -> None:
    for planner in ('navfn', 'smac2d'):
        root = _root(f'navigate_no_recovery_{planner}.xml')
        planner_recovery = root.find(
            './/RecoveryNode[@name="ComputePathToPose"]/Sequence')

        assert planner_recovery is not None
        assert [child.tag for child in planner_recovery] == [
            'WouldAPlannerRecoveryHelp', 'ClearEntireCostmap', 'Wait']
        assert planner_recovery.find('Wait').attrib['wait_duration'] == '1.1'


def test_smac_tree_selects_smac_planner() -> None:
    root = _root('navigate_no_recovery_smac2d.xml')
    selector = root.find('.//PlannerSelector')

    assert selector is not None
    assert selector.attrib['default_planner'] == 'Smac2D'


def test_replanning_validates_only_the_untraveled_path() -> None:
    for name in (
            'navigate_no_recovery_navfn.xml',
            'navigate_no_recovery_smac2d.xml',
            'navigate_recovery_smac2d.xml'):
        root = _root(name)
        controller = root.find('.//RateController')
        planner_fallback = controller.find(
            './RecoveryNode[@name="ComputePathToPose"]/Fallback')

        assert controller is not None
        assert controller.attrib == {'hz': '1.0'}
        assert root.find('.//DistanceController') is None
        assert planner_fallback is not None
        assert planner_fallback.find('.//GlobalUpdatedGoal') is not None
        assert planner_fallback.find('.//PathExpiringTimer') is None
        truncator = planner_fallback.find('.//TruncatePathLocal')
        assert truncator is not None
        assert truncator.attrib == {
            'input_path': '{path}',
            'output_path': '{remaining_path}',
            'distance_forward': '20.0',
            'distance_backward': '0.0',
            'robot_frame': 'base_link',
            'transform_tolerance': '0.3',
        }
        validator = planner_fallback.find('.//IsPathValid')
        assert validator is not None
        assert validator.attrib == {'path': '{remaining_path}'}


def test_stalled_follow_path_refreshes_costmap_once_before_adaptive_recovery() -> None:
    for planner in ('navfn', 'smac2d'):
        root = _root(f'navigate_no_recovery_{planner}.xml')
        follow_recovery = root.find('.//RecoveryNode[@name="FollowPath"]')

        assert follow_recovery is not None
        assert follow_recovery.attrib['number_of_retries'] == '1'


def test_final_fallback_retains_existing_motion_recoveries() -> None:
    root = _root('navigate_recovery_smac2d.xml')

    assert {'Spin', 'BackUp', 'Wait'} <= _tags(root)
    waits = root.findall('.//Wait')
    assert {wait.attrib['wait_duration'] for wait in waits} == {'0.3', '1.1'}
