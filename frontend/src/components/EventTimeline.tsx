import type { EventOut, EventPage } from '../types/events'

interface Props {
  page: EventPage | null
  loading: boolean
  error: unknown
}

/**
 * 시각 표기를 Asia/Seoul 로 고정한다.
 *
 * 브라우저 로컬 타임존을 따르지 않는 이유: 엔지니어 노트북이 UTC 로 맞춰져
 * 있는 경우가 흔한데, 그러면 같은 이벤트를 보고 두 사람이 다른 시각을 말하게
 * 된다. 로봇도 병원도 KST 에 있으므로 화면은 KST 로 고정한다.
 *
 * 저장은 UTC(TIMESTAMPTZ)다. 여기는 표시만 바꾼다.
 */
const TIME_FORMAT = new Intl.DateTimeFormat('ko-KR', {
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  fractionalSecondDigits: 3,
  hour12: false,
  timeZone: 'Asia/Seoul',
})

/**
 * occurred_at 과 received_at 이 이 이상 벌어지면 배지를 단다.
 *
 * 003_sessions_and_events.sql 주석 기준: 정상이면 수백 ms 다.
 * 초 단위로 벌어졌다면 게이트웨이가 큐잉했거나 로봇 시계가 어긋난 것이고,
 * 둘 다 화면에 드러나야 한다.
 */
const LAG_THRESHOLD_MS = 1000

function formatLag(ms: number): string {
  const abs = Math.abs(ms)
  if (abs < 60_000) return `${(abs / 1000).toFixed(1)}초`
  if (abs < 3_600_000) return `${Math.floor(abs / 60_000)}분 ${Math.round((abs % 60_000) / 1000)}초`
  return `${(abs / 3_600_000).toFixed(1)}시간`
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message
  return String(error)
}

/**
 * 수집 지연 배지.
 *
 * 양수 = 서버가 늦게 받았다. 통신 두절 중 게이트웨이 큐에 쌓였다가 몰려온 것.
 * 음수 = 로봇 시계가 서버보다 앞선다. chrony 가 안 돌고 있다는 뜻이므로
 *        큐잉보다 심각하다. 색을 다르게 준다.
 */
function LagBadge({ event }: { event: EventOut }) {
  const lag = new Date(event.received_at).getTime() - new Date(event.occurred_at).getTime()
  if (Math.abs(lag) < LAG_THRESHOLD_MS) return null

  const ahead = lag < 0
  return (
    <span
      className={`event-lag ${ahead ? 'ahead' : ''}`}
      title={
        ahead
          ? '로봇 시계가 서버보다 앞섭니다. chrony 동기화를 확인하세요.'
          : `수집 지연 ${formatLag(lag)} — 통신 두절 중 큐에 쌓였을 수 있습니다.`
      }
    >
      {ahead ? '시계 역전' : `+${formatLag(lag)}`}
    </span>
  )
}

function EventRow({ event }: { event: EventOut }) {
  // JSONB 라 코드마다 키가 다르다. 빈 객체면 접는 UI 자체를 만들지 않는다.
  const hasPayload = Object.keys(event.payload).length > 0

  return (
    <li className={`event-row ${event.level}`}>
      <div className="event-head">
        <time className="event-time" dateTime={event.occurred_at} title={event.occurred_at}>
          {TIME_FORMAT.format(new Date(event.occurred_at))}
        </time>
        <span className={`event-level ${event.level}`}>{event.level}</span>
        <span className="event-code">{event.event_code}</span>
        <LagBadge event={event} />
      </div>
      <div className="event-meta">
        {/* nullable 컬럼이다. 빈 값을 공백으로 두면 열이 어긋나 보인다. */}
        <span>{event.robot_id ?? '—'}</span>
        <span>{event.source_node ?? '—'}</span>
        {/* 세션과 무관한 이벤트는 null 로 온다. 0 이 아니다. */}
        <span>{event.session_id === null ? '세션 없음' : `#${event.session_id}`}</span>
      </div>
      {hasPayload && (
        <details className="event-payload">
          <summary>payload</summary>
          <pre>{JSON.stringify(event.payload, null, 2)}</pre>
        </details>
      )}
    </li>
  )
}

export function EventTimeline({ page, loading, error }: Props) {
  return (
    <div className="card">
      <div className="event-timeline-head">
        <div className="card-title">이벤트 타임라인</div>
        {page && (
          <span className="event-count">
            {/* total 은 필터에 걸린 전체 건수다. 화면에 실린 수와 다르다. */}
            {page.total.toLocaleString('ko-KR')}건 중 {page.items.length}건
          </span>
        )}
      </div>

      {error != null && <p className="event-error">조회 실패: {errorMessage(error)}</p>}

      {/* 필터를 바꾸는 동안에는 이전 목록을 유지한다. 비우면 화면이 튄다. */}
      {loading && page === null && <p className="empty">불러오는 중…</p>}

      {page && page.items.length === 0 && !loading && (
        <p className="empty">조건에 맞는 이벤트가 없습니다.</p>
      )}

      {page && page.items.length > 0 && (
        <ul className="event-list">
          {page.items.map((event) => (
            // 로봇이 만든 UUID 라 재전송돼도 값이 같다.
            <EventRow key={event.event_id} event={event} />
          ))}
        </ul>
      )}
    </div>
  )
}
