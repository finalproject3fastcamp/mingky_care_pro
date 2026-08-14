/**
 * 로봇 자원과 전송 큐. heartbeat 로 5초마다 갱신된다.
 *
 * 한 노드가 11시간 40분 동안 코어 하나를 붙들고 있어도 화면에는 아무것도
 * 없었다. 큐가 밀려 데이터가 버려지는 중이어도 마찬가지였다.
 *
 * null 과 0 을 구분해 그린다. "보고 안 함" 과 "0건" 은 다른 사실이고,
 * 구버전 게이트웨이는 이 필드를 아예 안 보낸다.
 */

import { Freshness } from './Freshness'
import { HEARTBEAT_FRESHNESS } from '../lib/freshness'
import type { Robot } from '../types/monitoring'

/** 게이트웨이 max_queue_rows 기본값. 이 근처면 이미 버려지는 중이다. */
const QUEUE_LIMIT = 50_000
const QUEUE_WARN = QUEUE_LIMIT * 0.1

/** 단일 노드가 이 이상이면 경고. 서버 판정이 붙기 전 화면 임계값이다. */
const NODE_CPU_WARN = 80

interface Props {
  robot: Robot
}

function queueClass(pending: number | null): string {
  if (pending == null) return ''
  if (pending >= QUEUE_WARN) return 'metric-error'
  if (pending > 0) return 'metric-warn'
  return ''
}

export function RobotResourceCard({ robot }: Props) {
  const { cpu_total_pct, queue_pending, max_node_cpu_pct, max_node_cpu_name } = robot

  // 구버전 게이트웨이는 이 필드를 통째로 안 보낸다. 0 으로 그리면
  // "정상" 으로 보여 오히려 잘못된 안심을 준다.
  const reported = cpu_total_pct != null || queue_pending != null

  return (
    <div className="card">
      <div className="card-title">
        자원 · 전송 큐
        <Freshness at={robot.runtime_reported_at} {...HEARTBEAT_FRESHNESS} />
      </div>

      {!reported ? (
        <p className="empty">이 게이트웨이는 자원 정보를 보고하지 않습니다.</p>
      ) : (
        <dl className="status-grid">
          <dt>전체 CPU</dt>
          <dd className="mono">
            {cpu_total_pct != null ? `${cpu_total_pct}%` : '—'}
          </dd>

          <dt>최다 사용 노드</dt>
          <dd
            className={`mono ${
              max_node_cpu_pct != null && max_node_cpu_pct >= NODE_CPU_WARN
                ? 'metric-error'
                : ''
            }`}
          >
            {max_node_cpu_pct != null
              ? `${max_node_cpu_name ?? '(이름 불명)'} ${max_node_cpu_pct}%`
              : '—'}
          </dd>

          <dt>전송 대기</dt>
          <dd className={`mono ${queueClass(queue_pending)}`}>
            {queue_pending != null
              ? `${queue_pending.toLocaleString('ko-KR')}건`
              : '—'}
            {queue_pending != null && queue_pending >= QUEUE_WARN && (
              <span className="metric-note">
                {' '}
                상한({QUEUE_LIMIT.toLocaleString('ko-KR')}) 에 가까워 오래된
                것부터 버려지고 있습니다
              </span>
            )}
          </dd>
        </dl>
      )}
    </div>
  )
}
