/**
 * 형상 패널 — 4대가 지금 무엇으로 돌고 있는가 (§7.2 · 로드맵 10).
 *
 * "데모가 어제는 됐는데 오늘 안 된다" 의 원인 대부분이 형상 불일치다. 그런데
 * 지금까지 셋을 한 화면에서 볼 곳이 없었다 — 커밋은 인벤토리 카드 안쪽에,
 * 맵은 아무 데도, 정책 체크포인트는 조제 패널에 흩어져 있었다.
 *
 * ## 판정을 여기서 다시 하지 않는다
 *
 * 무엇이 갈렸는지는 서버가 정한다(`app/fleet_config.py`). 화면이 값을 비교하면
 * "팔의 SHA 와 핑키의 SHA 를 나란히 놓는" 같은 실수가 조용히 들어온다.
 *
 * ## '다르다' 와 '모른다' 를 구분해서 그린다
 *
 * OMX 는 게이트웨이가 아직 없어(로드맵 6) 코드 형상을 정상적으로 보고하지
 * 않는다. 그 칸을 빨갛게 그리면 패널이 영구히 경고 상태가 되고, 진짜 불일치가
 * 그 속에 묻힌다. 보고 없음은 회색 '—' 다.
 */

import type { ConfigMismatch, FleetConfig, RobotConfig } from '../types/monitoring'

const AXIS_LABEL: Record<ConfigMismatch['axis'], string> = {
  commit: '코드 커밋',
  map: '맵',
  policy: '정책 체크포인트',
  dataset: '학습 데이터셋',
}

/** 값이 없는 것과 값이 다른 것은 다르게 그려야 한다. */
function Value({ children }: { children: string | null }) {
  return children === null || children === ''
    ? <span className="fleet-config__unknown">—</span>
    : <code>{children}</code>
}

function RobotRow({ robot }: { robot: RobotConfig }) {
  const isArm = robot.robot_type === 'manipulator'

  return (
    <tr>
      <th scope="row">
        {robot.display_name}
        <span className="fleet-config__id">{robot.robot_id}</span>
      </th>
      <td>
        {/* 팔의 버전은 코드 SHA 가 아니다 (§4.4). 빈칸이 아니라 그 사실을 쓴다. */}
        {isArm ? (
          <span className="fleet-config__na">해당 없음 — 정책이 버전이다</span>
        ) : (
          <>
            <Value>{robot.commit}</Value>
            {robot.branch && <span className="fleet-config__branch">{robot.branch}</span>}
            {/* 커밋 해시만으로는 재현이 불가능한 상태다. */}
            {robot.dirty && (
              <span className="fleet-config__dirty">커밋 안 된 변경</span>
            )}
          </>
        )}
      </td>
      <td>
        {isArm ? (
          <span className="fleet-config__na">해당 없음</span>
        ) : (
          <>
            <Value>{robot.map_hash}</Value>
            {robot.map_name && (
              <span className="fleet-config__branch">{robot.map_name}</span>
            )}
          </>
        )}
      </td>
      <td>
        {isArm ? (
          <>
            <Value>{robot.policy_checkpoint_id}</Value>
            {robot.policy_dataset_revision && (
              <span className="fleet-config__branch">
                데이터셋 {robot.policy_dataset_revision}
              </span>
            )}
          </>
        ) : (
          <span className="fleet-config__na">해당 없음</span>
        )}
      </td>
    </tr>
  )
}

function MismatchRow({ mismatch }: { mismatch: ConfigMismatch }) {
  return (
    <li className="fleet-config__mismatch">
      <strong>{AXIS_LABEL[mismatch.axis]}이(가) 갈렸습니다</strong>
      <ul>
        {Object.entries(mismatch.values).map(([value, robots]) => (
          <li key={value}>
            <code>{value}</code> — {robots.join(', ')}
          </li>
        ))}
      </ul>
      {/* 2대만 비교한 "갈렸다" 를 4대의 판정으로 읽으면 안 된다. */}
      {mismatch.unreported.length > 0 && (
        <small>
          비교에서 빠짐(보고 없음): {mismatch.unreported.join(', ')}
        </small>
      )}
    </li>
  )
}

interface Props {
  config: FleetConfig | null
  loading: boolean
  error: unknown
}

export function FleetConfigCard({ config, loading, error }: Props) {
  return (
    <section className="card fleet-config" aria-label="형상">
      <div className="card-title">형상 — 무엇이 돌고 있나</div>

      {error ? (
        <p className="empty">형상 정보를 불러오지 못했습니다.</p>
      ) : !config ? (
        <p className="empty">{loading ? '불러오는 중…' : '형상 정보가 없습니다.'}</p>
      ) : (
        <>
          {config.mismatches.length > 0 ? (
            <ul className="fleet-config__mismatches" role="alert">
              {config.mismatches.map((mismatch) => (
                <MismatchRow key={`${mismatch.axis}-${mismatch.robot_type}`}
                  mismatch={mismatch} />
              ))}
            </ul>
          ) : (
            <p className="fleet-config__ok">
              보고된 형상이 서로 같습니다. 아래 표에서 보고가 없는 로봇은
              비교에 포함되지 않았습니다.
            </p>
          )}

          <div className="fleet-config__scroll">
            <table className="fleet-config__table">
              <thead>
                <tr>
                  <th scope="col">로봇</th>
                  <th scope="col">코드 커밋</th>
                  <th scope="col">맵 지문</th>
                  <th scope="col">정책</th>
                </tr>
              </thead>
              <tbody>
                {config.robots.map((robot) => (
                  <RobotRow key={robot.robot_id} robot={robot} />
                ))}
              </tbody>
            </table>
          </div>

          <small>
            맵은 이름이 아니라 격자 지문으로 비교합니다. 같은 이름의 다른 맵이
            실제로 있습니다. 조제 로봇의 코드 형상은 게이트웨이가 연결되면
            표시됩니다.
          </small>
        </>
      )}
    </section>
  )
}
