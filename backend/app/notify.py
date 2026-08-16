"""심각한 사건을 화면 밖으로 내보낸다 (§8.4 · 로드맵 12).

원칙 4 — 대시보드에만 있고 아무도 안 보는 지표는 없는 것과 같다. 야간에
`fire.detected` 가 찍혀도 그 화면을 보는 사람이 없으면 관측한 것이 아니다.

## 무엇을 보내는가

`config/alert_routes.yaml` 이 정본이다. 코드·등급·문구를 전부 거기서 읽는다 —
임계와 심각도를 서버 설정에 둔 것과 같은 이유이고(§7.2 의 다른 판정들),
문구를 코드에 박으면 고치는 데 배포가 필요해져 결국 아무도 안 고친다.

**확률적 실패는 넣지 않는다.** `manipulator.pick_failed` 는 모방학습에서 정상
동작 범위다(§4.4). 그걸 내보내면 하루에 수십 번 울리고, 그 순간부터 아무도
이 채널을 안 본다. 알림의 가치는 희소성에서 나온다.

## 세 가지를 막는다

  중복    같은 (로봇, 코드) 를 throttle_sec 안에 다시 보내지 않는다.
          두절이 흔들리는 로봇 하나가 채널을 도배한다
  지난 일  게이트웨이는 두절 동안 쌓인 이벤트를 복구 시 몰아 보낸다(§3.2).
          10분 전 사건을 지금 알리면 사람이 현재로 읽는다
  홍수    한 배치의 상한. 몰려 들어온 배치가 알림 수십 건이 되면 그 안에
          있는 진짜 한 건을 아무도 못 찾는다

## 전송은 적재를 막지 않는다

이벤트 적재가 웹훅 응답을 기다리면, 슬랙이 느린 날 로봇의 이벤트 큐가 밀린다.
그래서 트랜잭션 밖에서, 태스크로 던지고 결과를 안 본다. 실패는 로그만 남긴다 —
알림을 못 보낸 것과 기록을 잃는 것은 무게가 다르다.

## 왜 HTTP 클라이언트를 새로 안 넣는가

한 건짜리 JSON POST 다. 배포 이미지에 의존성을 하나 더 얹을 이유가 없어
stdlib 로 보내고, 블로킹이므로 스레드로 뺀다. `routers/maps.py` 가 PNG 하나
때문에 이미지 라이브러리를 안 넣은 것과 같은 판단이다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import yaml

from .schemas import EventIn

log = logging.getLogger("mingky")

_DEFAULT_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "alert_routes.yaml")

# 정본이 없을 때. 알림 기능만 꺼지고 나머지는 그대로 돈다.
_DEFAULT_THROTTLE_SEC = 300.0
_DEFAULT_MAX_AGE_SEC = 300.0
_DEFAULT_MAX_PER_BATCH = 5

# 웹훅 응답을 오래 기다리지 않는다. 알림은 늦게 도착하면 가치가 떨어지고,
# 여기서 기다려도 로봇 쪽에는 아무 이득이 없다.
_TIMEOUT_SEC = 5.0

_TIER_LABEL = {"page": "즉시", "notify": "확인"}


@dataclass(frozen=True)
class Alert:
    event_code: str
    robot_id: str
    tier: str
    text: str
    occurred_at: datetime
    payload: dict


class AlertRoutes:
    """어떤 코드를 어느 등급으로 내보낼지의 정본."""

    def __init__(self, codes: dict, throttle_sec: float, max_age_sec: float,
                 max_per_batch: int):
        self._codes = codes
        self.throttle_sec = throttle_sec
        self.max_age_sec = max_age_sec
        self.max_per_batch = max_per_batch

    @classmethod
    def load(cls, explicit: str = "") -> "AlertRoutes":
        path = Path(
            explicit or os.environ.get("ALERT_ROUTES_FILE") or _DEFAULT_PATH)
        if not path.is_file():
            # 파일이 없으면 아무것도 안 보낸다. 기본 목록을 코드에 두면 설정
            # 파일을 지운 사람이 조용히 다른 정책으로 운영하게 된다.
            return cls({}, _DEFAULT_THROTTLE_SEC, _DEFAULT_MAX_AGE_SEC,
                       _DEFAULT_MAX_PER_BATCH)

        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        return cls(
            {code: entry for code, entry in (raw.get("codes") or {}).items()
             if isinstance(entry, dict)},
            float(raw.get("throttle_sec") or _DEFAULT_THROTTLE_SEC),
            float(raw.get("max_age_sec") or _DEFAULT_MAX_AGE_SEC),
            int(raw.get("max_per_batch") or _DEFAULT_MAX_PER_BATCH),
        )

    def route_of(self, code: str) -> dict | None:
        return self._codes.get(code)


_routes: AlertRoutes | None = None

# (robot_id, event_code) → 마지막 발송 시각.
_last_sent: dict[tuple[str, str], datetime] = {}

# 진행 중인 전송 태스크. 참조를 안 들고 있으면 이벤트 루프가 태스크를
# 중간에 수거해 전송이 조용히 사라진다.
_inflight: set[asyncio.Task] = set()


def load() -> AlertRoutes:
    global _routes
    _routes = AlertRoutes.load()
    return _routes


def get_routes() -> AlertRoutes:
    global _routes
    if _routes is None:
        _routes = AlertRoutes.load()
    return _routes


def webhook_url() -> str:
    return os.environ.get("ALERT_WEBHOOK_URL", "").strip()


def enabled() -> bool:
    """URL 이 없으면 꺼진 것이다. 기본이 '안 보냄' 이어야 한다 — 테스트와
    CI 가 실수로 실제 채널에 쏘는 일을 설정 하나로 막는다."""
    return bool(webhook_url())


def webhook_kind(url: str = "") -> str:
    """URL 에서 채널을 알아낸다. Slack 과 Discord 는 본문 키가 다르다.

    §13 이 "알림 채널 (Slack vs Discord)" 을 미결정으로 남겨 뒀다. 둘 중
    하나를 코드에 박고 나중에 고치는 대신, 호스트를 보고 갈라 둘 다 받는다.
    결정이 나면 이 함수는 그대로 두고 URL 만 바꾸면 된다.
    """
    explicit = os.environ.get("ALERT_WEBHOOK_KIND", "").strip().lower()
    if explicit in ("slack", "discord", "json"):
        return explicit

    host = urlparse(url or webhook_url()).netloc.lower()
    if "slack.com" in host:
        return "slack"
    if "discord.com" in host or "discordapp.com" in host:
        return "discord"
    # 모르는 호스트에는 사실을 그대로 보낸다. 남의 포맷을 추측해서 보내면
    # 받는 쪽에서 조용히 버려진다.
    return "json"


def _age_sec(event: EventIn, now: datetime) -> float:
    return (now - event.occurred_at).total_seconds()


def select(events: list[EventIn], now: datetime | None = None,
           routes: AlertRoutes | None = None) -> list[Alert]:
    """내보낼 사건을 고른다. 중복·지난 일·홍수를 여기서 막는다.

    상태를 바꾼다(throttle 기록). 순수 함수로 두면 호출부가 두 번 부르는
    순간 같은 알림이 두 번 나간다.
    """
    routes = routes or get_routes()
    now = now or datetime.now(timezone.utc)
    selected: list[Alert] = []

    # 발생 순으로 본다. 상한에 걸려 잘릴 때 남는 것이 최신이 아니라 처음
    # 벌어진 일이어야 원인 추적이 된다.
    for event in sorted(events, key=lambda e: e.occurred_at):
        route = routes.route_of(event.event_code)
        if route is None:
            continue

        # 지난 일을 현재형으로 알리지 않는다. 두절 복구 배치가 여기 걸린다.
        if _age_sec(event, now) > routes.max_age_sec:
            continue

        key = (event.robot_id, event.event_code)
        last = _last_sent.get(key)
        if last is not None and (now - last).total_seconds() < routes.throttle_sec:
            continue

        _last_sent[key] = now
        selected.append(Alert(
            event_code=event.event_code,
            robot_id=event.robot_id,
            tier=str(route.get("tier") or "notify"),
            text=str(route.get("text") or event.event_code),
            occurred_at=event.occurred_at,
            payload=dict(event.payload),
        ))

        if len(selected) >= routes.max_per_batch:
            # 나머지는 버린다. 대시보드에는 다 있다 — 여기서 지키려는 것은
            # 기록이 아니라 사람의 주의력이다.
            log.warning("알림 상한 도달: %d건에서 자름 (배치 %d건)",
                        routes.max_per_batch, len(events))
            break

    return selected


def message(alert: Alert) -> str:
    """사람이 읽는 한 줄. 코드 이름만 보내면 받는 쪽이 저장소를 열어야 한다."""
    label = _TIER_LABEL.get(alert.tier, alert.tier)
    detail = " · ".join(f"{k}={v}" for k, v in sorted(alert.payload.items()))
    line = f"[{label}] {alert.robot_id} — {alert.text}"
    if detail:
        line += f"\n{alert.event_code} · {detail}"
    else:
        line += f"\n{alert.event_code}"
    return line


def body(alert: Alert, kind: str) -> dict:
    """채널별 본문. 키 이름만 다르고 내용은 같다."""
    if kind == "slack":
        return {"text": message(alert)}
    if kind == "discord":
        return {"content": message(alert)}
    # 커스텀 수신기. 문장이 아니라 사실을 준다 — 받는 쪽이 자기 형식으로
    # 다시 만들 수 있어야 한다.
    return {
        "text": message(alert),
        "event_code": alert.event_code,
        "robot_id": alert.robot_id,
        "tier": alert.tier,
        "occurred_at": alert.occurred_at.isoformat(),
        "payload": alert.payload,
    }


def _post(url: str, payload: dict) -> None:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SEC):
        pass


async def deliver(alerts: list[Alert]) -> None:
    """실제 전송. 실패해도 예외를 밖으로 내보내지 않는다."""
    url = webhook_url()
    if not url or not alerts:
        return
    kind = webhook_kind(url)

    for alert in alerts:
        try:
            # stdlib 는 블로킹이다. 이벤트 루프에서 직접 부르면 웹훅이 느린
            # 동안 heartbeat 응답까지 같이 늦어진다.
            await asyncio.to_thread(_post, url, body(alert, kind))
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            log.warning("알림 전송 실패 (%s %s): %s",
                        alert.robot_id, alert.event_code, exc)
        except Exception:
            log.exception("알림 전송 중 예외 (%s %s)",
                          alert.robot_id, alert.event_code)


def notify(events: list[EventIn]) -> None:
    """ingest 가 부르는 진입점. **적재 경로를 절대 막지 않는다.**

    여기서 예외가 새면 이벤트 적재가 실패하고 게이트웨이가 같은 배치를 무한히
    재전송한다. 알림을 못 보낸 것과 기록을 잃는 것은 무게가 다르다.
    """
    if not events or not enabled():
        return
    try:
        alerts = select(events)
        if not alerts:
            return
        task = asyncio.create_task(deliver(alerts))
        _inflight.add(task)
        task.add_done_callback(_inflight.discard)
    except RuntimeError:
        # 이벤트 루프 밖(동기 테스트 등)에서 불렸다. 알림만 건너뛴다.
        log.debug("이벤트 루프가 없어 알림을 건너뜁니다")
    except Exception:
        log.exception("알림 선별 실패. 적재는 계속한다.")


def reset() -> None:
    """테스트용. 중복 억제 상태만 비운다. 정본은 그대로 둔다."""
    _last_sent.clear()
