-- 안내 세션, 방문 단계, 로봇 마스터, 이벤트 로그
--
-- 설계 원칙: 파생 가능한 값은 컬럼으로 저장하지 않는다.
--   - "현재 단계"는 session_steps에서 계산 (session_current_step 뷰)
--   - "중단됨"은 세션의 ended_at에서 계산 (session_step_status 뷰)

BEGIN;

-- ============================================================
-- 1. robots — 로봇 마스터
-- ============================================================
-- 주행 로봇 2대(Pinky) + 고정 로봇팔 스테이션 2대(OMX).
-- OMX는 리더/팔로워 텔레옵 쌍이므로 스테이션 단위로 1행씩 등록한다.
-- 리더 암은 입력 장치이므로 등록 대상이 아니다.
CREATE TABLE robots (
    robot_id      VARCHAR(20) PRIMARY KEY,           -- 'pinky-01', 'omx-01'
    robot_type    VARCHAR(20) NOT NULL CHECK (robot_type IN ('mobile', 'arm')),
    display_name  VARCHAR(50) NOT NULL,
    -- ROS 2 도메인 ID. 식별자가 아니라 현재 네트워크 구성값이므로 PK로 쓰지 않는다.
    -- OMX는 ROS가 아닌 LeRobot 시리얼 직결이므로 NULL.
    domain_id     SMALLINT,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    CHECK (robot_type <> 'mobile' OR domain_id IS NOT NULL)
);

-- ============================================================
-- 2. guidance_sessions — 안내 1회분
-- ============================================================
-- 안내 도중 로봇 교체는 없으므로 robot_id를 세션에 직접 둔다.
CREATE TABLE guidance_sessions (
    session_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id    VARCHAR(20) NOT NULL REFERENCES patients(patient_id),
    robot_id      VARCHAR(20) NOT NULL REFERENCES robots(robot_id),
    -- 그때의 증상 스냅샷. patients.condition_id는 '현재' 값이라 재방문 시 덮어써진다.
    condition_id  BIGINT NOT NULL REFERENCES conditions(condition_id),
    -- 환자가 착용하는 ArUco 마커. 물리적으로 재사용되므로 세션에 묶는다.
    -- 장소 마커는 사용하지 않으므로 대역 분리 없이 dictionary 범위만 강제한다.
    marker_id     INTEGER CHECK (marker_id BETWEEN 0 AND 49),
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at      TIMESTAMPTZ,
    end_reason    VARCHAR(20) CHECK (
                      end_reason IN ('completed', 'aborted', 'battery', 'patient_lost')
                  ),
    CHECK (ended_at IS NULL OR ended_at >= started_at),
    CHECK ((ended_at IS NULL) = (end_reason IS NULL))
);

-- 동시 배정 방어. 활성 세션(ended_at IS NULL)에만 적용해야 재방문이 막히지 않는다.
CREATE UNIQUE INDEX uq_active_session_robot
    ON guidance_sessions (robot_id)   WHERE ended_at IS NULL;
CREATE UNIQUE INDEX uq_active_session_patient
    ON guidance_sessions (patient_id) WHERE ended_at IS NULL;
CREATE UNIQUE INDEX uq_active_session_marker
    ON guidance_sessions (marker_id)  WHERE ended_at IS NULL AND marker_id IS NOT NULL;

CREATE INDEX idx_sessions_patient_started
    ON guidance_sessions (patient_id, started_at DESC);

-- ============================================================
-- 3. session_steps — 세션별 방문 단계
-- ============================================================
-- 세션 시작 시 examination_steps를 복사해 전부 생성한다(arrived_at NULL).
-- 마스터가 나중에 바뀌어도 과거 세션의 일정이 보존되어야 하므로 스냅샷이다.
CREATE TABLE session_steps (
    session_step_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id       BIGINT NOT NULL REFERENCES guidance_sessions(session_id) ON DELETE CASCADE,
    step_order       SMALLINT NOT NULL CHECK (step_order > 0),
    -- 방문 장소 이름 스냅샷. locations 테이블 확정 후 location_id FK를 추가한다.
    visit_name       VARCHAR(100) NOT NULL,
    arrived_at       TIMESTAMPTZ,          -- Nav2 goal succeeded 시점
    completed_at     TIMESTAMPTZ,          -- 환자가 QR 카드를 스캔한 시점
    -- 자동 감지가 실제로 작동하는지 측정하는 유일한 수단.
    -- manual 비율이 높아지면 QR 인식이 고장난 것이다.
    completed_source VARCHAR(10) CHECK (completed_source IN ('qr', 'manual')),
    UNIQUE (session_id, step_order),
    CHECK (completed_at IS NULL OR arrived_at IS NOT NULL),
    CHECK (completed_at IS NULL OR completed_at >= arrived_at),
    CHECK ((completed_at IS NULL) = (completed_source IS NULL))
);

-- ============================================================
-- 4. robot_battery_log — 배터리 추이 (2분 주기)
-- ============================================================
-- 화면 표시용 실시간 상태는 DB에 저장하지 않는다(백엔드 메모리).
-- 임계값 진입 순간은 여기가 아니라 events에 기록한다.
CREATE TABLE robot_battery_log (
    robot_id        VARCHAR(20) NOT NULL REFERENCES robots(robot_id),
    recorded_at     TIMESTAMPTZ NOT NULL,
    battery_percent SMALLINT NOT NULL CHECK (battery_percent BETWEEN 0 AND 100),
    PRIMARY KEY (robot_id, recorded_at)
);

-- ============================================================
-- 5. events — append-only 이벤트 로그
-- ============================================================
-- 의료진/엔지니어 대시보드가 같은 소스를 본다.
CREATE TABLE events (
    -- 로봇이 생성한다. 네트워크 두절 후 재전송 시
    -- ON CONFLICT (event_id) DO NOTHING 으로 멱등해진다.
    event_id     UUID PRIMARY KEY,
    robot_id     VARCHAR(20) REFERENCES robots(robot_id),
    session_id   BIGINT REFERENCES guidance_sessions(session_id),
    occurred_at  TIMESTAMPTZ NOT NULL,                 -- 로봇 시계
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),   -- 서버 시계
    level        VARCHAR(10) NOT NULL CHECK (level IN ('info', 'warning', 'error')),
    event_code   VARCHAR(50) NOT NULL,
    source_node  VARCHAR(100),                         -- 노드별 필터용
    payload      JSONB
);

-- occurred_at과 received_at의 차이가 시계 어긋남 탐지기 역할을 한다.
-- 정상이면 수백 ms, 초 단위로 벌어지면 시간 동기화(chrony) 점검이 필요하다.

CREATE INDEX idx_events_occurred   ON events (occurred_at DESC);
CREATE INDEX idx_events_robot_time ON events (robot_id, occurred_at DESC);
CREATE INDEX idx_events_session    ON events (session_id) WHERE session_id IS NOT NULL;

-- ============================================================
-- 6. 파생 뷰
-- ============================================================

-- 현재 진행 중인 단계. 끝난 세션에는 '현재'가 없으므로 활성 세션만 대상으로 한다.
CREATE VIEW session_current_step AS
SELECT DISTINCT ON (ss.session_id)
       ss.session_id,
       ss.step_order,
       ss.visit_name,
       ss.arrived_at
FROM session_steps ss
JOIN guidance_sessions gs USING (session_id)
WHERE ss.completed_at IS NULL
  AND gs.ended_at IS NULL
ORDER BY ss.session_id, ss.step_order;

-- 단계별 상태. status 컬럼을 저장하지 않고 여기서 계산한다.
-- CASE 순서가 곧 의미다:
--   완료는 되돌릴 수 없고, 세션 종료가 진행 상태를 이긴다.
--   arrived_at 검사를 ended_at보다 위에 두면 도착 후 중단된 단계가
--   영원히 in_progress로 남는다.
CREATE VIEW session_step_status AS
SELECT ss.*,
       CASE
         WHEN ss.completed_at IS NOT NULL THEN 'completed'
         WHEN gs.ended_at     IS NOT NULL THEN 'aborted'
         WHEN ss.arrived_at   IS NOT NULL THEN 'in_progress'
         ELSE 'pending'
       END AS status
FROM session_steps ss
JOIN guidance_sessions gs USING (session_id);

COMMIT;
