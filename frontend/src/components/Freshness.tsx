/**
 * 값의 나이를 값 옆에 붙여 보여주는 배지.
 *
 * 판정 로직은 lib/freshness.ts 에 있다. 낡은 값 자체를 흐리게 하려면
 * 호출부가 값 요소에 `stalenessClass(level)` 을 함께 입혀야 한다 —
 * 이 배지만으로는 부족하다. 사람은 숫자를 먼저 읽기 때문이다.
 */

import { formatAge, freshnessLevel } from '../lib/freshness'
import type { FreshnessThresholds } from '../lib/freshness'

interface Props extends FreshnessThresholds {
  at: string | null | undefined
  /** 라벨 앞에 붙일 구분자. 빈 문자열이면 생략한다. */
  separator?: string
}

/**
 * `title` 에 원본 시각을 그대로 넣는다. "12분 전" 으로 판단은 되지만
 * 로그와 대조할 때는 절대 시각이 필요하다.
 */
export function Freshness({ at, warnSec, staleSec, separator = '·' }: Props) {
  const level = freshnessLevel(at, { warnSec, staleSec })
  const label = formatAge(at)
  const absolute = at ? new Date(at).toLocaleString('ko-KR') : '수신 기록 없음'

  return (
    <span className={`freshness freshness--${level}`} title={absolute}>
      {separator && <span className="freshness-sep">{separator}</span>}
      {label}
    </span>
  )
}
