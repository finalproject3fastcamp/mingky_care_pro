"""safety_watch 판정 규칙 검증. 로봇·GPU·서버 없이 돈다.

    python3 -m pytest omx/web/test_safety_watch.py -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safety_watch import SafetyWatch  # noqa: E402


class FakeImage:
    """ndim 만 흉내 낸다 — 판정 모듈은 이미지 내용을 안 본다."""
    ndim = 3


def obs(cams=('top', 'wrist')):
    o = {name: FakeImage() for name in cams}
    o['shoulder_pan.pos'] = 12.3       # 관절값은 걸러져야 한다
    return o


def make(post, **kw):
    calls = []
    warns = []
    w = SafetyWatch(
        'http://x/infer',
        post=lambda url, jpeg, t: post(calls),
        encode=lambda img: b'jpg',
        warn=warns.append,
        **kw)
    return w, calls, warns


def person(conf=0.9):
    return [{'class': 'person', 'conf': conf, 'x': 0, 'y': 0, 'w': 10, 'h': 10}]


def test_every_gates_frames():
    """N프레임마다 한 번만 서버에 묻는다."""
    w, calls, _ = make(lambda c: c.append(1) or [], every=3)
    for frame in range(6):
        w.check(obs(), frame)
    # 프레임 0,3 에서만 — 카메라가 2대라 호출은 2배
    assert len(calls) == 2 * 2


def test_one_hit_is_not_enough_two_in_a_row_stops():
    """오검출 한 번으로는 안 세우고, 연속 2회면 세운다."""
    answers = [person(), [], person(), person()]
    w, _, _ = make(lambda c: answers.pop(0), every=1, hits_needed=2)
    assert w.check(obs(['top']), 0) is None      # 1회째
    assert w.check(obs(['top']), 1) is None      # 끊김 — 리셋
    assert w.check(obs(['top']), 2) is None      # 다시 1회째
    trip = w.check(obs(['top']), 3)              # 연속 2회째
    assert trip is not None
    assert trip['카메라'] == 'top'
    assert '사람' in trip['이유']


def test_low_conf_and_other_classes_ignored():
    """기준 미달 확신·다른 클래스는 사람이 아니다."""
    answers = [
        [{'class': 'person', 'conf': 0.2}],      # 확신 미달
        [{'class': 'bottle', 'conf': 0.99}],     # 다른 클래스
    ]
    w, _, _ = make(lambda c: answers.pop(0), every=1, conf=0.35, hits_needed=1)
    assert w.check(obs(['top']), 0) is None
    assert w.check(obs(['top']), 1) is None


def test_wrist_camera_counts_too():
    """손목 카메라에 잡혀도 선다."""
    seq = {'n': 0}

    def post(calls):
        seq['n'] += 1
        return person(0.8) if seq['n'] % 2 == 0 else []   # 두 번째 카메라만

    w, _, _ = make(post, every=1, hits_needed=1)
    trip = w.check(obs(['top', 'wrist']), 0)
    assert trip is not None


def test_server_down_fail_open_warns_once():
    """서버가 죽으면 경고 한 번 남기고 계속 간다."""
    def post(calls):
        raise ConnectionError('연결 거부')

    w, _, warns = make(post, every=1)
    for frame in range(4):
        assert w.check(obs(['top']), frame) is None
    assert len(warns) == 1
    assert '감시 없이 계속' in warns[0]


def test_server_down_required_stops():
    """required=True 면 감시를 못 하는 순간 세운다 (fail-closed)."""
    def post(calls):
        raise ConnectionError('연결 거부')

    w, _, _ = make(post, every=1, required=True)
    trip = w.check(obs(['top']), 0)
    assert trip is not None
    assert '안전 감시를 할 수 없습니다' in trip['이유']


def test_recovery_after_down_announces_and_needs_fresh_streak():
    """서버가 돌아오면 알리고, 끊기기 전 횟수는 이어 세지 않는다."""
    answers = ['down', 'down', person(), person()]

    def post(calls):
        a = answers.pop(0)
        if a == 'down':
            raise ConnectionError('x')
        return a

    w, _, warns = make(post, every=1, hits_needed=2)
    w.check(obs(['top']), 0)
    w.check(obs(['top']), 1)
    assert w.check(obs(['top']), 2) is None      # 복구 + 1회째
    assert any('다시 응답' in m for m in warns)
    assert w.check(obs(['top']), 3) is not None  # 연속 2회째


def test_strongest_camera_reported():
    """두 카메라에 다 보이면 확신 높은 쪽을 보고한다."""
    seq = {'n': 0}

    def post(calls):
        seq['n'] += 1
        return person(0.5) if seq['n'] % 2 == 1 else person(0.9)

    w, _, _ = make(post, every=1, hits_needed=1)
    trip = w.check(obs(['top', 'wrist']), 0)
    assert trip['확신'] == pytest.approx(0.9)
    assert trip['카메라'] == 'wrist'
