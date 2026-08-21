-- 조제 job 을 안내 세션·환자·조제 스테이션에 잇는다 (로드맵 item 4).
--
-- ## 왜 별도 테이블인가
--
-- 조제는 events 로 라이프사이클을 남기지만(manipulator.cycle_*), 그건 append-only
-- 로그라 "지금 이 세션에 어떤 조제가 걸려 있나" 를 물을 상태가 없다. 안내 로봇이
-- 약국에 도착(pharmacy.arrived)하면 백엔드가 그 세션·환자로 조제를 시작하고,
-- 조제가 끝나면 그 세션에 대해 pharmacy.dispense_completed 를 발행해야 한다 —
-- 그 연결을 여기 한 행으로 들고 있는다.
--
-- ## dispense_id 는 백엔드가 만든다
--
-- events.payload 의 dispense_id 와 같은 값이다(manipulator.cycle_*). 조제 지시의
-- 식별자이므로 제어 명령의 order_id(UUID) 와 겹치지 않게 별도로 둔다
-- (config/event_codes.yaml 조제 절 참조).
--
-- ## session_id·patient_id 는 NULL 을 허용한다
--
-- 화면에서 손으로 시작한 독립 조제(세션 없이 도는 정상 경로, §6.2)도 원하면 이
-- 표에 남길 수 있어야 한다. 세션 연결 조제만 두 값이 채워진다.

BEGIN;

CREATE TABLE dispense_jobs (
    dispense_id   VARCHAR(64) PRIMARY KEY,            -- 백엔드 생성, events 와 동일
    session_id    BIGINT REFERENCES guidance_sessions(session_id) ON DELETE SET NULL,
    patient_id    VARCHAR(20) REFERENCES patients(patient_id),
    -- 어느 OMX 박스가 조제했나. 조제=omx-01, 포장=omx-02.
    omx_robot_id  VARCHAR(20) NOT NULL REFERENCES robots(robot_id),
    status        VARCHAR(20) NOT NULL DEFAULT 'requested'
                    CHECK (status IN ('requested', 'completed', 'aborted')),
    requested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at  TIMESTAMPTZ,
    -- 끝난 job 만 completed_at 이 있고, 그 시각은 시작보다 뒤다.
    CHECK ((status = 'requested') = (completed_at IS NULL)),
    CHECK (completed_at IS NULL OR completed_at >= requested_at)
);

-- 세션별 타임라인 조회용.
CREATE INDEX idx_dispense_jobs_session
    ON dispense_jobs (session_id) WHERE session_id IS NOT NULL;

-- 한 세션에 진행 중인 조제는 하나뿐이다. 도착 이벤트가 재전송돼도(멱등) 조제가
-- 두 번 시작되지 않게 막는다 — guidance_sessions 의 활성 세션 유니크와 같은 결.
CREATE UNIQUE INDEX uq_active_dispense_session
    ON dispense_jobs (session_id)
    WHERE status = 'requested' AND session_id IS NOT NULL;

COMMIT;
