/**
 * arm 거부 사유를 의료진이 읽는 문장으로 바꾼다.
 *
 * 백엔드는 기계용 코드(`battery_stale`)와 숫자(`age_sec`)만 내려준다
 * (routers/robots.py 의 `_reject`). 문장을 만드는 건 화면의 일이다 —
 * 의료진 화면과 엔지니어 화면은 같은 사실을 다른 어휘로 말해야 한다.
 *
 * 백엔드 영어 문자열을 파싱하지 않는다. 파싱하면 백엔드가 문구를 못 고치고,
 * 결국 아무도 안 고쳐서 영어가 그대로 의료진에게 보인다.
 */

/** 백엔드 `_reject` 가 내려주는 409 본문. */
export interface RejectionDetail {
  code: string
  params: Record<string, unknown>
  /** 구버전 호환용 영어 문구. 화면에 그대로 쓰지 않는다. */
  message: string
}

export interface RejectionMessage {
  /** 의료진에게 보일 한 줄. */
  text: string
  /** 다음에 뭘 하면 되는지. */
  action: string
}

function num(params: Record<string, unknown>, key: string): number | null {
  const value = params[key]
  return typeof value === 'number' ? value : null
}

function minutes(seconds: number | null): string {
  if (seconds == null) return '알 수 없는 시간'
  if (seconds < 60) return `${seconds}초`
  return `${Math.floor(seconds / 60)}분`
}

/**
 * 모르는 코드가 와도 화면이 비지 않게 한다.
 *
 * 백엔드가 새 코드를 먼저 배포하는 순서는 정상이다. 그때 의료진 화면이
 * 빈 칸을 보여주면 "왜 안 되는지" 를 아무도 모른다. 코드라도 보여준다.
 */
const FALLBACK: RejectionMessage = {
  text: '지금은 이 로봇을 사용할 수 없습니다',
  action: '엔지니어 호출',
}

export function rejectionMessage(detail: RejectionDetail): RejectionMessage {
  const { code, params } = detail

  switch (code) {
    case 'robot_inactive':
      return { text: '점검 중인 로봇입니다', action: '다른 로봇 선택' }

    case 'not_mobile':
      return { text: '안내용 로봇이 아닙니다', action: '다른 로봇 선택' }

    case 'robot_busy':
      return { text: '다른 환자를 안내 중입니다', action: '다른 로봇 선택 또는 대기' }

    case 'robot_unavailable':
      return {
        text: params.state === 'returning_to_dock'
          ? '충전소로 복귀 중인 로봇입니다'
          : '안전 확인이 필요한 로봇입니다',
        action: '다른 로봇 선택 또는 대기',
      }

    case 'robot_offline':
      return {
        text: `로봇과 연결이 끊긴 지 ${minutes(num(params, 'last_seen_sec'))} 됐습니다`,
        action: '엔지니어 호출',
      }

    case 'link_unknown':
      return { text: '로봇과 아직 연결된 적이 없습니다', action: '엔지니어 호출' }

    case 'battery_low': {
      const percent = num(params, 'percent')
      return {
        text: `배터리가 ${percent ?? '?'}% 라 충전이 필요합니다`,
        action: '다른 로봇 선택',
      }
    }

    case 'battery_unknown':
      return { text: '배터리 정보를 받지 못했습니다', action: '엔지니어 호출' }

    case 'battery_charging':
      // 충전 중에는 단자 전압이 올라가 잔량이 실제보다 높게 보인다.
      // 100% 로 보이는 로봇이 실제로는 거의 비어 있을 수 있다.
      return {
        text: '충전 중이라 잔량을 확인할 수 없습니다',
        action: '충전을 마친 뒤 다시 선택',
      }

    case 'battery_stale':
      return {
        text: `배터리 정보가 ${minutes(num(params, 'age_sec'))}째 갱신되지 않습니다`,
        action: '엔지니어 호출',
      }

    default:
      return FALLBACK
  }
}

/**
 * axios 에러에서 거부 본문을 꺼낸다.
 *
 * detail 이 아직 문자열인 응답(구버전 백엔드)도 받아넘긴다. 배포 순서가
 * 어긋나도 화면이 죽지 않아야 한다.
 */
export function toRejectionDetail(error: unknown): RejectionDetail | null {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail

  if (typeof detail === 'string') {
    return { code: 'legacy', params: {}, message: detail }
  }
  if (detail && typeof detail === 'object' && 'code' in detail) {
    const record = detail as Record<string, unknown>
    return {
      code: String(record.code),
      params: (record.params as Record<string, unknown>) ?? {},
      message: String(record.message ?? ''),
    }
  }
  return null
}
