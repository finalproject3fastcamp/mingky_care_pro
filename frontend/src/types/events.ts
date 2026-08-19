/**
 * 엔지니어 대시보드가 쓰는 이벤트 타입.
 *
 * 정본은 backend/app/schemas.py 의 EventOut / EventPage 다.
 * 백엔드 스키마가 바뀌면 여기도 같이 고친다.
 *
 * monitoring.ts 에 넣지 않은 이유는 그쪽이 의료진 화면과 공유되는 파일이라서다.
 * 이벤트 타임라인만 쓰는 타입을 섞으면 서로 다른 담당자가 같은 파일을 건드린다.
 */

/**
 * DB 의 CHECK (level IN ('info','warning','error')) 와 같다.
 * (003_sessions_and_events.sql)
 *
 * monitoring.ts 의 NotificationLevel 과 값이 같지만 일부러 따로 둔다.
 * 저쪽은 의료진 화면의 알림 등급이고 이쪽은 DB 컬럼이다. 지금 우연히
 * 일치할 뿐 서로를 따라갈 이유가 없다.
 */
export type EventLevel = 'info' | 'warning' | 'error'

/** config/event_codes.yaml 의 event_code 접두사. 필터 드롭다운의 선택지다. */
export const EVENT_CODE_PREFIXES = [
  'qr.',
  'patient.',
  'nav.',
  'session.',
  'robot.',
  'system.',
] as const

export type EventCodePrefix = (typeof EVENT_CODE_PREFIXES)[number]

/** GET /events 응답의 한 건. schemas.py 의 EventOut 과 1:1. */
export interface EventOut {
  /** UUID. 로봇이 생성하므로 재전송돼도 값이 같다. React key 로 그대로 쓴다. */
  event_id: string
  /** events.robot_id 는 nullable 이다. */
  robot_id: string | null
  /**
   * 세션과 무관한 이벤트는 null 로 온다.
   * 로봇은 0 을 보내지만(EventIn.session_id 기본값) DB 는 FK 라 null 로 저장된다.
   * number 로 잡으면 런타임에 깨진다.
   */
  session_id: number | null
  /** 로봇 시계. ISO8601 + 오프셋 문자열 (예: 2026-08-04T11:26:11.401831Z). */
  occurred_at: string
  /**
   * 서버 수신 시각. occurred_at 과의 차이가 게이트웨이 큐잉 지연이다.
   * 두 값이 벌어진 이벤트는 통신 두절 중 쌓였다가 몰려온 것이다.
   */
  received_at: string
  level: EventLevel
  event_code: string
  /** 노드별 필터용. nullable 컬럼이다. */
  source_node: string | null
  /**
   * JSONB 라 event_code 마다 키가 다르고, DB 가 내용을 검증하지 않는다.
   * 값에 null 이 섞여 들어오므로 unknown 으로 받고 쓰는 쪽에서 좁힌다.
   */
  payload: Record<string, unknown>
}

/** GET /events 응답 전체. schemas.py 의 EventPage 와 1:1. */
export interface EventPage {
  /** 필터 조건에 맞는 전체 건수. 화면에 실린 건수가 아니다. */
  total: number
  limit: number
  offset: number
  items: EventOut[]
}

/**
 * GET /events/unknown-codes 응답의 한 건. schemas.py 의 UnknownCodeOut 과 1:1.
 *
 * config/event_codes.yaml 에 없는 코드가 얼마나 들어왔는지를 센다. 적재
 * 자체는 되므로 데이터가 사라진 건 아니지만, 등록되지 않은 코드는 상태
 * 갱신을 타지 않아 화면 판정에서 통째로 빠진다.
 */
export interface UnknownCode {
  event_code: string
  /** events.robot_id 가 nullable 이라 여기도 null 이 올 수 있다. */
  robot_id: string | null
  count: number
  first_seen: string
  last_seen: string
}

/**
 * GET /events 의 쿼리 파라미터. 전부 optional 이다.
 * 제약은 routers/events.py 의 Query(...) 선언을 따른다.
 */
export interface EventQuery {
  robot_id?: string
  session_id?: number
  /** 이 등급 이상만. warning 을 주면 warning 과 error 가 나온다. */
  min_level?: EventLevel
  /** 접두사 LIKE. 예: 'nav.' */
  code_prefix?: string
  source_node?: string
  /**
   * occurred_at >= since. **반드시 오프셋이 붙은 ISO 문자열로 보낸다.**
   * <input type="datetime-local"> 이 주는 '2026-08-06T11:00' 같은 naive 문자열을
   * 그대로 보내면 asyncpg 가 백엔드 프로세스의 로컬 타임존으로 해석한다.
   * 개발 PC(KST)에서는 맞게 동작하다가 UTC 컨테이너에 배포하는 순간
   * 9시간 어긋나고, 에러 없이 결과만 틀린다.
   */
  since?: string
  /** occurred_at < until. since 와 같은 규칙. */
  until?: string
  /** 1..500, 서버 기본값 100. */
  limit?: number
  /** 0 이상, 서버 기본값 0. */
  offset?: number
}
