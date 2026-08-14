/**
 * 배터리 표시. 원본(전압)과 파생(퍼센트)을 함께 보여준다.
 *
 * 퍼센트는 전압의 클램프된 파생값이다 (004_battery_voltage.sql).
 *     percent = (V - 6.8) / (7.6 - 6.8) * 100
 * 7.6V 위는 전부 100% 라, 퍼센트만 보면 충전 중인지 만충인지 알 수 없고
 * 기울기도 0 이라 남은 시간을 추정할 수도 없다. 판정은 전압으로 한다.
 *
 * 화면마다 어휘가 다르다.
 *     의료진   100% (충전 중)   — 전압은 툴팁으로
 *     엔지니어 7.92V · 100%     — 원본을 먼저
 * 의료진에게 전압을 들이밀면 해석하려다 잘못 판단한다. 엔지니어에게
 * 퍼센트만 주면 클램프 구간에서 아무것도 못 한다.
 */

import { BATTERY_FRESHNESS, freshnessLevel, isClamped, stalenessClass } from '../lib/freshness'
import { CLAMP_VOLTAGE } from '../lib/freshness'
import { Freshness } from './Freshness'

interface Props {
  voltage: number | null
  percent: number | null
  recordedAt: string | null
  /** 'staff' 는 퍼센트 위주, 'engineer' 는 전압 위주. */
  audience: 'staff' | 'engineer'
  /**
   * 충전 중인가 (battery-forecast 의 direction).
   *
   * 충전 중에는 충전기가 단자 전압을 끌어올려 잔량이 18% 여도 100% 로
   * 보인다. 실측으로 6.94V(18%) 에서 꽂자마자 7.64V(100%) 가 됐다.
   * 그럴 때는 숫자를 감춘다 — 100% 는 옆에 '클램프' 배지를 붙여도
   * 사람이 숫자만 읽는다.
   */
  charging?: boolean
}

export function BatteryReading({
  voltage, percent, recordedAt, audience, charging = false,
}: Props) {
  const level = freshnessLevel(recordedAt, BATTERY_FRESHNESS)
  const dim = stalenessClass(level)
  const clamped = isClamped(voltage)
  // 충전 중 + 클램프면 퍼센트는 잔량이 아니다. 숫자를 내보내지 않는다.
  const percentUnknown = clamped && charging

  // 전압 없이 퍼센트만 오는 응답이 실제로 있다. 변환 노드는 살아 있는데
  // ADC 읽기가 실패한 경우다. 이때 화면이 정상처럼 보이면 안 된다.
  const voltageLabel = voltage != null ? `${voltage.toFixed(2)}V` : '전압 없음'
  const percentLabel = percentUnknown
    ? '잔량 확인 불가'
    : percent != null ? `${percent}%` : '—'

  return (
    <div className="battery-reading">
      <div className={`battery-reading-primary ${dim}`}>
        {audience === 'engineer' ? (
          <>
            <span className="battery-reading-voltage mono">{voltageLabel}</span>
            <span className="battery-reading-sep">·</span>
            <span className="battery-reading-derived">{percentLabel}</span>
          </>
        ) : (
          <span
            className="battery-reading-percent"
            title={voltage != null ? `${voltageLabel} (원본 측정값)` : undefined}
          >
            {percentLabel}
          </span>
        )}
        {clamped && (
          <span
            className="battery-reading-clamp"
            title={`${CLAMP_VOLTAGE}V 이상은 모두 100% 로 표시된다. 이 숫자는 더 올라가지 않는다.`}
          >
            {audience === 'engineer'
              ? (percentUnknown ? '클램프 · 충전 중' : '클램프')
              : '충전 중'}
          </span>
        )}
      </div>
      <Freshness at={recordedAt} {...BATTERY_FRESHNESS} />
    </div>
  )
}
