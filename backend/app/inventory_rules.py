"""로봇이 보고한 사실에 판정을 붙인다.

로봇은 "이 노드가 두 번 떴다" 는 사실만 보고한다. 그게 얼마나 나쁜지는
여기서 정한다 — 임계나 문구를 바꾸려고 로봇을 재배포하는 상황을 만들지
않기 위해서다.

판정을 프론트가 아니라 서버가 하는 이유도 같다. 프론트가 같은 판정을 다시
구현하면 두 곳이 어긋나고, 어긋난 순간 어느 쪽이 맞는지 아무도 모른다.
"""

import os
from pathlib import Path

import yaml

from .schemas import DuplicateNodeOut, NodeGraphInfo, WorkspaceInfo

_DEFAULT_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "duplicate_node_severity.yaml")

_DEFAULT_SEVERITY = "warning"
_DEFAULT_REASON = "중복 실행 — 자원 낭비"


class DuplicateNodeRules:
    """중복 노드의 심각도 정본."""

    def __init__(self, nodes: dict, default_severity: str, default_reason: str):
        self._nodes = nodes
        self._default_severity = default_severity
        self._default_reason = default_reason

    @classmethod
    def load(cls, explicit: str = "") -> "DuplicateNodeRules":
        """파일이 없어도 죽지 않는다.

        event_codes.yaml 과 다른 판단이다. 그쪽은 없으면 미등록 이벤트를
        기록할 방법 자체가 사라지므로 기동을 막아야 한다. 이쪽은 없어도
        기본 등급으로 동작하고, 관측성 기능이 하나 덜 정확해질 뿐이다.
        기동을 막으면 이 파일 때문에 관제 전체가 안 뜬다.
        """
        path = Path(
            explicit or os.environ.get("DUPLICATE_NODE_RULES_FILE")
            or _DEFAULT_PATH)
        if not path.is_file():
            return cls({}, _DEFAULT_SEVERITY, _DEFAULT_REASON)

        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        return cls(
            raw.get("nodes") or {},
            raw.get("default_severity") or _DEFAULT_SEVERITY,
            raw.get("default_reason") or _DEFAULT_REASON,
        )

    def severity_of(self, node_name: str) -> tuple[str, str]:
        """(등급, 사유). 목록에 없으면 기본값이다.

        모르는 노드를 정상으로 보지 않는다. 중복 자체가 정상이 아니다.
        """
        entry = self._nodes.get(node_name)
        if not isinstance(entry, dict):
            return self._default_severity, self._default_reason
        severity = entry.get("severity")
        if severity not in ("error", "warning"):
            severity = self._default_severity
        return severity, entry.get("reason") or self._default_reason


_rules: DuplicateNodeRules | None = None


def load() -> DuplicateNodeRules:
    global _rules
    _rules = DuplicateNodeRules.load()
    return _rules


def get_rules() -> DuplicateNodeRules:
    # event_codes 와 달리 lifespan 로드를 놓쳐도 죽이지 않는다.
    # 판정이 하나 덜 정확한 것과 요청이 500 으로 죽는 것은 무게가 다르다.
    global _rules
    if _rules is None:
        _rules = DuplicateNodeRules.load()
    return _rules


def duplicates(
    node_graph: list[NodeGraphInfo],
    rules: DuplicateNodeRules | None = None,
) -> list[DuplicateNodeOut]:
    """중복으로 뜬 노드에 심각도를 붙인다. error 를 먼저 보여준다."""
    rules = rules or get_rules()
    found = []
    for node in node_graph:
        if node.count <= 1:
            continue
        severity, reason = rules.severity_of(node.name)
        found.append(DuplicateNodeOut(
            name=node.name,
            namespace=node.namespace,
            count=node.count,
            severity=severity,
            reason=reason,
        ))
    # 조용히 틀리는 쪽(error)이 위로 와야 한다.
    return sorted(found, key=lambda d: (d.severity != "error", d.name))


def has_mixed_workspaces(workspaces: list[WorkspaceInfo]) -> bool:
    """우리가 관리하는 코드가 두 벌 이상 돌고 있는가.

    둘 이상이면 서로 다른 코드가 한 로봇에서 같이 도는 것이고, 그 상태로는
    무엇을 고쳐야 하는지 알 수 없다.

    두 가지는 세지 않는다.

    노드가 하나도 안 잡힌 워크스페이스 — 빌드만 해두고 안 쓰는 것이라
    지금 도는 코드가 갈렸다는 뜻이 아니다.

    git 저장소가 아닌 워크스페이스(commit 이 None) — 로봇 제조사가 준
    플랫폼(~/pinky_pro 의 라이다 드라이버 등)이 여기 해당한다. 우리
    저장소와 무관하고 버전 관리 대상도 아니라, 별도 경로에 있는 것이
    정상이다. 이걸 세면 정상 배치에서도 경고가 항상 켜져 있게 되고,
    그러면 진짜 혼재가 일어났을 때 아무도 안 본다.
    """
    ours = [
        w for w in workspaces
        if w.process_count > 0 and w.commit is not None
    ]
    return len(ours) > 1
