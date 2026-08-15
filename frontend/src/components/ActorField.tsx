/**
 * 조작자 이름 입력. 헤더 오른쪽에 상시 노출된다.
 *
 * 설정 화면 안에 숨기지 않는 이유는, 비어 있다는 사실이 보여야 하기 때문이다.
 * 감사 로그가 익명으로 쌓이는 것을 막는 유일한 장치가 "이 칸이 비어 있는 게
 * 눈에 띈다" 이다. 막지 않고 드러내는 쪽을 택했으므로(lib/actor.ts) 드러나는
 * 자리에 둬야 한다.
 *
 * 입력은 어떤 명령도 막지 않는다. 비워도 조작은 그대로 되고 서버가 익명으로
 * 기록한다.
 */

import { useState } from 'react'

import { MAX_ACTOR_LENGTH, getActor, setActor } from '../lib/actor'

export function ActorField() {
  const [name, setName] = useState(getActor)

  return (
    <label className="app-actor" title="제어 명령에 남길 이름. 감사 로그에 기록된다.">
      <span className="app-actor__label">조작자</span>
      <input
        className="app-actor__input"
        value={name}
        maxLength={MAX_ACTOR_LENGTH}
        placeholder="이름 미입력"
        aria-label="조작자 이름"
        onChange={(event) => setName(event.target.value)}
        // 저장은 포커스가 빠질 때 한 번만 한다. 타이핑마다 쓰면 '정' 처럼
        // 반쯤 적힌 이름이 그 사이 눌린 명령에 붙는다.
        onBlur={(event) => setName(setActor(event.target.value))}
      />
      {!name && <span className="app-actor__warn" aria-hidden="true">익명</span>}
    </label>
  )
}
