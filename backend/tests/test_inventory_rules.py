"""중복 노드 심각도와 워크스페이스 혼재 판정 검증.

판정을 서버가 하는 이유는 임계나 문구를 바꾸려고 로봇을 재배포하지 않기
위해서다. 그래서 이 판정이 설정 파일을 제대로 읽는지가 중요하다.
"""

from app import inventory_rules
from app.inventory_rules import DuplicateNodeRules
from app.schemas import NodeGraphInfo, WorkspaceInfo


def _rules():
    return DuplicateNodeRules(
        nodes={
            "adc_reader": {
                "severity": "error", "reason": "I2C 동시 접근"},
            "camera_image_streamer": {
                "severity": "warning", "reason": "CPU 낭비"},
        },
        default_severity="warning",
        default_reason="중복 실행",
    )


def test_single_instance_is_not_a_duplicate():
    graph = [NodeGraphInfo(name="guide_manager", namespace="/", count=1)]

    assert inventory_rules.duplicates(graph, _rules()) == []


def test_hardware_nodes_are_errors_because_they_fail_silently():
    graph = [NodeGraphInfo(name="adc_reader", namespace="/", count=2)]

    result = inventory_rules.duplicates(graph, _rules())

    assert result[0].severity == "error"
    assert result[0].count == 2
    assert "I2C" in result[0].reason


def test_unknown_nodes_fall_back_to_warning_not_silence():
    # 중복 자체가 정상이 아니다. 모르는 노드를 정상으로 보면 안 된다.
    graph = [NodeGraphInfo(name="some_new_node", namespace="/", count=3)]

    result = inventory_rules.duplicates(graph, _rules())

    assert result[0].severity == "warning"
    assert result[0].reason == "중복 실행"


def test_errors_are_listed_before_warnings():
    graph = [
        NodeGraphInfo(name="camera_image_streamer", namespace="/", count=2),
        NodeGraphInfo(name="adc_reader", namespace="/", count=2),
    ]

    result = inventory_rules.duplicates(graph, _rules())

    # 조용히 틀리는 쪽이 위로 와야 한다.
    assert [d.name for d in result] == ["adc_reader", "camera_image_streamer"]


def test_a_single_workspace_is_the_normal_deployment():
    workspaces = [WorkspaceInfo(path="/home/pinky/mingky_care_pro",
                                process_count=11)]

    assert inventory_rules.has_mixed_workspaces(workspaces) is False


def test_two_active_workspaces_mean_code_is_mixed():
    workspaces = [
        WorkspaceInfo(path="/home/pinky/mingky_care_pro", process_count=11),
        WorkspaceInfo(path="/home/pinky/wmk", process_count=1),
    ]

    assert inventory_rules.has_mixed_workspaces(workspaces) is True


def test_workspace_without_running_processes_does_not_count():
    workspaces = [
        WorkspaceInfo(path="/home/pinky/mingky_care_pro", process_count=11),
        WorkspaceInfo(path="/home/pinky/old", process_count=0),
    ]

    assert inventory_rules.has_mixed_workspaces(workspaces) is False


def test_missing_rules_file_does_not_stop_the_server(tmp_path):
    # event_codes.yaml 과 다른 판단이다. 이 파일이 없다고 관제 전체가
    # 안 뜨면 안 된다 — 판정이 하나 덜 정확해질 뿐이다.
    rules = DuplicateNodeRules.load(str(tmp_path / "nope.yaml"))

    result = inventory_rules.duplicates(
        [NodeGraphInfo(name="adc_reader", namespace="/", count=2)], rules)

    assert result[0].severity == "warning"


def test_shipped_config_marks_i2c_and_state_publishers_as_errors():
    # 실제 배포되는 설정이 의도대로 읽히는지. 오타 하나로 error 가
    # warning 이 되면 조용한 오염을 놓친다.
    rules = DuplicateNodeRules.load()

    for name in ("adc_reader", "battery_guard", "event_gateway",
                 "joint_state_publisher"):
        severity, _ = rules.severity_of(name)
        assert severity == "error", name
