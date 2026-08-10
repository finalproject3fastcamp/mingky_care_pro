"""진료 순서 이름과 병원 지도 waypoint 연결을 검증한다."""

from pathlib import Path

import yaml


CONFIG_FILE = (
    Path(__file__).resolve().parents[1]
    / 'config' / 'waypoints' / 'yun_map_highres_clean_waypoints.yaml'
)

SEED_VISITS = {'X-ray', '임상병리실', '물리치료실', 'CT', 'MRI'}


def test_every_seed_visit_has_distinct_goal_and_waiting_waypoints() -> None:
    data = yaml.safe_load(CONFIG_FILE.read_text(encoding='utf-8'))
    mappings = data['visit_waypoints']
    waypoints = data['waypoints']

    assert SEED_VISITS <= mappings.keys()
    for visit_name in SEED_VISITS:
        mapping = mappings[visit_name]
        assert set(mapping) == {'goal', 'waiting'}
        assert mapping['goal'] in waypoints
        assert mapping['waiting'] in waypoints
        assert mapping['goal'] != mapping['waiting']
