import { EMPTY_FILTERS } from '../lib/eventFilters'
import type { EventFilterValues } from '../lib/eventFilters'
import { EVENT_CODE_PREFIXES } from '../types/events'
import type { EventLevel } from '../types/events'

/** 화면 라벨. 접두사 자체를 보여주면 무슨 이벤트인지 안 읽힌다. */
const PREFIX_LABELS: Record<string, string> = {
  'qr.': 'QR · 환자 인식',
  'patient.': '환자 추적',
  'nav.': '주행',
  'session.': '세션',
  'robot.': '로봇 상태',
  'system.': '시스템',
}

const LEVELS: EventLevel[] = ['info', 'warning', 'error']

interface Props {
  values: EventFilterValues
  onChange: (next: EventFilterValues) => void
  /** GET /robots 에서 받은 목록. 비어 있으면 로봇 셀렉트를 '전체'만 둔다. */
  robots?: { robot_id: string; display_name: string }[]
}

export function EventFilters({ values, onChange, robots = [] }: Props) {
  const set = (key: keyof EventFilterValues) => (value: string) =>
    onChange({ ...values, [key]: value })

  const dirty = Object.values(values).some((value) => value !== '')

  return (
    <div className="card">
      <div className="event-timeline-head">
        <div className="card-title">필터</div>
        {dirty && (
          <button type="button" className="btn" onClick={() => onChange(EMPTY_FILTERS)}>
            초기화
          </button>
        )}
      </div>

      <div className="filter-grid">
        <label className="filter-field">
          <span>로봇</span>
          <select value={values.robot_id} onChange={(e) => set('robot_id')(e.target.value)}>
            <option value="">전체</option>
            {robots.map((robot) => (
              <option key={robot.robot_id} value={robot.robot_id}>
                {robot.display_name} ({robot.robot_id})
              </option>
            ))}
          </select>
        </label>

        <label className="filter-field">
          <span>최소 레벨</span>
          <select value={values.min_level} onChange={(e) => set('min_level')(e.target.value)}>
            {/* 서버가 이 값 '이상'만 남긴다. warning 을 고르면 error 도 나온다. */}
            <option value="">전체</option>
            {LEVELS.map((level) => (
              <option key={level} value={level}>
                {level} 이상
              </option>
            ))}
          </select>
        </label>

        <label className="filter-field">
          <span>분류</span>
          <select value={values.code_prefix} onChange={(e) => set('code_prefix')(e.target.value)}>
            <option value="">전체</option>
            {/* config/event_codes.yaml 의 접두사. 자유 입력이면 오타가 곧 0 건이다. */}
            {EVENT_CODE_PREFIXES.map((prefix) => (
              <option key={prefix} value={prefix}>
                {PREFIX_LABELS[prefix] ?? prefix}
              </option>
            ))}
          </select>
        </label>

        <label className="filter-field">
          <span>노드</span>
          <input
            type="text"
            placeholder="guide_manager"
            value={values.source_node}
            onChange={(e) => set('source_node')(e.target.value)}
          />
        </label>

        <label className="filter-field">
          <span>세션 ID</span>
          <input
            type="number"
            min={1}
            placeholder="전체"
            value={values.session_id}
            onChange={(e) => set('session_id')(e.target.value)}
          />
        </label>

        <label className="filter-field">
          <span>시작 (이상)</span>
          {/* step=1 이어야 초 단위까지 입력된다. 기본값은 분 단위다. */}
          <input
            type="datetime-local"
            step={1}
            value={values.since}
            onChange={(e) => set('since')(e.target.value)}
          />
        </label>

        <label className="filter-field">
          <span>종료 (미만)</span>
          <input
            type="datetime-local"
            step={1}
            value={values.until}
            onChange={(e) => set('until')(e.target.value)}
          />
        </label>
      </div>
    </div>
  )
}
