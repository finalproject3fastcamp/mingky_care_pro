/**
 * 값의 나이 판정. 순수 함수만 둔다.
 *
 * 값만 보여주는 화면은 거짓말을 할 수 있다. `57%` 는 못 믿지만
 * `57% · 12분 전` 은 믿을 수 있다 — 틀린 값이 문제가 아니라 틀렸는지 알 수
 * 없는 값이 문제였다.
 *
 * 판정 기준은 화면마다 다르다. 배터리는 2분 주기 로그라 5분이면 낡은
 * 것이지만 heartbeat 는 5초 주기라 15초면 이미 두절이다. 그래서 임계를
 * 이 파일에 박지 않고 호출부가 넘긴다.
 */

export type FreshnessLevel = 'fresh' | 'warn' | 'stale' | 'unknown'

export interface FreshnessThresholds {
  /** 이 초를 넘으면 주의. */
  warnSec: number
  /** 이 초를 넘으면 값을 믿을 수 없다고 본다. */
  staleSec: number
}

/** 배터리 로그는 2분 주기다 (게이트웨이 battery_interval_sec). */
export const BATTERY_FRESHNESS: FreshnessThresholds = { warnSec: 180, staleSec: 300 }

/** heartbeat 는 5초 주기, 백엔드 두절 판정은 15초다 (HEARTBEAT_OFFLINE_AFTER_SEC). */
export const HEARTBEAT_FRESHNESS: FreshnessThresholds = { warnSec: 10, staleSec: 15 }

/**
 * 나이 판정.
 *
 * `at` 이 null 이면 'unknown' 이다. 'stale' 과 구분해야 한다 — 전자는 "한
 * 번도 받은 적 없음", 후자는 "받았지만 오래됨" 이고 대응이 다르다.
 *
 * 시계가 어긋나 미래 시각이 오면 나이가 음수가 된다. 그건 fresh 로 본다.
 * 로봇 시계가 앞선 것이지 값이 낡은 건 아니다.
 */
export function freshnessLevel(
  at: string | null | undefined,
  thresholds: FreshnessThresholds,
  now: number = Date.now(),
): FreshnessLevel {
  if (!at) return 'unknown'
  const parsed = new Date(at).getTime()
  if (Number.isNaN(parsed)) return 'unknown'

  const ageSec = (now - parsed) / 1000
  if (ageSec >= thresholds.staleSec) return 'stale'
  if (ageSec >= thresholds.warnSec) return 'warn'
  return 'fresh'
}

/**
 * 사람이 읽는 나이. "12분 전".
 *
 * 초 단위를 그대로 노출하지 않는다. `743초 전` 은 계산을 요구하지만
 * `12분 전` 은 즉시 판단할 수 있다.
 */
export function formatAge(
  at: string | null | undefined,
  now: number = Date.now(),
): string {
  if (!at) return '기록 없음'
  const parsed = new Date(at).getTime()
  if (Number.isNaN(parsed)) return '기록 없음'

  const ageSec = Math.floor((now - parsed) / 1000)
  if (ageSec < 10) return '방금'
  if (ageSec < 60) return `${ageSec}초 전`

  const minutes = Math.floor(ageSec / 60)
  if (minutes < 60) return `${minutes}분 전`

  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}시간 전`

  return `${Math.floor(hours / 24)}일 전`
}

/**
 * 값 요소에 붙일 클래스. 값 자체를 흐리게 만드는 것이 목적이다.
 *
 * 나이 표시만 경고색으로 바꾸는 걸로는 부족하다. 사람은 숫자를 먼저 읽는다.
 * 값 자체가 흐려져야 "이 숫자를 믿지 마라" 가 말없이 전달된다.
 */
export function stalenessClass(level: FreshnessLevel): string {
  return level === 'stale' || level === 'unknown' ? 'value-stale' : ''
}

/** pinkylib 기준 2셀 리튬이온. 이 위로는 퍼센트가 더 안 올라간다. */
export const CLAMP_VOLTAGE = 7.6

/**
 * 클램프 구간인가 — 퍼센트가 더 올라가지 않는 상태인가.
 *
 * 전압이 없으면 판정할 수 없다. 퍼센트가 100 이어도 클램프인지 정확히
 * 만충인지 구분이 안 되므로 false 를 돌려준다. 모르는 걸 안다고 하지 않는다.
 */
export function isClamped(voltage: number | null): boolean {
  return voltage != null && voltage > CLAMP_VOLTAGE
}
