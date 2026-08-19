-- 제어 명령 감사 로그 — 누가 로봇에 손을 댔는가.
--
-- monitoring-spec.md §7.2 의 감사 로그이자 §1.1 판정의 선행 조건이다.
-- 병원 도메인에서 "누가 비상정지를 눌렀나" 는 기능이 아니라 요건이고,
-- SLO 의 "수동 개입 없음" 판정도 이 표가 없으면 성립하지 않는다.
--
-- ## 왜 events 에 안 넣는가
--
-- events 는 로봇이 주체다. event_id 를 로봇이 만들고(재전송 멱등의 근거),
-- 코드 정본이 config/event_codes.yaml 이며, occurred_at 과 received_at 의
-- 차이가 시계 어긋남 탐지기 역할을 한다. 서버가 발행 주체인 기록을 여기
-- 섞으면 그 세 가지 규칙이 한꺼번에 흐려진다.
--
-- ## 왜 orders 를 통째로 영속화하지 않는가
--
-- app/orders.py 는 로봇이 ack 하면 슬롯에서 지운다. 대기 중인 명령은 휘발성
-- 상태이고(원칙 2), 남아야 하는 것은 "명령이 있었다" 가 아니라 "사람이
-- 실행했다" 다. 그래서 대기열의 사본이 아니라 발행 이력만 적재한다.
--
-- ## actor 는 인증이 아니다
--
-- 이 시스템에는 인증 계층이 없다(deploy/nginx.conf 에 auth_basic 이 없다).
-- X-Actor 는 대시보드가 스스로 적어 보내는 이름이고 위조를 막을 방법이
-- 없다. 그 한계를 컬럼 이름이 말하게 한다 — actor_source 가 'header' 면
-- "자기신고" 라는 뜻이지 "확인됨" 이라는 뜻이 아니다. 나중에 인증이
-- 붙으면 'session' 같은 값이 추가되고, 과거 행은 여전히 자기신고로 남아
-- 신뢰도가 섞이지 않는다.
--
-- ## 누락은 거부하지 않는다
--
-- 헤더가 없다고 422 를 돌려주면 감사 문제가 가용성 문제로 바뀐다. 프론트가
-- 헤더를 빠뜨리는 버그 하나로 조작자가 비상정지를 못 누르게 된다. 게다가
-- 거부는 정직한 누락만 막고 위조는 못 막으므로 지키려던 것을 지키지도
-- 못한다. 받아서 남기고 드러낸다 — ingest.py 가 미등록 이벤트 코드를 다루는
-- 방식과 같은 원칙이다(§12 "적재 후 경고 발행").
--
-- 중요한 건 이 완화가 무엇을 깎는지다. actor 가 비어도 **행은 남는다.**
-- §1.1 은 "사람이 손댔는가" 만 묻지 "누가" 를 묻지 않으므로 SLO 판정은
-- 온전하다. 깎이는 것은 귀속(attribution)뿐이고, 그건 익명 비율을 화면에
-- 띄워 회수한다.

BEGIN;

CREATE TABLE control_audit (
    audit_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- 서버 시각 하나만 둔다. 이 기록의 주체가 서버라서 events 처럼 로봇
    -- 시계와 대조할 대상이 없다.
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    robot_id     VARCHAR(20) NOT NULL REFERENCES robots(robot_id),

    -- 발행 '시점' 의 활성 세션 스냅샷. 나중에 시간창으로 조인하면 세션
    -- 경계가 흐려지고, 종료 직후의 정리 명령까지 그 세션의 개입으로 끌려온다.
    -- 세션 밖(대기 중) 개입은 NULL 로 남아 SLO 집계에서 자연히 빠진다.
    --
    -- ON DELETE 를 안 붙인 것이 의도다. 감사 행이 세션 삭제를 막아야 한다.
    -- session_steps 는 세션에 종속된 사본이라 CASCADE 지만, 이쪽은 세션이
    -- 사라져도 남아야 하는 독립된 사실이다.
    session_id   BIGINT REFERENCES guidance_sessions(session_id),

    -- OrderIn.command 값 또는 teleop_attach / teleop_detach.
    --
    -- CHECK 로 열거하지 않는다. 명령은 계속 늘어나는 어휘이고, 그때마다
    -- 마이그레이션을 물고 배포해야 하면 감사 대상에서 빠뜨리는 쪽이 쉬워진다.
    -- events.event_code 가 VARCHAR 에 CHECK 없이 앱 계층(registry)에서
    -- 검증되는 것과 같은 이유다. 여기서는 schemas.py 의 Literal 이 그 역할을
    -- 한다 — 정본을 두 곳에 적지 않는다(원칙 1).
    action       VARCHAR(30) NOT NULL,

    -- OrderIn.argument 와 같은 상한. teleop 행은 NULL.
    argument     VARCHAR(200),

    -- 자기신고 이름. NULL 이면 아무것도 안 왔다는 뜻이고, 그 자체가 집계
    -- 대상이다. 'unknown' 같은 문자열 표식을 쓰지 않는 이유는 조작자가
    -- 실제로 그렇게 적어 보낼 수 있어서다 — 그러면 익명 행과 구분이 안 된다.
    actor        VARCHAR(50),

    actor_source VARCHAR(10) NOT NULL
                 CHECK (actor_source IN ('header', 'absent')),

    -- 로봇 ack 와 대조할 때만 쓴다. orders 는 테이블이 아니라 메모리라
    -- FK 를 걸 대상이 없다. teleop 행에는 명령이 없으므로 NULL.
    order_id     UUID,

    -- 003 의 (completed_at, completed_source) 와 같은 짝 제약이다.
    -- 둘이 갈라지면 "이름이 비었다" 와 "헤더가 안 왔다" 를 구분하지 못해
    -- 익명 비율 자체를 믿을 수 없게 된다.
    CHECK ((actor IS NULL) = (actor_source = 'absent'))
);

-- SLO 판정이 세션마다 EXISTS 로 때린다. events 의 같은 목적 인덱스와
-- 같은 형태로 만든다 — 세션 밖 개입(NULL)은 이 질의의 대상이 아니다.
CREATE INDEX idx_control_audit_session
    ON control_audit (session_id) WHERE session_id IS NOT NULL;

-- fleet 탭의 "최근 수동 개입" 목록과 익명 비율 집계가 쓴다.
CREATE INDEX idx_control_audit_occurred
    ON control_audit (occurred_at DESC);

-- 로봇별 조회는 아직 화면이 없다. 인덱스는 쓰는 질의가 생길 때 추가한다 —
-- 미리 깔면 쓰이지 않는 채로 INSERT 비용만 낸다.

COMMIT;
