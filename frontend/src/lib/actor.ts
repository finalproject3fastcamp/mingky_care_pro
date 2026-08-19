/**
 * 제어 명령을 누른 사람. 감사 로그의 actor 가 된다.
 *
 * ## 인증이 아니다
 *
 * 이 시스템에는 로그인이 없다(backend/app/actor.py 참고). 여기 적는 이름은
 * 자기신고이고 위조를 막을 방법이 없다. 그래도 남기는 이유는 병원 도메인의
 * 감사 요건과 SLO 판정(§1.1) 때문이다 — "누가 눌렀는지 모른다" 보다
 * "간호사 스테이션 2 가 눌렀다" 가 사고 조사에서 훨씬 쓸모 있다.
 *
 * ## 왜 명령마다 안 묻는가
 *
 * 확인 다이얼로그에서 매번 입력받는 쪽이 정확하지만, 그러면 **비상정지 앞에
 * 단계가 하나 생긴다.** 감사를 위해 안전 조작을 느리게 만드는 건 맞바꿀 수
 * 없는 거래다. 한 번 적어두고 브라우저가 기억한다.
 *
 * 비어 있어도 명령은 그대로 나간다. 서버가 익명으로 기록하고 fleet 탭이 그
 * 비율을 드러낸다 — 막는 대신 보이게 하는 것이 이 설계의 원칙이다.
 */

const STORAGE_KEY = 'mingky.actor'

/**
 * control_audit.actor 가 VARCHAR(50) 이다.
 *
 * 서버도 잘라내지만(actor.py) 여기서 먼저 막는다. 화면에 51자를 적어놓고
 * 기록은 50자인 상태가 되면, 나중에 감사 로그를 보는 사람이 이름이 왜 다른지
 * 알 수 없다.
 */
export const MAX_ACTOR_LENGTH = 50

/** 저장된 조작자 이름. 없으면 빈 문자열. */
export function getActor(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? ''
  } catch {
    // 사파리 프라이빗 모드 등에서 localStorage 접근 자체가 던진다.
    // 이름을 못 읽는 것이 화면을 죽일 이유는 없다 — 익명으로 간다.
    return ''
  }
}

export function setActor(name: string): string {
  const normalized = name.trim().slice(0, MAX_ACTOR_LENGTH)
  try {
    if (normalized) {
      localStorage.setItem(STORAGE_KEY, normalized)
    } else {
      localStorage.removeItem(STORAGE_KEY)
    }
  } catch {
    // 위와 같다. 이번 세션에서만 안 남을 뿐이다.
  }
  return normalized
}
