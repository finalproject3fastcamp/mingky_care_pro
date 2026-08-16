/**
 * 서보 온도·전류 — 조제 패널 (§4.4 · 로드맵 11).
 *
 * Dynamixel 이 공짜로 주는 유일한 예지보전 신호다. mobile 에는 대응물이 없다.
 *
 * ## 두 가지를 따로 그린다
 *
 * **지금 뜨거운 것**(state)과 **오르는 중인 것**(rising)은 다른 사실이다.
 * 40℃ 인데 회차마다 오르는 조인트가, 55℃ 에서 평평한 조인트보다 나쁜 신호다 —
 * 전자는 그리퍼 마모나 과부하 자세이고 후자는 그냥 그런 축이다. 한 배지로
 * 뭉개면 예지보전 신호가 사라지고 온도계 하나만 남는다.
 *
 * ## 추세가 비어 있는 것이 정상이다
 *
 * 표본이 모자라거나 조제 부하가 출렁이면 서버가 기울기를 내지 않는다.
 * 그때 화면이 0 을 그리면 "평평하다" 는 거짓말이 된다 — 배터리 예상에서
 * seconds 가 null 인 것과 같은 규칙이다.
 */

import type { ServoHealth, ServoReading } from '../types/monitoring'

const STATE_LABEL: Record<ServoReading['state'], string> = {
  fault: '하드웨어 결함',
  hot: '과열',
  warm: '따뜻함',
  ok: '정상',
  unknown: '온도 없음',
}

/** 빨강은 사람이 손을 대야 하는 둘에만. 따뜻함은 정상 범위다. */
const STATE_TONE: Record<ServoReading['state'], string> = {
  fault: 'metric-error',
  hot: 'metric-error',
  warm: 'metric-warn',
  ok: '',
  unknown: 'metric-warn',
}

function temperature(servo: ServoReading): string {
  return servo.temp_c === null ? '—' : `${servo.temp_c.toFixed(1)}℃`
}

function current(servo: ServoReading): string {
  // 부호는 방향이다. 부하는 절대값으로 읽는 편이 빠르다.
  return servo.current_ma === null
    ? '—'
    : `${Math.round(Math.abs(servo.current_ma))}mA`
}

interface Props {
  health: ServoHealth | null
  loading: boolean
  error: unknown
}

export function ServoHealthCard({ health, loading, error }: Props) {
  const servos = health?.servos ?? []

  return (
    <section className="card servo-health" aria-label="서보 상태">
      <div className="card-title">서보 — 온도 · 전류</div>

      {error ? (
        <p className="manipulator-empty">서보 상태를 불러오지 못했습니다.</p>
      ) : servos.length === 0 ? (
        <p className="manipulator-empty">
          {loading
            ? '불러오는 중…'
            : '서보 보고가 없습니다. U2D2 로 읽은 값이 올라오면 이 카드가 채워집니다.'}
        </p>
      ) : (
        <>
          <ul className="servo-health__list">
            {servos.map((servo) => (
              <li key={servo.joint} className="servo-health__row">
                <code className="servo-health__joint">{servo.joint}</code>
                <span className={`servo-health__state ${STATE_TONE[servo.state]}`}>
                  {STATE_LABEL[servo.state]}
                </span>
                <span className="servo-health__temp mono">
                  {temperature(servo)}
                  {servo.hot_temp_c !== null && (
                    <span className="servo-health__limit"> / {servo.hot_temp_c}℃</span>
                  )}
                </span>
                <span className="servo-health__current mono">{current(servo)}</span>

                {/* 지금 온도와 무관한 신호다. 정상 온도에서 오르는 쪽이 오히려
                    먼저 잡아야 하는 상태다. */}
                {servo.rising && (
                  <span className="servo-health__rising">
                    상승 중 · 시간당 {servo.slope_c_per_hour?.toFixed(1)}℃
                  </span>
                )}

                {servo.hardware_error !== null && servo.hardware_error !== 0 && (
                  <span className="servo-health__fault">
                    에러 비트 0x{servo.hardware_error.toString(16)}
                  </span>
                )}
              </li>
            ))}
          </ul>

          <small>
            추세는 직전 {Math.round((health?.window_min ?? 0) / 60)}시간 표본으로
            냅니다. 표본이 모자라거나 부하가 출렁이면 기울기를 내지 않습니다 —
            틀린 추세는 없는 추세보다 나쁩니다.
          </small>
        </>
      )}
    </section>
  )
}
