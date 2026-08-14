"""로봇에서 실제로 무엇이 돌고 있는지 수집한다.

원인 추적에 가장 많은 시간을 잡아먹은 건 "이 로봇에서 지금 어떤 코드가
돌고 있는가" 를 알 수 없다는 것이었다. 로봇마다 다른 워크스페이스가 섞여
있었고, 같은 노드가 두 번 떠서 I2C 측정값을 조용히 오염시키고 있었다.

## 왜 세 종류로 나누는가

한 모델에 다 담고 싶지만 출처가 다르고 신뢰도가 다르다.

    NodeGraphInfo   rclpy 그래프.  이름·네임스페이스만. **항상 정확하다**
    ProcessInfo     /proc 스캔.    PID·실행경로·CPU. 노드 이름은 추정이다
    WorkspaceInfo   git.           커밋·브랜치·dirty

`get_node_names_and_namespaces()` 는 PID 를 주지 않고, 노드→프로세스를
잇는 공식 API 도 없다. 컴포지션 컨테이너를 쓰면 한 PID 에 노드가 여럿이라
1:1 도 아니다. 그래서 **중복 판정은 그래프로만** 하고(그쪽은 정확하다),
자원은 프로세스 단위로 따로 보고한다. 억지로 합치면 매칭이 틀린 날
중복 경고까지 같이 틀린다.

## 로봇은 사실만 보고한다

중복의 심각도, CPU 임계값, 화면 문구는 서버가 정한다. 임계를 바꾸려고
로봇을 재배포하는 상황을 만들지 않는다.

## subprocess 를 쓰지 않는 곳

노드 목록은 `ros2 node list` 를 부르지 않는다. 호출마다 노드를 새로 띄우는데
5초 주기에서는 그 자체가 부하다. `git rev-parse` 는 subprocess 지만 워크스페이스
커밋은 몇 시간에 한 번 바뀌므로 캐시한다.
"""

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

# /proc/<pid>/stat 의 utime, stime 은 클럭 틱 단위다.
_CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

# git 호출 캐시 수명. 커밋은 몇 시간에 한 번 바뀐다.
GIT_CACHE_SEC = 300.0

# 파이썬으로 실행되는 노드는 argv[0] 이 인터프리터다. 실행 경로를 그걸로
# 잡으면 모든 파이썬 노드가 /usr/bin/python3 로 뭉쳐 아무 정보도 안 남는다.
_PYTHON_PREFIXES = ("python", "python3")


def parse_node_graph(names_and_namespaces) -> list[dict]:
    """그래프 조회 결과를 (이름, 네임스페이스)별 개수로 접는다.

    같은 이름이 두 번 뜨면 목록에 두 번 나온다. 그게 중복의 정의다 —
    이 판정에는 프로세스 매칭이 필요 없으므로 항상 정확하다.
    """
    counts: dict[tuple[str, str], int] = {}
    for name, namespace in names_and_namespaces:
        counts[(name, namespace)] = counts.get((name, namespace), 0) + 1

    return [
        {"name": name, "namespace": namespace, "count": count}
        for (name, namespace), count in sorted(counts.items())
    ]


def split_cmdline(raw: bytes) -> list[str]:
    """/proc/<pid>/cmdline 은 NUL 로 구분된다. 마지막에도 NUL 이 붙는다."""
    if not raw:
        return []
    return [part for part in raw.decode("utf-8", "replace").split("\0") if part]


def executable_path(argv: list[str]) -> str | None:
    """이 프로세스가 실제로 실행한 파일.

    파이썬 노드는 argv[0] 이 인터프리터라 argv[1] 을 봐야 한다.
    """
    if not argv:
        return None
    first = Path(argv[0]).name
    if any(first.startswith(prefix) for prefix in _PYTHON_PREFIXES):
        for arg in argv[1:]:
            # 옵션이 아닌 첫 인자가 스크립트다.
            if not arg.startswith("-"):
                return arg
        return argv[0]
    return argv[0]


def node_names_from_cmdline(argv: list[str]) -> list[str]:
    """실행 인자의 `__node:=이름` 리매핑에서 노드 이름을 추정한다.

    ros2 launch 로 뜬 노드는 거의 항상 이 리매핑이 붙는다. 없으면 실행
    파일 이름으로 대신한다 — 못 찾았다고 빈 칸을 두면 화면에서 이 프로세스가
    무엇인지 알 수 없고, 그게 원래 문제였다.

    **이건 추정이다.** 중복 판정에는 쓰지 않는다.
    """
    names = [
        arg.split(":=", 1)[1]
        for arg in argv
        if arg.startswith("__node:=") and ":=" in arg
    ]
    if names:
        return names

    executable = executable_path(argv)
    return [Path(executable).name] if executable else []


def workspace_of(executable: str | None) -> str | None:
    """실행 경로에서 colcon 워크스페이스 루트를 역산한다.

        /home/pinky/mingky_care_pro/install/mingky_bringup/lib/...
            → /home/pinky/mingky_care_pro

    /opt/ros 아래는 워크스페이스가 아니라 배포판이다. None 을 돌려준다 —
    "이 로봇이 어느 커밋을 돌리는가" 라는 질문의 답이 아니기 때문이다.
    """
    if not executable:
        return None
    marker = "/install/"
    index = executable.find(marker)
    if index < 0:
        return None
    return executable[:index] or "/"


def parse_proc_stat(raw: str) -> tuple[float, str] | None:
    """/proc/<pid>/stat 에서 누적 CPU 초와 상태를 뽑는다.

    두 번째 필드(comm)에 괄호와 공백이 들어갈 수 있어 앞에서부터 자르면
    필드가 밀린다. 마지막 ')' 를 기준으로 나눠야 한다.
    """
    close = raw.rfind(")")
    if close < 0:
        return None
    rest = raw[close + 2:].split()
    # rest[0] 이 state(3번째 필드). utime 은 14번째 → rest[11], stime 은 rest[12].
    if len(rest) < 13:
        return None
    try:
        ticks = int(rest[11]) + int(rest[12])
    except ValueError:
        return None
    return ticks / _CLK_TCK, rest[0]


def cpu_percent(
    previous_seconds: float | None,
    current_seconds: float,
    elapsed_wall_sec: float,
) -> float:
    """주기 간 CPU 차분.

    첫 표본에는 이전 값이 없다. 그때 누적값을 그대로 퍼센트로 쓰면 11시간
    동안 돌던 노드가 첫 보고에서 수천 퍼센트로 찍힌다. 0 으로 둔다.
    """
    if previous_seconds is None or elapsed_wall_sec <= 0:
        return 0.0
    delta = current_seconds - previous_seconds
    if delta < 0:
        # PID 가 재사용됐다. 이전 값은 다른 프로세스의 것이다.
        return 0.0
    return round(delta / elapsed_wall_sec * 100.0, 1)


def inventory_hash(payload: dict) -> str:
    """인벤토리 내용의 지문. 8자리면 충돌 확률이 실용상 무시할 만하다.

    CPU 처럼 매번 바뀌는 값은 제외해야 한다. 넣으면 해시가 매 주기 바뀌어
    "변할 때만 보낸다" 는 설계가 무의미해진다.
    """
    stable = {
        "node_graph": payload.get("node_graph", []),
        "processes": [
            {
                "install_path": p.get("install_path"),
                "matched_node_names": p.get("matched_node_names"),
            }
            for p in payload.get("processes", [])
        ],
        "workspaces": [
            {
                "path": w.get("path"),
                "commit": w.get("commit"),
                "branch": w.get("branch"),
                "dirty": w.get("dirty"),
            }
            for w in payload.get("workspaces", [])
        ],
        "ros_domain_id": payload.get("ros_domain_id"),
    }
    encoded = json.dumps(stable, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:8]


class GitCache:
    """워크스페이스별 커밋 정보. subprocess 를 매 주기 부르지 않는다."""

    def __init__(self, ttl_sec: float = GIT_CACHE_SEC, runner=None):
        self.ttl_sec = ttl_sec
        # 테스트에서 갈아끼운다. 기본은 실제 git 호출.
        self._runner = runner or _run_git
        self._cache: dict[str, tuple[float, dict]] = {}

    def get(self, workspace: str, now: float | None = None) -> dict:
        now = time.monotonic() if now is None else now
        cached = self._cache.get(workspace)
        if cached is not None and now - cached[0] < self.ttl_sec:
            return cached[1]

        info = self._runner(workspace)
        self._cache[workspace] = (now, info)
        return info


def _run_git(workspace: str) -> dict:
    """커밋·브랜치·dirty. git 이 없거나 저장소가 아니면 전부 None 이다.

    dirty 를 따로 보는 이유는, 커밋 안 된 변경이 있는 워크스페이스는
    커밋 해시만으로 재현이 불가능하기 때문이다.
    """
    def git(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", workspace, *args],
                check=False, capture_output=True, text=True, timeout=5.0)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    commit = git("rev-parse", "--short", "HEAD")
    if commit is None:
        return {"commit": None, "branch": None, "dirty": False}

    status = git("status", "--porcelain")
    return {
        "commit": commit,
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(status),
    }


def scan_processes(proc_root: str = "/proc") -> list[dict]:
    """이 머신에서 도는 ROS 프로세스. 누적 CPU 초까지 함께 돌려준다.

    후보 판정은 넉넉하게 잡는다 — colcon install 공간에서 실행됐거나
    `--ros-args` 를 달고 있으면 ROS 프로세스로 본다. 놓치는 것보다
    몇 개 더 보고하는 쪽이 낫다. 원인 추적에 쓰는 자료이기 때문이다.
    """
    root = Path(proc_root)
    processes: list[dict] = []

    for entry in root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = split_cmdline((entry / "cmdline").read_bytes())
            if not argv:
                continue
            executable = executable_path(argv)
            if not _looks_like_ros(argv, executable):
                continue
            parsed = parse_proc_stat((entry / "stat").read_text())
        except (OSError, PermissionError):
            # 스캔 중 죽은 프로세스다. 이건 정상이라 조용히 넘긴다.
            continue
        if parsed is None:
            continue

        cpu_seconds, _state = parsed
        processes.append({
            "pid": int(entry.name),
            "install_path": executable or argv[0],
            "workspace_path": workspace_of(executable),
            "matched_node_names": node_names_from_cmdline(argv),
            "cpu_seconds_total": round(cpu_seconds, 2),
        })

    return sorted(processes, key=lambda p: p["pid"])


def _looks_like_ros(argv: list[str], executable: str | None) -> bool:
    if "--ros-args" in argv:
        return True
    if executable and "/install/" in executable:
        return True
    return False


def build_workspaces(processes: list[dict], git_cache: GitCache) -> list[dict]:
    """프로세스가 실제로 실행된 워크스페이스별로 커밋을 붙인다.

    정상 배치에서는 하나여야 한다. 둘 이상이면 서로 다른 코드가 한 로봇에서
    같이 도는 것이고, 그 상태에서는 무엇을 고쳐야 하는지 알 수 없다.
    판정과 경고는 서버가 한다 — 여기서는 사실만 센다.
    """
    counts: dict[str, int] = {}
    for process in processes:
        path = process.get("workspace_path")
        if path:
            counts[path] = counts.get(path, 0) + 1

    return [
        {"path": path, "process_count": count, **git_cache.get(path)}
        for path, count in sorted(counts.items())
    ]


def parse_total_cpu(raw: str) -> tuple[float, float] | None:
    """/proc/stat 첫 줄에서 (일한 시간, 전체 시간) 을 뽑는다.

    노드별 CPU 만 보면 "이 로봇이 지금 버거운가" 를 알 수 없다. 노드 하나가
    100% 여도 코어가 4개면 여유가 있고, 노드들이 조금씩 먹어도 합이 포화면
    주행이 흔들린다.
    """
    for line in raw.splitlines():
        if not line.startswith("cpu "):
            continue
        fields = [int(value) for value in line.split()[1:] if value.isdigit()]
        if len(fields) < 4:
            return None
        total = sum(fields)
        # 4번째가 idle, 5번째가 iowait. 둘 다 일한 시간이 아니다.
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
        return (total - idle) / _CLK_TCK, total / _CLK_TCK
    return None


def total_cpu_percent(
    previous: tuple[float, float] | None,
    current: tuple[float, float] | None,
) -> float | None:
    """전체 CPU 사용률. 첫 표본에는 이전 값이 없어 None 이다."""
    if previous is None or current is None:
        return None
    busy_delta = current[0] - previous[0]
    total_delta = current[1] - previous[1]
    if total_delta <= 0 or busy_delta < 0:
        return None
    return round(busy_delta / total_delta * 100.0, 1)


def busiest_process(processes: list[dict]) -> dict | None:
    """CPU 를 가장 많이 쓰는 프로세스. heartbeat 배지에 쓴다.

    상세는 인벤토리에서 본다. heartbeat 는 5초 주기라 payload 를 키우면
    안 되므로 이름과 퍼센트 하나씩만 싣는다.
    """
    with_cpu = [p for p in processes if p.get("cpu_pct") is not None]
    if not with_cpu:
        return None
    return max(with_cpu, key=lambda p: p["cpu_pct"])
