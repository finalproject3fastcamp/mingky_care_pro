"""인벤토리 수집의 순수 로직 검증.

/proc 과 git 은 건드리지 않는다. 파싱과 판정만 본다 — 그쪽이 틀리면
원인 추적용 자료가 조용히 거짓말을 하게 된다.
"""

from mingky_event_gateway import inventory


def test_duplicate_nodes_are_counted_from_the_graph():
    graph = [
        ('battery_publisher', '/'),
        ('battery_publisher', '/'),
        ('guide_manager', '/'),
    ]

    result = inventory.parse_node_graph(graph)

    # 중복 판정에는 프로세스 매칭이 필요 없다. 그래서 항상 정확하다.
    counts = {item['name']: item['count'] for item in result}
    assert counts == {'battery_publisher': 2, 'guide_manager': 1}


def test_same_name_in_different_namespaces_is_not_a_duplicate():
    graph = [('camera', '/front'), ('camera', '/rear')]

    result = inventory.parse_node_graph(graph)

    assert [item['count'] for item in result] == [1, 1]


def test_python_nodes_report_the_script_not_the_interpreter():
    argv = ['/usr/bin/python3', '/home/pinky/ws/install/pkg/lib/pkg/node.py']

    # argv[0] 을 쓰면 모든 파이썬 노드가 /usr/bin/python3 로 뭉쳐
    # 아무 정보도 안 남는다.
    assert inventory.executable_path(argv) == argv[1]


def test_node_name_comes_from_the_ros_remap_when_present():
    argv = ['/ws/install/pkg/lib/pkg/node', '--ros-args', '-r', '__node:=battery_guard']

    assert inventory.node_names_from_cmdline(argv) == ['battery_guard']


def test_node_name_falls_back_to_the_executable_name():
    argv = ['/ws/install/pkg/lib/pkg/adc_reader', '--ros-args']

    # 못 찾았다고 빈 칸을 두면 이 프로세스가 무엇인지 알 수 없고,
    # 그게 원래 문제였다.
    assert inventory.node_names_from_cmdline(argv) == ['adc_reader']


def test_workspace_is_derived_from_the_install_space():
    path = '/home/pinky/mingky_care_pro/install/mingky_bringup/lib/x/node'

    assert inventory.workspace_of(path) == '/home/pinky/mingky_care_pro'


def test_ros_distribution_is_not_a_workspace():
    # "이 로봇이 어느 커밋을 돌리는가" 의 답이 아니다.
    assert inventory.workspace_of('/opt/ros/jazzy/lib/rclcpp/node') is None
    assert inventory.workspace_of(None) is None


def test_proc_stat_survives_a_comm_field_with_spaces_and_parens():
    # comm 에 괄호와 공백이 들어갈 수 있어 앞에서부터 자르면 필드가 밀린다.
    # 각 값이 자기 필드 번호와 같게 만든다 — state 가 3번, utime 이 14번,
    # stime 이 15번이다.
    fields = ' '.join(str(n) for n in range(4, 40))
    raw = f'1234 (my node (2)) S {fields}'

    parsed = inventory.parse_proc_stat(raw)

    assert parsed is not None
    seconds, state = parsed
    assert state == 'S'
    assert seconds == (14 + 15) / inventory._CLK_TCK


def test_first_cpu_sample_reports_zero_not_the_lifetime_total():
    # 11시간 동안 돌던 노드가 첫 보고에서 수천 퍼센트로 찍히면 안 된다.
    assert inventory.cpu_percent(None, 42000.0, 30.0) == 0.0


def test_cpu_percent_is_the_delta_over_wall_time():
    assert inventory.cpu_percent(100.0, 115.0, 30.0) == 50.0


def test_recycled_pid_does_not_report_negative_cpu():
    assert inventory.cpu_percent(500.0, 3.0, 30.0) == 0.0


def test_hash_ignores_cpu_so_it_only_changes_when_content_does():
    base = {
        'node_graph': [{'name': 'a', 'namespace': '/', 'count': 1}],
        'processes': [{
            'pid': 1, 'install_path': '/ws/install/a',
            'matched_node_names': ['a'], 'cpu_pct': 3.0,
        }],
        'workspaces': [{'path': '/ws', 'commit': 'abc', 'branch': 'main',
                        'dirty': False}],
        'ros_domain_id': 0,
    }
    busier = {**base, 'processes': [{**base['processes'][0], 'cpu_pct': 99.9}]}

    # CPU 를 해시에 넣으면 매 주기 바뀌어 "변할 때만 보낸다" 가 무의미해진다.
    assert inventory.inventory_hash(base) == inventory.inventory_hash(busier)


def test_hash_changes_when_the_commit_changes():
    base = {
        'node_graph': [],
        'processes': [],
        'workspaces': [{'path': '/ws', 'commit': 'abc', 'branch': 'main',
                        'dirty': False}],
        'ros_domain_id': 0,
    }
    moved = {**base, 'workspaces': [{**base['workspaces'][0], 'commit': 'def'}]}

    assert inventory.inventory_hash(base) != inventory.inventory_hash(moved)


def test_hash_changes_when_a_workspace_goes_dirty():
    base = {
        'node_graph': [], 'processes': [], 'ros_domain_id': 0,
        'workspaces': [{'path': '/ws', 'commit': 'abc', 'branch': 'main',
                        'dirty': False}],
    }
    dirty = {**base, 'workspaces': [{**base['workspaces'][0], 'dirty': True}]}

    # 커밋 안 된 변경이 있으면 커밋 해시만으로 재현이 불가능하다.
    assert inventory.inventory_hash(base) != inventory.inventory_hash(dirty)


def test_git_is_not_called_again_within_the_cache_window():
    calls = []

    def runner(workspace):
        calls.append(workspace)
        return {'commit': 'abc1234', 'branch': 'main', 'dirty': False}

    cache = inventory.GitCache(ttl_sec=300.0, runner=runner)

    cache.get('/ws', now=1000.0)
    cache.get('/ws', now=1200.0)
    assert calls == ['/ws']

    cache.get('/ws', now=1400.0)
    assert calls == ['/ws', '/ws']


def test_workspaces_count_the_processes_that_ran_from_them():
    processes = [
        {'workspace_path': '/home/pinky/mingky_care_pro'},
        {'workspace_path': '/home/pinky/mingky_care_pro'},
        {'workspace_path': '/home/pinky/wmk'},
        {'workspace_path': None},
    ]
    cache = inventory.GitCache(
        runner=lambda ws: {'commit': 'abc', 'branch': 'main', 'dirty': False})

    result = inventory.build_workspaces(processes, cache)

    assert [(w['path'], w['process_count']) for w in result] == [
        ('/home/pinky/mingky_care_pro', 2),
        ('/home/pinky/wmk', 1),
    ]


def test_total_cpu_excludes_idle_and_iowait():
    # user nice system idle iowait ...
    raw = 'cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 1 1 1 1\n'

    busy, total = inventory.parse_total_cpu(raw)

    assert busy == 200 / inventory._CLK_TCK
    assert total == 1000 / inventory._CLK_TCK


def test_total_cpu_percent_needs_two_samples():
    assert inventory.total_cpu_percent(None, (1.0, 2.0)) is None
    assert inventory.total_cpu_percent((100.0, 1000.0), (150.0, 1100.0)) == 50.0


def test_busiest_process_ignores_ones_without_a_reading():
    processes = [
        {'pid': 1, 'cpu_pct': None},
        {'pid': 2, 'cpu_pct': 12.0},
        {'pid': 3, 'cpu_pct': 99.9},
    ]

    assert inventory.busiest_process(processes)['pid'] == 3
    assert inventory.busiest_process([{'pid': 1, 'cpu_pct': None}]) is None
