"""알림 라우팅 (§8.4 · 로드맵 12).

원칙 4 — 대시보드에만 있고 아무도 안 보는 지표는 없는 것과 같다. 그런데 알림은
**너무 많이 보내면 정확히 같은 결과**가 된다. 그래서 이 파일이 잠그는 것의
대부분은 "무엇을 안 보내는가" 다.

  확률적 실패(pick_failed)가 절대 안 나가는가
  두절 복구 배치의 10분 전 사건이 현재형으로 안 나가는가
  같은 사건이 반복 발송되지 않는가
  적재 경로가 알림 때문에 막히지 않는가
"""

import uuid
from datetime import datetime, timedelta, timezone

from app import notify
from app.notify import AlertRoutes
from app.schemas import EventIn

NOW = datetime(2026, 8, 16, 9, 0, tzinfo=timezone.utc)


def _routes(**overrides):
    codes = {
        "fire.detected": {"tier": "page", "text": "화재 감지"},
        "robot.comm_lost": {"tier": "page", "text": "통신 두절"},
        "manipulator.servo_fault": {"tier": "notify", "text": "서보 결함"},
    }
    return AlertRoutes(
        codes=overrides.get("codes", codes),
        throttle_sec=overrides.get("throttle_sec", 300.0),
        max_age_sec=overrides.get("max_age_sec", 300.0),
        max_per_batch=overrides.get("max_per_batch", 5),
    )


def _event(code: str, robot_id="pinky-01", at=NOW, payload=None,
           level="error") -> EventIn:
    return EventIn(
        event_id=uuid.uuid4(), robot_id=robot_id, session_id=0,
        occurred_at=at, level=level, event_code=code,
        source_node="test", payload=payload or {})


def _select(events, now=NOW, routes=None):
    notify.reset()
    return notify.select(events, now=now, routes=routes or _routes())


# --- 무엇을 보내는가 ----------------------------------------------------------

def test_serious_events_go_out():
    alerts = _select([_event("fire.detected", payload={"detections": 3})])

    assert [a.event_code for a in alerts] == ["fire.detected"]
    assert alerts[0].tier == "page"
    # 코드 이름만 보내면 받는 사람이 저장소를 열어야 뜻을 안다.
    assert "화재 감지" in notify.message(alerts[0])
    assert "pinky-01" in notify.message(alerts[0])


def test_everything_else_stays_on_the_dashboard():
    """알림의 가치는 희소성에서 나온다. 목록에 없으면 안 보낸다."""
    assert _select([_event("nav.stuck", level="warning"),
                    _event("session.started", level="info")]) == []


def test_probabilistic_failures_never_leave_the_screen():
    """모방학습 pick 실패는 정상 동작 범위다(§4.4). 이게 알림으로 나가면
    하루에 수십 번 울리고, 그때부터 아무도 이 채널을 안 본다."""
    alerts = _select([_event("manipulator.pick_failed", robot_id="omx-01",
                             level="warning")])

    assert alerts == []


# --- 무엇을 막는가 ------------------------------------------------------------

def test_the_same_fact_is_not_repeated_within_the_window():
    routes = _routes()
    notify.reset()

    first = notify.select([_event("robot.comm_lost")], now=NOW, routes=routes)
    again = notify.select([_event("robot.comm_lost")],
                          now=NOW + timedelta(seconds=60), routes=routes)

    assert len(first) == 1
    assert again == []


def test_the_same_code_on_another_robot_is_a_different_fact():
    routes = _routes()
    notify.reset()

    notify.select([_event("robot.comm_lost", robot_id="pinky-01")],
                  now=NOW, routes=routes)
    other = notify.select([_event("robot.comm_lost", robot_id="pinky-02")],
                          now=NOW, routes=routes)

    assert len(other) == 1


def test_it_speaks_again_once_the_window_has_passed():
    routes = _routes()
    notify.reset()

    notify.select([_event("robot.comm_lost")], now=NOW, routes=routes)

    # 새로 벌어진 두절이다. 판정 시각과 발생 시각이 같이 움직인다 —
    # 발생만 옛날이면 그건 지난 일이라 나이 규칙에 먼저 걸린다.
    at = NOW + timedelta(seconds=301)
    later = notify.select([_event("robot.comm_lost", at=at)],
                          now=at, routes=routes)

    assert len(later) == 1


def test_the_backlog_after_an_outage_is_not_announced_as_news():
    """게이트웨이는 두절 동안 쌓인 이벤트를 복구 시 몰아 보낸다(§3.2).
    그 배치에는 10분 전 사건이 들어 있고, 지금 알리면 현재로 읽힌다."""
    stale = _event("fire.detected", at=NOW - timedelta(minutes=10))

    assert _select([stale]) == []


def test_a_flood_is_cut_so_the_real_one_is_findable():
    routes = _routes(max_per_batch=2)
    notify.reset()
    batch = [
        _event("robot.comm_lost", robot_id=f"pinky-0{i}",
               at=NOW - timedelta(seconds=i))
        for i in range(1, 5)
    ]

    alerts = notify.select(batch, now=NOW, routes=routes)

    assert len(alerts) == 2
    # 잘릴 때 남는 것은 최신이 아니라 처음 벌어진 일이어야 원인 추적이 된다.
    assert [a.robot_id for a in alerts] == ["pinky-04", "pinky-03"]


# --- 채널 ---------------------------------------------------------------------

def test_slack_and_discord_carry_the_same_sentence_in_their_own_key():
    """§13 이 채널을 미결정으로 남겼다. 하나를 코드에 박지 않는다."""
    alert = _select([_event("fire.detected")])[0]

    assert notify.body(alert, "slack")["text"] == notify.message(alert)
    assert notify.body(alert, "discord")["content"] == notify.message(alert)


def test_unknown_receivers_get_facts_not_just_a_sentence():
    alert = _select([_event("manipulator.servo_fault", robot_id="omx-01",
                            payload={"joint": "gripper"})])[0]
    payload = notify.body(alert, "json")

    assert payload["event_code"] == "manipulator.servo_fault"
    assert payload["robot_id"] == "omx-01"
    assert payload["payload"] == {"joint": "gripper"}


def test_the_channel_is_detected_from_the_url(monkeypatch):
    monkeypatch.delenv("ALERT_WEBHOOK_KIND", raising=False)

    assert notify.webhook_kind("https://hooks.slack.com/services/T/B/x") == "slack"
    assert notify.webhook_kind("https://discord.com/api/webhooks/1/x") == "discord"
    # 모르는 호스트에 남의 포맷을 추측해 보내면 조용히 버려진다.
    assert notify.webhook_kind("https://ops.example.com/hook") == "json"


# --- 꺼진 상태 ----------------------------------------------------------------

def test_alerting_is_off_unless_a_url_is_configured(monkeypatch):
    """기본이 '안 보냄' 이어야 개발·CI 가 실수로 실제 채널에 쏘지 않는다."""
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)

    assert notify.enabled() is False


def test_notify_never_raises_into_the_ingest_path(monkeypatch):
    """여기서 예외가 새면 적재가 실패하고 게이트웨이가 같은 배치를 무한히
    재전송한다. 알림을 못 보낸 것과 기록을 잃는 것은 무게가 다르다."""
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.slack.com/services/x")
    monkeypatch.setattr(notify, "select",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    notify.notify([_event("fire.detected")])       # 예외가 나면 테스트 실패


def test_missing_route_file_sends_nothing(tmp_path):
    """기본 목록을 코드에 두면 설정 파일을 지운 사람이 조용히 다른 정책으로
    운영하게 된다."""
    routes = AlertRoutes.load(str(tmp_path / "없는파일.yaml"))

    assert routes.route_of("fire.detected") is None


def test_shipped_routes_keep_the_page_tier_short():
    """§8.4 — '즉시 사람 호출' 등급은 손에 꼽게 유지한다. 늘어나는 순간
    알림 전체가 무시된다."""
    routes = AlertRoutes.load()
    paged = [code for code in (
        "fire.detected", "robot.estop_engaged", "robot.comm_lost",
        "robot.battery_low", "manipulator.servo_fault",
        "manipulator.servo_overheat", "manipulator.cycle_aborted",
        "robot.topic_stale", "manipulator.pick_failed",
    ) if (routes.route_of(code) or {}).get("tier") == "page"]

    assert set(paged) == {
        "fire.detected", "robot.estop_engaged", "robot.comm_lost"}
    # 확률적 실패는 어느 등급에도 없어야 한다.
    assert routes.route_of("manipulator.pick_failed") is None
