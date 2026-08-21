"""robot_id 별 job 분리 · 원격 러너 프록시 · 세션 연결 조제.

카메라도 로봇도 DB 도 없이 확인할 수 있는 경계다.

  - 조제(omx-01)와 포장(omx-02)이 서로 다른 job 으로 동시에 돈다
  - robot_id 를 주지 않는 기존 호출은 전역 `_JOB` 그대로 (하위호환)
  - 원격 러너 URL 이 설정되면 로컬 서브프로세스 대신 HTTP 로 프록시한다
  - 도착(pharmacy.arrived) → dispense_requested → 조제 → dispense_completed 배관
  - 새 이벤트 코드 payload 가 event_codes.yaml 스키마와 정확히 일치한다
"""

import asyncio
from pathlib import Path

import pytest
import yaml

from app import pharmacy, pharmacy_link

_CODES = yaml.safe_load(
    (Path(pharmacy.__file__).resolve().parents[2] / "config" / "event_codes.yaml")
    .read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch):
    """asyncio.Lock 은 처음 await 된 루프에 묶인다. 테스트마다 새로 만든다."""
    pharmacy._JOB = pharmacy._new_job()
    pharmacy._JOBS = {}
    pharmacy._JOB_PROC = None
    pharmacy._JOB_PROCS = {}
    pharmacy._JOB_LOCK = asyncio.Lock()
    pharmacy._SUBSCRIBERS = set()
    monkeypatch.setattr(pharmacy, "REAL_MODE", False)

    async def _meds():
        return {c: {"이름": "약", "색이름": c} for c in ("red", "green", "yellow")}
    monkeypatch.setattr(pharmacy, "_get_meds", _meds)
    yield
    pharmacy._JOB_PROC = None
    pharmacy._JOBS = {}
    pharmacy._JOB_PROCS = {}


@pytest.fixture
def _fast_sim(monkeypatch):
    async def _instant(*a, **k):
        return
    monkeypatch.setattr(pharmacy.asyncio, "sleep", _instant)


# ── job 분리 ────────────────────────────────────────────────────────────────
def test_default_robot_is_the_module_global():
    """robot_id 미지정은 전역 _JOB 을 그대로 쓴다 (기존 호출·테스트 호환)."""
    assert pharmacy._job() is pharmacy._JOB
    assert pharmacy._job(pharmacy.DEFAULT_ROBOT_ID) is pharmacy._JOB


def test_other_stations_get_their_own_job():
    a = pharmacy._job("omx-02")
    b = pharmacy._job("omx-02")
    assert a is b                       # 같은 스테이션은 같은 dict
    assert a is not pharmacy._JOB       # 기본 스테이션과는 다르다
    a["상태"] = "포장중"
    assert pharmacy._JOB["상태"] == "대기"   # 서로 새지 않는다


def test_procs_are_tracked_per_robot():
    sentinel = object()
    pharmacy._set_proc("omx-02", sentinel)
    assert pharmacy._get_proc("omx-02") is sentinel
    # 기본 스테이션 proc(트레이 게이트가 보는 전역)은 안 건드린다.
    assert pharmacy._JOB_PROC is None
    pharmacy._set_proc(None, sentinel)
    assert pharmacy._JOB_PROC is sentinel


# ── 조제·포장 동시 ────────────────────────────────────────────────────────────
def test_dispense_and_pack_run_concurrently_on_two_stations(_fast_sim):
    """omx-01 조제와 omx-02 포장이 서로 다른 job 으로 동시에 완주한다."""
    async def _run():
        pharmacy._JOB_LOCK = asyncio.Lock()
        d = asyncio.create_task(
            pharmacy._dispense_worker("d1", {"조합": ["red", "green"]}, "xy", None))
        p = asyncio.create_task(pharmacy._pack_worker("p1", "omx-02"))
        await asyncio.gather(d, p)
    asyncio.run(_run())

    assert pharmacy._job(None)["상태"] == "조제완료"
    assert pharmacy._job("omx-02")["상태"] == "완료"
    # 조제 단계는 기본 job 에만, 포장은 omx-02 job 에만 쌓였다.
    assert len(pharmacy._job(None)["단계"]) == 2
    assert pharmacy._job("omx-02")["단계"] == []


def test_stop_targets_only_the_named_station():
    async def _run():
        pharmacy._JOB_LOCK = asyncio.Lock()
        await pharmacy.stop_dispense("omx-02")
    asyncio.run(_run())

    assert pharmacy._job("omx-02")["중단요청"] is True
    assert pharmacy._job(None)["중단요청"] is False


# ── 원격 러너 프록시 ──────────────────────────────────────────────────────────
def test_remote_url_is_read_from_env(monkeypatch):
    monkeypatch.delenv("MINGKY_OMX_DISPENSE_URL", raising=False)
    assert pharmacy._dispense_runner_url() is None
    monkeypatch.setenv("MINGKY_OMX_DISPENSE_URL", "http://box/")
    assert pharmacy._dispense_runner_url() == "http://box"   # 끝 슬래시 제거


def test_run_sequence_proxies_to_remote_runner(monkeypatch, _fast_sim):
    """원격 URL 이 있으면 로컬 서브프로세스 대신 러너로 프록시한다."""
    monkeypatch.setenv("MINGKY_OMX_DISPENSE_URL", "http://box:8800")
    calls = []
    # 러너 상태를 흉내 낸다: 폴링할 때마다 한 단계씩 진행하다 완료.
    states = iter([
        {"상태": "진행", "완료단계": 0},
        {"상태": "진행", "완료단계": 1},
        {"상태": "완료", "완료단계": 2},
    ])

    def _fake_http(url, method, body=None):
        calls.append((method, url, body))
        if url.endswith("/dispense/start"):
            return {"상태": "진행", "총단계": 2}
        if url.endswith("/dispense/state"):
            return next(states)
        return {}
    monkeypatch.setattr(pharmacy, "_http_json", _fake_http)

    ok, memo = asyncio.run(pharmacy._run_sequence(["red", "green"], "xy", "omx-01"))

    assert (ok, memo) == (True, "완료")
    assert calls[0][0] == "POST" and calls[0][1].endswith("/dispense/start")
    assert calls[0][2] == {"sequence": ["red", "green"], "policy": "xy"}
    # 로컬 서브프로세스는 생성되지 않았다(러너가 대신했다).
    assert pharmacy._JOB_PROC is None


def test_remote_stop_forwards_to_runner(monkeypatch):
    monkeypatch.setenv("MINGKY_OMX_DISPENSE_URL", "http://box:8800")
    seen = []

    def _fake_http(url, method, body=None):
        seen.append(url)
        if url.endswith("/dispense/start"):
            return {"상태": "진행"}
        if url.endswith("/dispense/state"):
            return {"상태": "진행", "완료단계": 0}
        return {}
    monkeypatch.setattr(pharmacy, "_http_json", _fake_http)

    async def _run():
        pharmacy._JOB_LOCK = asyncio.Lock()
        pharmacy._job("omx-01")["중단요청"] = True   # 시작하자마자 중단
        return await pharmacy._run_sequence(["red"], "xy", "omx-01")
    ok, memo = asyncio.run(_run())

    assert ok is False and memo == "사용자가 중단했습니다"
    assert any(u.endswith("/dispense/stop") for u in seen)


def test_remote_error_surfaces(monkeypatch):
    monkeypatch.setenv("MINGKY_OMX_DISPENSE_URL", "http://box:8800")

    def _fake_http(url, method, body=None):
        if url.endswith("/dispense/start"):
            return {"오류": "러너에 연결하지 못했습니다"}
        return {}
    monkeypatch.setattr(pharmacy, "_http_json", _fake_http)

    ok, memo = asyncio.run(pharmacy._run_sequence(["red"], "xy", "omx-01"))
    assert ok is False and "연결" in memo


# ── 원격 프록시가 manipulator 사이클 이벤트를 직접 INSERT 한다 ────────────────────
# 클라우드 백엔드는 MINGKY_OMX_ROBOT_ID 게이트(_report)가 꺼져 있어 이벤트가
# 0건이었다. 원격 경로는 events 에 직접 넣어 관제 조제 패널을 채운다.
# (_FakeConn/_FakePool/_emitted 는 아래 세션 절에서 정의 — 런타임에 해소된다.)
def test_remote_dispense_emits_cycle_events(monkeypatch, _fast_sim):
    """원격 조제가 policy_loaded → cycle_started → cycle_completed 를 남긴다.

    robot_id 는 조제 스테이션(omx-01)로 태깅되고, pick_* 는 발행하지 않는다
    (ACT 는 pick ground truth 를 주지 않는다)."""
    monkeypatch.setenv("MINGKY_OMX_DISPENSE_URL", "http://box:8800")
    rec = []
    conn = _FakeConn(rec)
    monkeypatch.setattr(pharmacy, "get_pool", lambda: _FakePool(conn))

    def _fake_http(url, method, body=None):
        if url.endswith("/dispense/start"):
            return {"상태": "진행"}
        if url.endswith("/dispense/state"):
            return {"상태": "완료", "완료단계": 2}
        return {}
    monkeypatch.setattr(pharmacy, "_http_json", _fake_http)

    pharmacy._job("omx-01")["id"] = "d42"
    ok, memo = asyncio.run(pharmacy._run_sequence(["red", "green"], "xy", "omx-01"))
    assert (ok, memo) == (True, "완료")

    emitted = _emitted(rec)
    codes = [c for c, _ in emitted]
    assert codes == ["manipulator.policy_loaded",
                     "manipulator.cycle_started",
                     "manipulator.cycle_completed"]
    assert not any("pick" in c for c in codes)        # pick 은 발행하지 않는다
    # 조제 스테이션(omx-01)에 태깅된다. robot_id 는 args[1].
    assert all(a[1] == "omx-01" for sql, a in rec if "events" in sql)

    by = dict(emitted)
    assert by["manipulator.cycle_started"] == {
        "dispense_id": "d42", "medication_id": "red+green"}
    assert by["manipulator.cycle_completed"]["dispense_id"] == "d42"
    assert isinstance(by["manipulator.cycle_completed"]["duration_ms"], int)
    # payload 키가 정본 스키마(event_codes.yaml)와 정확히 일치.
    for code, payload in emitted:
        assert set(payload) == set(_CODES[code]["payload"]), code


def test_remote_dispense_error_emits_cycle_aborted(monkeypatch, _fast_sim):
    """원격 조제가 실패하면 cycle_aborted(reason) 를 남긴다 — 완료는 없다."""
    monkeypatch.setenv("MINGKY_OMX_DISPENSE_URL", "http://box:8800")
    rec = []
    conn = _FakeConn(rec)
    monkeypatch.setattr(pharmacy, "get_pool", lambda: _FakePool(conn))

    def _fake_http(url, method, body=None):
        if url.endswith("/dispense/start"):
            return {"오류": "러너에 연결하지 못했습니다"}
        return {}
    monkeypatch.setattr(pharmacy, "_http_json", _fake_http)

    ok, _ = asyncio.run(pharmacy._run_sequence(["red"], "xy", "omx-01"))
    assert ok is False
    codes = [c for c, _ in _emitted(rec)]
    assert "manipulator.cycle_aborted" in codes
    assert "manipulator.cycle_completed" not in codes
    aborted = dict(_emitted(rec))["manipulator.cycle_aborted"]
    assert "연결" in aborted["reason"]
    assert set(aborted) == set(_CODES["manipulator.cycle_aborted"]["payload"])


def test_remote_pack_emits_cycle_events_on_pack_station(monkeypatch, _fast_sim):
    """원격 포장은 포장 박스(omx-02)에 사이클 이벤트를 남긴다 (dispense_id …-pack)."""
    monkeypatch.setenv("MINGKY_OMX_PACK_URL", "http://box2:8800")
    rec = []
    conn = _FakeConn(rec)
    monkeypatch.setattr(pharmacy, "get_pool", lambda: _FakePool(conn))

    def _fake_http(url, method, body=None):
        if url.endswith("/pack/start"):
            return {"상태": "진행"}
        if url.endswith("/pack/state"):
            return {"상태": "완료"}
        return {}
    monkeypatch.setattr(pharmacy, "_http_json", _fake_http)

    pharmacy._job("omx-02")["id"] = "p7"
    ok, _ = asyncio.run(pharmacy._run_pack("omx-02"))
    assert ok is True

    emitted = _emitted(rec)
    codes = [c for c, _ in emitted]
    assert "manipulator.cycle_started" in codes
    assert "manipulator.cycle_completed" in codes
    assert all(a[1] == "omx-02" for sql, a in rec if "events" in sql)
    assert dict(emitted)["manipulator.cycle_started"]["dispense_id"] == "p7-pack"
    for code, payload in emitted:
        assert set(payload) == set(_CODES[code]["payload"]), code


# ── 세션 연결 조제 (pharmacy_link) ────────────────────────────────────────────
class _FakeConn:
    def __init__(self, recorder, fail_insert_job=False):
        self.rec = recorder
        self.fail_insert_job = fail_insert_job

    async def execute(self, sql, *args):
        if "INSERT INTO dispense_jobs" in sql and self.fail_insert_job:
            raise RuntimeError("uq_active_dispense_session")
        self.rec.append((sql.strip().split()[0] + " " + _table(sql), args))


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False
        return _Ctx()


def _table(sql: str) -> str:
    for key in ("dispense_jobs", "events"):
        if key in sql:
            return key
    return "?"


def _emitted(recorder):
    """emit 된 events 의 (code, payload) 를 편다. payload 는 args[7] JSON 문자열."""
    import json
    out = []
    for sql, args in recorder:
        if "events" in sql and args:
            out.append((args[5], json.loads(args[7])))
    return out


def test_arrived_starts_and_completes_a_session_dispense(monkeypatch):
    rec = []
    conn = _FakeConn(rec)
    monkeypatch.setattr(pharmacy_link, "get_pool", lambda: _FakePool(conn))

    async def _rx(pid):
        return {"조합": ["red", "green"], "환자": {"id": pid, "이름": "홍길동"}}
    monkeypatch.setattr(pharmacy, "prescription_for_patient", _rx)

    ran = {}

    async def _run(dispense_id, 처방, policy_id, robot_id):
        ran["args"] = (dispense_id, 처방["조합"], robot_id)
        return True
    monkeypatch.setattr(pharmacy, "run_session_dispense", _run)

    asyncio.run(pharmacy_link.on_pharmacy_arrived(7, "p001"))

    # 조제가 omx-01 에서 실제 처방으로 시작됐다.
    assert ran["args"][1] == ["red", "green"]
    assert ran["args"][2] == "omx-01"

    codes = [c for c, _ in _emitted(rec)]
    assert codes == ["pharmacy.dispense_requested", "pharmacy.dispense_completed"]

    requested = _emitted(rec)[0][1]
    assert requested == {"session_id": 7, "patient_id": "p001",
                         "omx_robot_id": "omx-01"}
    completed = _emitted(rec)[1][1]
    assert completed["session_id"] == 7
    assert completed["dispense_id"] == ran["args"][0]

    # dispense_jobs 가 삽입되고 완료로 갱신됐다.
    ops = [sql for sql, _ in rec if "dispense_jobs" in sql]
    assert any("INSERT" in o for o in ops)
    assert any("UPDATE" in o for o in ops)


def test_duplicate_arrival_does_not_start_a_second_dispense(monkeypatch):
    """도착 이벤트가 재전송돼 이미 조제가 걸려 있으면 두 번 시작하지 않는다."""
    rec = []
    conn = _FakeConn(rec, fail_insert_job=True)     # 유니크 위반
    monkeypatch.setattr(pharmacy_link, "get_pool", lambda: _FakePool(conn))

    async def _rx(pid):
        return {"조합": ["red"], "환자": {"id": pid, "이름": "홍길동"}}
    monkeypatch.setattr(pharmacy, "prescription_for_patient", _rx)

    started = []

    async def _run(*a):
        started.append(a)
        return True
    monkeypatch.setattr(pharmacy, "run_session_dispense", _run)

    asyncio.run(pharmacy_link.on_pharmacy_arrived(7, "p001"))

    assert started == []                     # 조제를 시작하지 않았다
    assert _emitted(rec) == []               # requested/completed 도 없다


def test_no_prescription_skips_without_emitting(monkeypatch):
    rec = []
    conn = _FakeConn(rec)
    monkeypatch.setattr(pharmacy_link, "get_pool", lambda: _FakePool(conn))

    async def _rx(pid):
        return None
    monkeypatch.setattr(pharmacy, "prescription_for_patient", _rx)

    asyncio.run(pharmacy_link.on_pharmacy_arrived(7, "pXXX"))
    assert rec == []


def test_aborted_dispense_does_not_emit_completed(monkeypatch):
    rec = []
    conn = _FakeConn(rec)
    monkeypatch.setattr(pharmacy_link, "get_pool", lambda: _FakePool(conn))

    async def _rx(pid):
        return {"조합": ["red"], "환자": {"id": pid, "이름": "x"}}
    monkeypatch.setattr(pharmacy, "prescription_for_patient", _rx)

    async def _run(*a):
        return False                         # 중단/오류
    monkeypatch.setattr(pharmacy, "run_session_dispense", _run)

    asyncio.run(pharmacy_link.on_pharmacy_arrived(7, "p001"))

    codes = [c for c, _ in _emitted(rec)]
    assert "pharmacy.dispense_requested" in codes
    assert "pharmacy.dispense_completed" not in codes
    # dispense_jobs 는 aborted 로 갱신됐다.
    assert any("UPDATE" in sql and "dispense_jobs" in sql for sql, _ in rec)


# ── 새 이벤트 코드가 정본과 일치 ──────────────────────────────────────────────
def test_new_codes_match_the_canonical_schema(monkeypatch):
    rec = []
    conn = _FakeConn(rec)
    monkeypatch.setattr(pharmacy_link, "get_pool", lambda: _FakePool(conn))

    async def _rx(pid):
        return {"조합": ["red"], "환자": {"id": pid, "이름": "x"}}
    monkeypatch.setattr(pharmacy, "prescription_for_patient", _rx)

    async def _run(*a):
        return True
    monkeypatch.setattr(pharmacy, "run_session_dispense", _run)

    asyncio.run(pharmacy_link.on_pharmacy_arrived(7, "p001"))

    for code, payload in _emitted(rec):
        assert code in _CODES, f"{code} 가 event_codes.yaml 에 없다"
        assert _CODES[code]["robot_types"] == ["manipulator"]
        # payload 키가 정본과 정확히 일치(누락도 초과도 없다).
        assert set(payload) == set(_CODES[code]["payload"]), code
