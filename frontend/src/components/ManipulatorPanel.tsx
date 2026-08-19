/**
 * 조제 로봇(OMX) 전용 패널 — System 탭에서 팔을 골랐을 때 (§7.3 · 로드맵 7).
 *
 * 주행 로봇의 제어 카드(통합 시스템·주행 속도·화재 경보)는 팔에 하나도
 * 성립하지 않는다. 대신 팔에 대해 사람이 실제로 묻는 세 가지를 답한다.
 *
 *   지금 무엇을 하고 있나        조제 중 · 대기 · 홈 복귀 필요
 *   잘 하고 있나                 pick 성공률 · 사이클 타임 (§7.2)
 *   무엇이 돌고 있나             정책 체크포인트 · 데이터셋 revision (§4.4)
 *
 * ## 읽는 사람이 헷갈리면 안 되는 것
 *
 * pick 실패는 고장이 아니다. 모방학습 성공률은 원래 90% 근처라 개별 실패가
 * 정상 동작 범위이고, 정본에서도 `warning` 이다(§6.2). 여기서 실패 건수를
 * 빨갛게 그리면 사람이 매번 팔을 보러 오고, 그러면 진짜 `error` 인 사이클
 * 포기와 서보 결함이 묻힌다. 빨강은 그 둘에만 쓴다.
 *
 * ## 버튼이 없는 이유
 *
 * §4.4 의 팔 개입(홈 복귀·재시도)은 orders 큐의 이산 명령인데, 그 명령을
 * 받아 실행할 OMX 게이트웨이가 아직 없다(§6.2 "발행 측은 아직 없다").
 * 누르면 큐에만 쌓이고 아무 일도 안 일어나는 버튼은 없는 것보다 나쁘다.
 */

import { Freshness } from './Freshness'
import { RobotResourceCard } from './RobotResourceCard'
import { ServoHealthCard } from './ServoHealthCard'
import { getServoHealth } from '../lib/api'
import { usePolling } from '../lib/usePolling'
import type { ManipulatorRobot } from '../types/monitoring'

/**
 * 서보 표본은 분 단위로 올라오고 추세는 몇 시간 창이다. 조제 상태(3초)와
 * 같은 주기로 물어볼 이유가 없고, 그 주기로 물으면 추세 집계가 3초마다 돈다.
 */
const SERVO_POLL_MS = 60000

/**
 * 조제 이력의 나이 기준. 실측 전 잠정값이다.
 *
 * 사이클 하나가 10~15초인데 마지막 사이클이 30분 전이면 팔이 놀고 있는
 * 것이고, 두 시간이 넘었으면 이 화면의 숫자로 지금을 판단하면 안 된다.
 */
const DISPENSE_FRESHNESS = { warnSec: 1800, staleSec: 7200 }

function formatRate(rate: number | null): string {
  return rate === null ? '—' : `${(rate * 100).toFixed(1)}%`
}

function formatDuration(ms: number | null): string {
  return ms === null ? '—' : `${(ms / 1000).toFixed(1)}초`
}

function formatTime(iso: string | null): string {
  return iso === null ? '—' : new Date(iso).toLocaleString('ko-KR')
}

/** 팔이 지금 무엇을 하고 있는가. 셋은 서로 배타적이다. */
function cycleState(robot: ManipulatorRobot) {
  const { detail } = robot
  if (detail.homing_required) {
    return { tone: 'error' as const, label: '홈 복귀 필요' }
  }
  if (detail.active_dispense_id !== null) {
    return { tone: 'busy' as const, label: '조제 중' }
  }
  return { tone: 'idle' as const, label: '대기' }
}

export function ManipulatorPanel({ robot }: { robot: ManipulatorRobot }) {
  const { detail } = robot
  // key 를 주지 않으면 팔을 바꿔도 최대 1분간 이전 팔의 서보가 남는다.
  // 그 사이 "이 팔은 정상" 으로 읽히면 카드의 존재 이유가 사라진다.
  const servos = usePolling(
    (signal) => getServoHealth(robot.robot_id, { signal }),
    SERVO_POLL_MS,
    robot.robot_id,
  )
  const state = cycleState(robot)
  const picks = detail.pick_succeeded + detail.pick_failed
  // 보고가 하나도 없는 팔. 0% 나 0건으로 그리면 고장으로 읽힌다.
  const silent = detail.window_cycles === 0 && picks === 0
    && detail.policy_checkpoint_id === null

  return (
    <div className="manipulator-grid">
      <section className="card manipulator-state" aria-label="조제 상태">
        <div className="card-title">조제 상태</div>
        <div className="manipulator-state__headline">
          <strong data-tone={state.tone}>{state.label}</strong>
          {detail.active_dispense_id !== null && (
            <span>
              <code>{detail.active_dispense_id}</code>
              <Freshness at={detail.active_started_at} {...DISPENSE_FRESHNESS} />
            </span>
          )}
        </div>

        {/* 홈 복귀는 사람이 지시해야 풀린다. 이유를 같이 보여주지 않으면
            무엇을 확인하고 복귀시켜야 하는지 알 수 없다. */}
        {detail.homing_required && (
          <p className="manipulator-alert" role="alert">
            사이클이 중간에 멈췄습니다{detail.homing_reason && ` (${detail.homing_reason})`}.
            트레이와 그리퍼를 확인한 뒤 현장에서 홈 복귀를 지시하세요.
          </p>
        )}

        {detail.last_servo_fault && (
          <div className="manipulator-fault" role="alert">
            <span className="manipulator-fault__title">서보 결함</span>
            <span>
              <code>{detail.last_servo_fault.joint}</code>
              {detail.last_servo_fault.temp_c !== null
                && ` · ${detail.last_servo_fault.temp_c.toFixed(1)}℃`}
              {detail.last_servo_fault.fault_bits !== null
                && ` · 에러 비트 0x${detail.last_servo_fault.fault_bits.toString(16)}`}
              {/* 해소 이벤트가 없다. 나이를 안 보여주면 3일 전 결함이 방금
                  일어난 것처럼 보인다. */}
              <Freshness at={detail.last_servo_fault.occurred_at} {...DISPENSE_FRESHNESS} />
            </span>
          </div>
        )}

        <div className="manipulator-meta">
          <span>마지막 사이클</span>
          <strong>
            {formatTime(detail.last_cycle_ended_at)}
            <Freshness at={detail.last_cycle_ended_at} {...DISPENSE_FRESHNESS} />
          </strong>
        </div>
      </section>

      <section className="card manipulator-performance" aria-label="조제 성능">
        <div className="card-title">조제 성능 — 직전 {detail.window} 사이클</div>

        {silent ? (
          <p className="manipulator-empty">
            아직 조제 보고가 없습니다. 정본(§6.2)과 검증 경로는 서 있고,
            OMX 게이트웨이가 붙으면 이 카드가 채워집니다.
          </p>
        ) : (
          <>
            <div className="manipulator-headline">
              {/* 표본 0 과 성공률 0% 는 다른 사실이다. 전자는 '아직 모른다',
                  후자는 '한 번도 못 집었다' 이고 대응이 정반대다. */}
              <strong className={`manipulator-rate manipulator-rate--${
                detail.pick_success_rate === null ? 'unknown' : 'known'}`}>
                {formatRate(detail.pick_success_rate)}
              </strong>
              <div className="manipulator-headline__meta">
                <span>pick 성공률</span>
                <span>
                  성공 {detail.pick_succeeded} · 실패 {detail.pick_failed}
                </span>
                {/* 재시도로 성공한 건. 한 번에 집은 것과 구분하지 못하면
                    성공률이 정책 품질을 반영하지 못한다. */}
                <span>재시도 성공 {detail.pick_retried}건</span>
                {!detail.sample_complete && (
                  <span className="manipulator-sample">
                    표본 {detail.window_cycles}/{detail.window} — 판정 근거 부족
                  </span>
                )}
              </div>
            </div>

            <div className="manipulator-metrics">
              <div>
                <span>사이클 완주</span>
                <strong>{detail.cycles_completed}건</strong>
              </div>
              <div>
                {/* 포기는 error 다. 조제분이 사라졌으므로 사람이 트레이를
                    확인해야 한다 (§4.4). */}
                <span>사이클 포기</span>
                <strong data-tone={detail.cycles_aborted > 0 ? 'error' : undefined}>
                  {detail.cycles_aborted}건
                </strong>
              </div>
              <div>
                <span>사이클 타임 p50</span>
                <strong>{formatDuration(detail.cycle_time_p50_ms)}</strong>
              </div>
              <div>
                <span>p95</span>
                <strong>{formatDuration(detail.cycle_time_p95_ms)}</strong>
              </div>
            </div>

            <small>
              pick 실패는 확률적 실패라 경고 등급입니다. 사람을 불러야 하는 것은
              사이클 포기와 서보 결함입니다.
            </small>
          </>
        )}
      </section>

      <section className="card manipulator-policy" aria-label="정책">
        <div className="card-title">정책</div>
        {detail.policy_checkpoint_id === null ? (
          <p className="manipulator-empty">
            정책 보고가 없습니다. 어떤 체크포인트로 돌고 있는지 모르는 상태라
            pick 실패의 원인을 코드에서 찾게 됩니다.
          </p>
        ) : (
          <>
            {/* 어제 되던 pick 이 오늘 안 되는 원인은 대개 코드가 아니라
                체크포인트를 바꿔 낀 것이다 (§4.4). git SHA 로는 안 보인다. */}
            <div className="manipulator-meta">
              <span>체크포인트</span>
              <strong><code>{detail.policy_checkpoint_id}</code></strong>
            </div>
            <div className="manipulator-meta">
              <span>데이터셋</span>
              <strong><code>{detail.policy_dataset_revision ?? '—'}</code></strong>
            </div>
            <div className="manipulator-meta">
              <span>적재 시각</span>
              <strong>{formatTime(detail.policy_loaded_at)}</strong>
            </div>
          </>
        )}
      </section>

      {/* Dynamixel 이 공짜로 주는 예지보전 신호 (§4.4). mobile 에는 없다. */}
      <ServoHealthCard health={servos.data ?? null} loading={servos.loading}
        error={servos.error} />

      {/* 자원·큐는 종류와 무관한 공통 축이다 (§4.3). 팔도 heartbeat 를 보낸다. */}
      <RobotResourceCard robot={robot} />
    </div>
  )
}
