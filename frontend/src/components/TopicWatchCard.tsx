/**
 * 토픽 주기 감시 — System 탭 (§7.2 · 로드맵 9).
 *
 * 이 카드가 없던 동안 화면은 systemd 유닛 상태만 보여줬다. 그래서 **유닛은
 * active 인데 `/scan` 이 안 나오는** 상태가 관제에서 정상으로 보였다. 라이다
 * USB 가 죽어도 노드 프로세스는 살아 있다.
 *
 * ## 판정을 여기서 다시 하지 않는다
 *
 * 임계는 서버의 config/topic_watch.yaml 이 정본이고 응답에 state 가 실려 온다.
 * 화면이 자기 숫자로 색을 칠하면 설정을 고쳐도 색이 안 바뀌고, 그 순간 어느
 * 쪽이 맞는지 아무도 모른다.
 *
 * ## 빨강을 아끼는 이유
 *
 * `/cmd_vel` 은 서 있는 로봇에서 안 나오는 게 정상이다(idle). 그걸 빨갛게
 * 그리면 대기 중인 로봇 2대가 항상 경고 상태가 되고, 그 빨강 속에 진짜
 * 라이다 두절이 묻힌다.
 */

import { Freshness } from './Freshness'
import { HEARTBEAT_FRESHNESS } from '../lib/freshness'
import type { MobileRobot, TopicAge } from '../types/monitoring'

/** 상태별 한 줄 설명. 문구를 서버가 주지 않는 부분(판정 어휘)만 여기 있다. */
const STATE_LABEL: Record<TopicAge['state'], string> = {
  fresh: '정상',
  slow: '늦음',
  stale: '끊김',
  idle: '쉬는 중',
  missing: '나이 없음',
  unwatched: '감시 안 함',
  unrated: '판정 없음',
}

/** 빨강·주황은 사람이 손을 대야 하는 둘에만 쓴다. */
const STATE_TONE: Record<TopicAge['state'], string> = {
  fresh: '',
  slow: 'metric-warn',
  stale: 'metric-error',
  idle: '',
  missing: 'metric-warn',
  unwatched: 'metric-warn',
  unrated: '',
}

function formatAge(sec: number | null): string {
  if (sec === null) return '—'
  if (sec < 1) return `${Math.round(sec * 1000)}ms 전`
  if (sec < 60) return `${sec.toFixed(1)}초 전`
  return `${Math.round(sec / 60)}분 전`
}

function formatRate(topic: TopicAge): string {
  // 측정값이 없는 것과 0Hz 는 다른 사실이다. 전자는 창에 표본이 하나뿐이라
  // 간격을 못 잰 것이고, 후자는 정말 안 오는 것이다.
  const measured = topic.hz === null ? '—' : `${topic.hz.toFixed(1)}Hz`
  return topic.expected_hz === null
    ? measured
    : `${measured} / 기대 ${topic.expected_hz}Hz`
}

export function TopicWatchCard({ robot }: { robot: MobileRobot }) {
  const topics = robot.topics

  return (
    <section className="card topic-watch" aria-label="토픽 주기 감시">
      <div className="card-title">
        토픽 주기
        {/* heartbeat 가 실어 오는 값이다. heartbeat 자체가 낡았으면 아래
            숫자도 그만큼 낡았다. */}
        <Freshness at={robot.runtime_reported_at} {...HEARTBEAT_FRESHNESS} />
      </div>

      {topics.length === 0 ? (
        <p className="empty">
          이 게이트웨이는 토픽을 감시하지 않습니다. 토픽이 죽은 것이 아니라
          감시가 아직 안 붙은 상태입니다.
        </p>
      ) : (
        <ul className="topic-watch__list">
          {topics.map((topic) => (
            <li key={topic.topic} className={`topic-watch__row topic-watch__row--${topic.state}`}>
              <code className="topic-watch__name">{topic.topic}</code>
              <span className={`topic-watch__state ${STATE_TONE[topic.state]}`}>
                {STATE_LABEL[topic.state]}
              </span>
              <span className="topic-watch__rate mono">{formatRate(topic)}</span>
              <span className="topic-watch__age mono">{formatAge(topic.age_sec)}</span>
              {/* 왜 문제인지는 상태가 나쁠 때만 보여준다. 항상 띄우면 정상
                  로봇의 카드가 설명문으로 덮인다. */}
              {(topic.state === 'stale' || topic.state === 'slow') && topic.why && (
                <span className="topic-watch__why">{topic.why}</span>
              )}
            </li>
          ))}
        </ul>
      )}

      <small>
        유닛이 active 여도 데이터가 안 흐를 수 있습니다. 상시 발행 토픽이 끊기면
        타임라인에 <code>robot.topic_stale</code> 이 남습니다.
      </small>
    </section>
  )
}
