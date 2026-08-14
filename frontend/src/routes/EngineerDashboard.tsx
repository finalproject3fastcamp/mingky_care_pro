import { useEffect, useState } from 'react'

import { EventFilters } from '../components/EventFilters'
import { EventTimeline } from '../components/EventTimeline'
import { BatteryForecastLabel } from '../components/BatteryForecastLabel'
import { BatteryReading } from '../components/BatteryReading'
import { RobotInventoryCard } from '../components/RobotInventoryCard'
import { RobotResourceCard } from '../components/RobotResourceCard'
import { SessionEndingSummary } from '../components/SessionEndingSummary'
import { UnknownCodePanel } from '../components/UnknownCodePanel'
import {
  api,
  getBatteryForecast,
  getRobotInventory,
  getRobots,
  getSessionEndingContext,
} from '../lib/api'
import { EMPTY_FILTERS, toEventQuery } from '../lib/eventFilters'
import type { EventFilterValues } from '../lib/eventFilters'
import { listUnknownCodes } from '../lib/eventsApi'
import { useEventFeed } from '../lib/useEventFeed'
import { usePolling } from '../lib/usePolling'

/** GET /robots 응답 중 필터가 쓰는 부분만. 전체 스키마는 schemas.py 의 RobotOut. */
interface RobotSummary {
  robot_id: string
  display_name: string
}

const PAGE_SIZE = 50
const POLL_MS = 3000
const UNKNOWN_CODE_POLL_MS = 60000
// 자원은 heartbeat(5초)로 갱신되므로 그보다 자주 물어볼 이유가 없다.
const ROBOT_POLL_MS = 5000
// 인벤토리는 내용이 바뀔 때만 갱신된다. 몇 시간에 한 번이다.
const INVENTORY_POLL_MS = 30000
// 배터리 로그가 2분 주기라 그보다 자주 추정해도 같은 답이 나온다.
const FORECAST_POLL_MS = 120000
// 끝난 세션의 종료 창은 더 이상 안 바뀐다. 확인만 이따금 한다.
const ENDING_POLL_MS = 30000

const UPDATED_FORMAT = new Intl.DateTimeFormat('ko-KR', {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
  timeZone: 'Asia/Seoul',
})

/**
 * 엔지니어 대시보드.
 *
 * 이 화면이 query 와 모드를 소유한다. EventTimeline 은 표시만 한다.
 * "페이지를 넘기면 실시간이 꺼진다" 같은 규칙을 아는 곳이 부모여야 하기 때문이다.
 *
 * 모드를 둘로 나눈 이유
 *   서버는 occurred_at DESC 로 정렬한다(도착순이 아니다). 폴링과 offset
 *   페이지네이션을 동시에 켜면, 새 이벤트가 앞에 끼어들 때마다 offset 이 밀려
 *   2 페이지가 1 페이지 내용을 다시 보여준다. 두절 후 몰아 들어온 과거
 *   이벤트는 목록 중간에 삽입돼 스크롤이 튄다.
 *
 *   live   폴링 켬, offset 0 고정 — "지금 흐르는 것"
 *   paused 폴링 끔, 페이지 이동   — "과거 조회"
 */
export function EngineerDashboard() {
  const [live, setLive] = useState(true)
  const [offset, setOffset] = useState(0)
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  const [filters, setFilters] = useState<EventFilterValues>(EMPTY_FILTERS)
  const [robots, setRobots] = useState<RobotSummary[]>([])
  // 인벤토리·자원은 로봇 한 대씩 본다. 여러 대를 한 화면에 늘어놓으면
  // 어느 로봇 이야기인지 헷갈리고, 그게 원인 추적을 늦춘다.
  const [focused, setFocused] = useState<string | null>(null)

  // 로봇 목록은 마스터 테이블이라 세션 중에 바뀌지 않는다. 한 번만 읽는다.
  // 실패해도 화면을 막지 않는다 — 셀렉트가 '전체'만 남을 뿐이다.
  useEffect(() => {
    const controller = new AbortController()
    api
      .get<RobotSummary[]>('/robots', { signal: controller.signal })
      .then((response) => setRobots(response.data))
      .catch(() => undefined)
    return () => controller.abort()
  }, [])

  // query 객체를 useMemo 로 감싸지 않아도 된다.
  // useEventFeed 가 내용을 정렬·직렬화한 key 로 비교하므로 참조가 새로 만들어져도
  // 재조회가 일어나지 않는다.
  const feed = useEventFeed(
    { ...toEventQuery(filters), limit: PAGE_SIZE, offset },
    live ? POLL_MS : null,
  )

  // 미등록 코드는 몇 시간에 한 번 바뀌는 값이라 이벤트 피드와 같은 주기로
  // 물어볼 이유가 없다. 집계 쿼리라 비싸기도 하다.
  const unknownCodes = usePolling(
    (signal) => listUnknownCodes({ signal }),
    UNKNOWN_CODE_POLL_MS,
  )

  // 자원은 GET /robots 에 실려 온다. 별도 엔드포인트를 두지 않은 이유는
  // 이 값들이 전부 heartbeat 가 남긴 인메모리 상태라서다.
  const robotState = usePolling((signal) => getRobots({ signal }), ROBOT_POLL_MS)

  const target = focused ?? robots[0]?.robot_id ?? null
  // key 가 없으면 로봇을 바꿔도 최대 30초간 이전 로봇의 인벤토리가 남는다.
  // 그 사이에 "이 로봇은 중복 노드 없음" 으로 읽히면 이 화면의 존재 이유가
  // 사라진다.
  const inventory = usePolling(
    (signal) => (target ? getRobotInventory(target, { signal }) : Promise.resolve(null)),
    INVENTORY_POLL_MS,
    target,
  )
  const forecast = usePolling(
    (signal) => (target ? getBatteryForecast(target, { signal }) : Promise.resolve(null)),
    FORECAST_POLL_MS,
    target,
  )
  const focusedRobot = robotState.data?.find((r) => r.robot_id === target) ?? null

  // 세션으로 필터링하면 "왜 그렇게 끝났는지" 를 함께 보여준다. 이벤트
  // 목록만으로는 종료 직전 창을 눈으로 골라내야 한다.
  const sessionId = toEventQuery(filters).session_id ?? null
  const ending = usePolling(
    (signal) => (sessionId
      ? getSessionEndingContext(sessionId, { signal })
      : Promise.resolve(null)),
    ENDING_POLL_MS,
    sessionId,
  )

  useEffect(() => {
    if (feed.page) setUpdatedAt(new Date())
  }, [feed.page])

  /**
   * 필터가 바뀌면 첫 페이지로 되돌린다.
   *
   * 3 페이지를 보던 중에 조건을 좁히면 결과가 1 페이지밖에 안 남을 수 있고,
   * 그러면 offset 이 범위를 벗어나 빈 목록이 뜬다. 원인이 화면에 안 드러난다.
   *
   * live 는 건드리지 않는다. 페이지 이동과 달리 필터링은 폴링과 충돌하지 않는다
   * — 조건을 걸어둔 채 실시간으로 보는 쪽이 오히려 쓸모 있다.
   */
  const handleFilterChange = (next: EventFilterValues) => {
    setFilters(next)
    setOffset(0)
  }

  const total = feed.page?.total ?? 0
  const shown = feed.page?.items.length ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1

  /** 페이지를 넘기면 실시간을 끈다. 안 끄면 다음 tick 에 목록이 밀린다. */
  const goToOffset = (next: number) => {
    setLive(false)
    setOffset(next)
  }

  const resumeLive = () => {
    setOffset(0)
    setLive(true)
  }

  return (
    <div className="dashboard">
      <div className="card">
        <div className="card-title">수집 상태</div>
        <div className={`state-badge ok${live ? ' pulsing' : ''}`}>
          {live ? '실시간 수신 중' : '과거 조회'}
        </div>
        <dl className="status-grid">
          <dt>전체 이벤트</dt>
          <dd>{total.toLocaleString('ko-KR')}건</dd>
          <dt>폴링 주기</dt>
          <dd>{live ? `${POLL_MS / 1000}초` : '정지'}</dd>
          <dt>마지막 갱신</dt>
          <dd className="mono">{updatedAt ? UPDATED_FORMAT.format(updatedAt) : '—'}</dd>
        </dl>
        <div className="toolbar">
          {live ? (
            <button type="button" className="btn" onClick={() => setLive(false)}>
              일시정지
            </button>
          ) : (
            <button type="button" className="btn primary" onClick={resumeLive}>
              실시간으로 돌아가기
            </button>
          )}
          <button type="button" className="btn" onClick={feed.refresh}>
            새로고침
          </button>
        </div>
      </div>

      {robots.length > 0 && (
        <div className="card">
          <div className="card-title">로봇 선택</div>
          <div className="toolbar">
            {robots.map((robot) => (
              <button
                key={robot.robot_id}
                type="button"
                className={`btn${robot.robot_id === target ? ' primary' : ''}`}
                onClick={() => setFocused(robot.robot_id)}
              >
                {robot.display_name}
              </button>
            ))}
          </div>
        </div>
      )}

      {focusedRobot && (
        <div className="card">
          <div className="card-title">배터리</div>
          <BatteryReading
            voltage={focusedRobot.battery_voltage}
            percent={focusedRobot.battery_percent}
            recordedAt={focusedRobot.battery_recorded_at}
            audience="engineer"
            charging={forecast.data?.direction === 'charging'}
          />
          <BatteryForecastLabel forecast={forecast.data ?? null} audience="engineer" />
        </div>
      )}

      {focusedRobot && <RobotResourceCard robot={focusedRobot} />}

      <RobotInventoryCard
        inventory={inventory.data ?? null}
        loading={inventory.loading}
        error={inventory.error}
      />

      <UnknownCodePanel
        codes={unknownCodes.data ?? []}
        loading={unknownCodes.loading}
        error={unknownCodes.error}
      />

      <EventFilters values={filters} onChange={handleFilterChange} robots={robots} />

      {ending.data && (
        <SessionEndingSummary context={ending.data} audience="engineer" />
      )}

      <EventTimeline page={feed.page} loading={feed.loading} error={feed.error} />

      <div className="pager">
        <button
          type="button"
          className="btn"
          disabled={offset === 0}
          onClick={() => goToOffset(Math.max(0, offset - PAGE_SIZE))}
        >
          이전
        </button>
        <span className="pager-label">
          {currentPage} / {totalPages}
        </span>
        <button
          type="button"
          className="btn"
          // total 이 아니라 실제로 받은 건수로 판단한다. 폴링 중에 total 만
          // 늘어난 상태에서 넘기면 빈 페이지가 나온다.
          disabled={offset + shown >= total}
          onClick={() => goToOffset(offset + PAGE_SIZE)}
        >
          다음
        </button>
      </div>
    </div>
  )
}
