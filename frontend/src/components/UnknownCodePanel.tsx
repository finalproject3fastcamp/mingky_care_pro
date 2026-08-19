/**
 * 서버가 해석하지 못한 event_code 목록.
 *
 * 비상정지 이력이 통째로 화면에서 빠져 있어도 지금까지는 아무 신호가
 * 없었다. 로봇 로그에는 남지만 관제는 조용했다. 이 패널이 그 신호다.
 *
 * "데이터가 사라졌다" 가 아니라 "해석되지 않았다" 로 쓴다. ingest 규칙 4 가
 * 모르는 코드도 원본 그대로 적재하므로 events 에는 남아 있다. 다만 등록된
 * 코드만 상태 갱신을 타므로(_apply_state) 대시보드 판정에서 빠질 뿐이다.
 * 문구를 틀리게 쓰면 엔지니어가 없는 데이터를 복구하러 간다.
 */

import { Freshness } from './Freshness'
import type { UnknownCode } from '../types/events'

interface Props {
  codes: UnknownCode[]
  loading: boolean
  error: unknown
}

// 미등록 코드는 드물어야 정상이다. 자주 보이면 배포가 어긋난 것이다.
const CODE_FRESHNESS = { warnSec: 3600, staleSec: 86400 }

export function UnknownCodePanel({ codes, loading, error }: Props) {
  if (error) {
    return (
      <div className="card">
        <div className="card-title">미등록 event_code</div>
        <p className="picker-error">목록을 불러오지 못했습니다.</p>
      </div>
    )
  }

  if (loading && codes.length === 0) {
    return (
      <div className="card">
        <div className="card-title">미등록 event_code</div>
        <p className="empty">확인 중…</p>
      </div>
    )
  }

  if (codes.length === 0) {
    return (
      <div className="card">
        <div className="card-title">미등록 event_code</div>
        <div className="state-badge ok">전부 등록됨</div>
      </div>
    )
  }

  const total = codes.reduce((sum, code) => sum + code.count, 0)

  return (
    <div className="card">
      <div className="card-title">미등록 event_code</div>
      <div className="state-badge warning">
        {codes.length}종 · {total.toLocaleString('ko-KR')}건
      </div>
      <p className="picker-hint">
        적재는 되지만 상태 갱신을 타지 않아 대시보드 판정에서 빠집니다.
        <code className="mono"> config/event_codes.yaml </code>
        갱신이 필요합니다.
      </p>
      <ul className="unknown-code-list">
        {codes.map((code) => (
          <li key={`${code.event_code}:${code.robot_id ?? '-'}`}>
            <span className="unknown-code-name mono">{code.event_code}</span>
            <span className="unknown-code-robot mono">{code.robot_id ?? '—'}</span>
            <span className="unknown-code-count">
              {code.count.toLocaleString('ko-KR')}건
            </span>
            <Freshness at={code.last_seen} {...CODE_FRESHNESS} separator="" />
          </li>
        ))}
      </ul>
    </div>
  )
}
