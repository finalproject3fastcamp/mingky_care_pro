/**
 * Fleet 탭 — SLO 현황이 맨 위에 온다 (§7.2).
 *
 * 이 화면의 유일한 주장은 "지금 목표를 지키고 있는가" 다. 나머지는 그 숫자가
 * 나빠졌을 때 원인을 찾는 도구이므로 아래에 둔다. 순서를 뒤집으면 계기판만
 * 화려하고 아무도 SLO 를 안 보는 상태가 된다.
 *
 * ## 판정을 여기서 다시 하지 않는다
 *
 * 완주율·예산·개입 여부는 전부 서버가 계산해 내려준 값을 그대로 그린다
 * (`app/slo.py`). 프론트에서 한 번 더 세면 화면과 API 가 다른 완주율을 말하는
 * 날이 오고, 그때 어느 쪽이 맞는지 아무도 답하지 못한다.
 *
 * ## 구분해서 그려야 하는 세 쌍
 *
 *   표본 없음 ≠ 완주율 0%     — 하나는 안내, 하나는 비상이다
 *   예산 잔량 0 ≠ 예산 소진   — 잔량 0 은 아직 목표 안이다 (§1.2)
 *   기록 안 함 ≠ 익명 기록    — 후자는 명령은 갔고 이름만 없다
 */

import { useMemo } from 'react'

import { getControlAudit, getRobots, getSloCompletion } from '../lib/api'
import { usePolling } from '../lib/usePolling'
import { isMobile, type Robot } from '../types/monitoring'
import type { ControlAuditEntry, SloFailure, SloWindow } from '../types/slo'

// 세션 종료는 분 단위 사건이다. 로봇 상태와 같은 3~5초로 물어볼 이유가 없다.
const SLO_POLL_MS = 30000
const ROBOT_POLL_MS = 5000
const AUDIT_LIMIT = 20

const FAILURE_LABELS: Record<SloFailure, string> = {
  abnormal_end: '비정상 종료',
  manual_stop: '수동 정지',
  operator_order: '관리자 개입',
}

function formatRate(rate: number | null): string {
  return rate === null ? '—' : `${(rate * 100).toFixed(1)}%`
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString('ko-KR', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

/** 예산 상태. 잔량 0 과 소진을 다른 등급으로 그리기 위한 구분이다. */
function budgetTone(slo: SloWindow): 'ok' | 'warning' | 'error' {
  if (slo.budget_exhausted) return 'error'
  if (slo.budget_remaining === 0) return 'warning'
  return 'ok'
}

function SloPanel({ slo }: { slo: SloWindow }) {
  const tone = budgetTone(slo)
  const met = slo.completion_rate !== null && slo.completion_rate >= slo.target

  return (
    <section className="card fleet-slo" aria-label="SLO 현황">
      <div className="card-title">세션 완주율 · 최근 {slo.window}세션</div>

      <div className="fleet-slo__headline">
        <strong
          className={`fleet-slo__rate fleet-slo__rate--${
            slo.completion_rate === null ? 'unknown' : met ? 'ok' : 'error'}`}
        >
          {formatRate(slo.completion_rate)}
        </strong>
        <div className="fleet-slo__meta">
          <span>목표 {formatRate(slo.target)}</span>
          <span>
            성공 {slo.success} · 실패 {slo.failure}
          </span>
          {/* 표본이 창을 못 채웠다는 사실을 완주율 옆에 붙인다. 12세션에
              1회 실패면 91.7% 지만 신뢰구간이 창만큼 넓다. */}
          {!slo.sample_complete && (
            <span className="fleet-slo__sample">
              표본 {slo.sessions_judged}/{slo.window} — 판정 근거 부족
            </span>
          )}
        </div>
      </div>

      <div className="fleet-budget">
        <div className="fleet-budget__head">
          <span>오차 예산</span>
          <strong data-tone={tone}>
            {slo.budget_remaining}/{slo.budget_total} 남음
          </strong>
        </div>
        <div className="fleet-budget__bar" role="img"
          aria-label={`오차 예산 ${slo.budget_total}건 중 ${slo.budget_used}건 사용`}>
          {Array.from({ length: slo.budget_total }, (_, index) => (
            <span key={index}
              className={`fleet-budget__slot${
                index < slo.budget_used ? ' fleet-budget__slot--used' : ''}`} />
          ))}
        </div>
        {slo.budget_exhausted && (
          <p className="fleet-budget__alert" role="alert">
            예산을 다 썼습니다. 새 기능 배포를 멈추고 원인부터 잡아야 합니다.
          </p>
        )}
        {!slo.budget_exhausted && slo.budget_remaining === 0 && (
          <p className="fleet-budget__note">
            잔량이 0 이지만 아직 목표 안입니다. 다음 한 건이 위반입니다.
          </p>
        )}
      </div>
    </section>
  )
}

function FailedSessions({ slo }: { slo: SloWindow }) {
  if (slo.failed_sessions.length === 0) {
    return (
      <section className="card" aria-label="실패한 세션">
        <div className="card-title">실패한 세션</div>
        <p className="fleet-empty">이 창에서 실패한 세션이 없습니다.</p>
      </section>
    )
  }

  return (
    <section className="card" aria-label="실패한 세션">
      <div className="card-title">실패한 세션 {slo.failed_sessions.length}건</div>
      <ul className="fleet-list">
        {slo.failed_sessions.map((session) => (
          <li key={session.session_id} className="fleet-list__row">
            <span className="fleet-list__when">{formatTime(session.ended_at)}</span>
            <code>#{session.session_id}</code>
            <span>{session.robot_id}</span>
            <span className="fleet-list__reasons">
              {session.failures.map((failure) => (
                <span key={failure} className="fleet-tag fleet-tag--fail">
                  {FAILURE_LABELS[failure]}
                </span>
              ))}
            </span>
            {/* 완주했는데 실패로 잡힌 세션. 이 화면에서 가장 설명이 필요한
                줄이라 종료 사유를 같이 보여준다. */}
            <span className="fleet-list__end">{session.end_reason}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

function RobotSummary({ robots }: { robots: Robot[] }) {
  return (
    <section className="card" aria-label="로봇 요약">
      <div className="card-title">로봇 {robots.length}대</div>
      <div className="fleet-robots">
        {robots.map((robot) => (
          <div key={robot.robot_id} className="fleet-robot">
            <div className="fleet-robot__top">
              <strong>{robot.display_name}</strong>
              <span className={`fleet-robot__link fleet-robot__link--${robot.link_state}`}>
                {robot.link_state === 'online' ? '온라인'
                  : robot.link_state === 'offline' ? '오프라인' : '이력 없음'}
              </span>
            </div>
            <div className="fleet-robot__meta">
              <code>{robot.robot_id}</code>
              <span>{robot.robot_type}</span>
            </div>
            {/* 두 줄의 뜻이 종류마다 다르다. 팔에 '배터리 —' 를 그리면
                "보고가 없다" 로 읽히지만 사실은 배터리가 없는 로봇이다. */}
            {isMobile(robot) ? (
              <div className="fleet-robot__meta">
                <span>
                  배터리 {robot.battery_percent === null ? '—' : `${robot.battery_percent}%`}
                </span>
                <span>
                  {robot.active_session_id === null
                    ? '유휴'
                    : `세션 #${robot.active_session_id}`}
                </span>
              </div>
            ) : (
              <div className="fleet-robot__meta">
                <span>유선 급전</span>
                <span>
                  {robot.detail.active_dispense_id === null
                    ? '조제 대기'
                    : `조제 중 ${robot.detail.active_dispense_id}`}
                </span>
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}

function AuditPanel({
  entries, total, anonymous,
}: { entries: ControlAuditEntry[]; total: number; anonymous: number }) {
  return (
    <section className="card" aria-label="최근 제어 개입">
      <div className="card-title">최근 제어 개입</div>

      {/* 익명 비율은 누적이다. 이 숫자가 오르면 클라이언트가 X-Actor 를
          빠뜨리고 있다는 뜻이고, 그러면 감사 로그가 서서히 죽는다. */}
      <div className={`fleet-anon${anonymous > 0 ? ' fleet-anon--warn' : ''}`}>
        누적 {total}건 중 익명 {anonymous}건
        {anonymous > 0 && <span> — 조작자 이름이 비어 있는 명령이 있습니다.</span>}
      </div>

      {entries.length === 0 ? (
        <p className="fleet-empty">기록된 제어 명령이 없습니다.</p>
      ) : (
        <ul className="fleet-list">
          {entries.map((entry) => (
            <li key={entry.audit_id} className="fleet-list__row">
              <span className="fleet-list__when">{formatTime(entry.occurred_at)}</span>
              <span className="fleet-list__actor">
                {entry.actor ?? <em>익명</em>}
              </span>
              <span>{entry.robot_id}</span>
              <code>
                {entry.action}
                {entry.argument ? `(${entry.argument})` : ''}
              </code>
              {/* 판정 대상 여부는 백엔드가 붙여 보낸 값이다. */}
              {entry.intervention && (
                <span className="fleet-tag fleet-tag--intervention">SLO 개입</span>
              )}
              {entry.session_id !== null && (
                <span className="fleet-list__end">세션 #{entry.session_id}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export function FleetDashboard() {
  const slo = usePolling((signal) => getSloCompletion({ signal }), SLO_POLL_MS)
  const robots = usePolling((signal) => getRobots({ signal }), ROBOT_POLL_MS)
  const audit = usePolling(
    (signal) => getControlAudit(AUDIT_LIMIT, { signal }), SLO_POLL_MS)

  // §7.3 — 선택기는 4대를 모두 보여준다. mobile 만 거르면 팔이 관제 대상이
  // 아니라는 뜻이 된다.
  const robotList = useMemo(() => robots.data ?? [], [robots.data])

  return (
    <div className="dashboard">
      <header className="waypoint-page-header">
        <div>
          <span className="waypoint-page-header__eyebrow">FLEET INSIGHT</span>
          <h1>Fleet</h1>
          <p>세션 완주율과 오차 예산, 그리고 그 숫자를 움직인 개입.</p>
        </div>
      </header>

      {slo.error != null && (
        <div className="waypoint-notice" role="alert">
          SLO 현황을 불러오지 못했습니다. 값이 없는 것과 목표 미달은 다릅니다 —
          아래 숫자를 판단 근거로 쓰지 마세요.
        </div>
      )}

      {slo.data && <SloPanel slo={slo.data} />}
      {slo.data && <FailedSessions slo={slo.data} />}

      <RobotSummary robots={robotList} />

      {audit.data && (
        <AuditPanel
          entries={audit.data.items}
          total={audit.data.total}
          anonymous={audit.data.anonymous}
        />
      )}
    </div>
  )
}
