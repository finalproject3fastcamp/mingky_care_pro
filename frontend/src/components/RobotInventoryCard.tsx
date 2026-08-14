/**
 * 로봇에서 실제로 돌고 있는 것 — 실행 코드 버전과 중복 노드.
 *
 * 장애 대응에서 가장 많은 시간을 잡아먹은 항목이다. 로봇마다 다른
 * 워크스페이스가 섞여 있었고, 같은 노드가 두 번 떠서 I2C 측정값을 조용히
 * 오염시키고 있었는데 화면에는 아무 흔적이 없었다.
 *
 * 심각도 판정은 서버가 한다(inventory_rules.py). 여기서 다시 판정하면 두
 * 곳이 어긋나고, 어긋난 순간 어느 쪽이 맞는지 아무도 모른다.
 */

import { Freshness } from './Freshness'
import { freshnessLevel, stalenessClass } from '../lib/freshness'
import type { RobotInventory } from '../types/monitoring'

// 인벤토리는 30초마다 확인하고 바뀔 때만 온다. 몇 분 지난 건 정상이다.
const INVENTORY_FRESHNESS = { warnSec: 600, staleSec: 1800 }

interface Props {
  inventory: RobotInventory | null
  loading: boolean
  error: unknown
}

/** 누적 CPU 초를 사람이 읽는 길이로. "11시간 40분". */
function formatCpuTime(seconds: number | null): string | null {
  if (seconds == null) return null
  if (seconds < 60) return `${Math.round(seconds)}초`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}분`
  return `${Math.floor(minutes / 60)}시간 ${minutes % 60}분`
}

export function RobotInventoryCard({ inventory, loading, error }: Props) {
  if (error) {
    return (
      <div className="card">
        <div className="card-title">실행 중인 코드</div>
        <p className="picker-error">인벤토리를 불러오지 못했습니다.</p>
      </div>
    )
  }

  if (loading && !inventory) {
    return (
      <div className="card">
        <div className="card-title">실행 중인 코드</div>
        <p className="empty">확인 중…</p>
      </div>
    )
  }

  if (!inventory) {
    // 404 다. 실패가 아니라 "아직 보고한 적 없음" 이고, 원인이 다르다.
    return (
      <div className="card">
        <div className="card-title">실행 중인 코드</div>
        <p className="empty">
          아직 보고된 적이 없습니다. 게이트웨이가 구버전이거나
          <code className="mono"> inventory_interval_sec </code>
          이 0 입니다.
        </p>
      </div>
    )
  }

  const level = freshnessLevel(inventory.reported_at, INVENTORY_FRESHNESS)
  const dim = stalenessClass(level)

  return (
    <div className="card">
      <div className="card-title">실행 중인 코드</div>
      <div className="inventory-head">
        <span className="mono">{inventory.inventory_hash}</span>
        <Freshness at={inventory.reported_at} {...INVENTORY_FRESHNESS} />
      </div>

      {inventory.mixed_workspaces && (
        <div className="inventory-alert error">
          워크스페이스가 둘 이상입니다. 서로 다른 코드가 함께 돌고 있어
          어디를 고쳐야 할지 알기 어렵습니다.
        </div>
      )}

      <ul className={`inventory-workspaces ${dim}`}>
        {inventory.workspaces.map((workspace) => (
          <li key={workspace.path}>
            <div className="inventory-workspace-head">
              <span className="mono">
                {workspace.branch ?? '(브랜치 불명)'} @ {workspace.commit ?? '(커밋 불명)'}
              </span>
              {workspace.dirty && (
                <span
                  className="inventory-dirty"
                  title="커밋 안 된 변경이 있어 이 커밋 해시만으로는 재현할 수 없습니다."
                >
                  dirty
                </span>
              )}
            </div>
            <div className="inventory-workspace-path mono">{workspace.path}</div>
            <div className="inventory-workspace-count">
              {workspace.process_count}개 프로세스
            </div>
          </li>
        ))}
      </ul>

      {inventory.duplicates.length > 0 && (
        <>
          <div className="card-subtitle">중복 노드</div>
          <ul className="inventory-duplicates">
            {inventory.duplicates.map((duplicate) => (
              <li
                key={`${duplicate.namespace}${duplicate.name}`}
                className={`inventory-duplicate ${duplicate.severity}`}
              >
                <span className="mono">
                  {duplicate.name} × {duplicate.count}
                </span>
                <span className="inventory-duplicate-reason">{duplicate.reason}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      <details className="inventory-processes">
        <summary>프로세스 {inventory.processes.length}개</summary>
        <ul>
          {inventory.processes.map((process) => {
            const cpuTime = formatCpuTime(process.cpu_seconds_total)
            return (
              <li key={process.pid}>
                <span className="mono">{process.pid}</span>
                <span className="inventory-process-name">
                  {/* 추정한 이름이다. 못 찾으면 실행 파일 이름이 온다. */}
                  {process.matched_node_names.join(', ') || '(이름 불명)'}
                </span>
                <span className="inventory-process-cpu mono">
                  {process.cpu_pct != null ? `${process.cpu_pct}%` : '—'}
                  {/* 순간 100% 는 정상일 수 있지만 11시간 누적은 아니다. */}
                  {cpuTime && <span className="inventory-cpu-time"> ({cpuTime})</span>}
                </span>
              </li>
            )
          })}
        </ul>
      </details>
    </div>
  )
}
