-- 로봇에서 실제로 돌고 있는 것 — 실행 코드 버전과 노드 목록.
--
-- 장애 대응에서 가장 많은 시간을 잡아먹은 건 "이 로봇에서 지금 어떤 코드가
-- 돌고 있는가" 를 알 수 없다는 것이었다. 로봇마다 다른 워크스페이스가 섞여
-- 있었고, 같은 노드가 두 번 떠서 I2C 측정값을 조용히 오염시켰다.
--
-- ## 왜 최신 한 행만 두는가
--
-- 추이가 필요 없다. 지금 무엇이 도는지가 질문이지 어제 무엇이 돌았는지가
-- 아니다. 바뀐 이력이 필요하면 events 에 남기면 된다 — 그쪽이 이미
-- append-only 이고 시각 질의가 붙어 있다.
--
-- ## 왜 JSONB 인가
--
-- 이번이 첫 구현이라 어떤 필드가 실제로 쓸모 있을지 모른다. 프로세스별로
-- 무엇을 담을지, 노드 매칭을 어디까지 믿을지가 운영하면서 바뀐다. 컬럼으로
-- 박아두면 필드 하나 추가할 때마다 마이그레이션·게이트웨이·백엔드·프론트를
-- 한꺼번에 배포해야 한다.
--
-- 대신 안정화된 필드는 컬럼으로 승격한다. 승격 기준을 정하지 않으면 JSONB
-- 가 영원히 JSONB 로 남는다 — 화면이 3주 이상 실제로 읽는 필드는 옮긴다.
--
-- ## heartbeat 의 신규 필드는 왜 여기 없는가
--
-- cpu_total_pct, queue_pending 같은 값은 3~5초마다 덮어쓰는 실시간 값이다.
-- 003 이 "화면 표시용 실시간 상태는 DB 에 저장하지 않는다" 로 정해둔 것과
-- 성격이 같아 백엔드 메모리(robot_runtime)에만 둔다. 경계를 흐리면 "이
-- 숫자가 언제 것인가" 를 나중에 아무도 답하지 못한다.

BEGIN;

CREATE TABLE robot_inventory (
    robot_id       VARCHAR(20) PRIMARY KEY REFERENCES robots(robot_id),

    -- 게이트웨이가 계산한 내용 지문. heartbeat 에 이것만 실어 보내고,
    -- 서버가 아는 값과 다를 때만 본문을 다시 요구한다(need_inventory).
    -- CPU 처럼 매번 바뀌는 값은 해시 계산에서 빠진다.
    inventory_hash TEXT NOT NULL,

    -- node_graph / processes / workspaces / ros_domain_id
    payload        JSONB NOT NULL,

    -- 로봇 시계가 아니라 서버 수신 시각이다. 이 값으로 신선도를 판정하므로
    -- 전송 지연을 숨기지 않아야 한다 (battery 와 같은 규칙).
    reported_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
