-- 실제로 몇 번째로 방문했는가 — 계획 순서와 따로 둔다.
--
-- ## 왜 필요해졌나
--
-- 핑키 2대를 동시에 돌리면 **같은 검사실이 겹친다.** 시드의 세 증상이 전부
-- 1단계가 X-ray 라(001_initial_data.sql), 환자 둘을 시차를 두고 받아도
-- 앞 환자가 X-ray 를 마치기 전에 뒤 환자가 도착하면 그대로 막힌다.
--
-- 답은 기다리는 것이 아니라 **순서를 바꾸는 것**이다. "X-ray 가 차 있으니
-- CT 를 먼저." 그러면 방문 순서가 계획과 달라진다.
--
-- ## 왜 step_order 를 고치지 않는가
--
-- 003 이 session_steps 를 examination_steps 의 **스냅샷**으로 만든 이유가
-- 있다 — 마스터가 나중에 바뀌어도 과거 안내의 일정이 보존되어야 한다.
-- step_order 를 실행 중에 갈아치우면 "이 환자에게 원래 무엇을 하려 했는가"
-- 를 답할 수 없게 되고, 그건 진료 기록으로서 잃으면 안 되는 사실이다.
--
-- 그래서 계획은 step_order 에 그대로 두고 실제만 여기에 적는다. 둘이 다르면
-- 다른 것이 사실이다.
--
-- ## 왜 파생이 아닌가
--
-- 003 은 "파생 가능한 값은 컬럼으로 저장하지 않는다" 를 원칙으로 세웠다.
-- 이 값은 파생되지 않는다 — 이동 중에는 arrived_at 도 completed_at 도 없어서,
-- 타임스탬프만으로는 "지금 어디로 가는 중인가" 를 복원할 수 없다. 도착한
-- 뒤에야 알 수 있는 값으로 이동 중 화면을 그릴 수는 없다.
--
-- ## 뷰를 바꾸는 이유
--
-- 기존 session_current_step 은 '완료 안 된 것 중 step_order 가 가장 작은 것'
-- 이었다. 순서를 바꾸면 로봇은 CT 에 있는데 화면은 X-ray 가 현재라고 말한다.
-- 실제로 방문에 들어간 것 중 가장 최근 것이 현재다.

BEGIN;

ALTER TABLE session_steps
    ADD COLUMN visit_seq SMALLINT CHECK (visit_seq > 0);

-- 한 세션에서 같은 순번이 둘일 수 없다. 아직 안 간 단계는 NULL 이라 여럿이다.
CREATE UNIQUE INDEX uq_session_steps_visit_seq
    ON session_steps (session_id, visit_seq)
    WHERE visit_seq IS NOT NULL;

-- 이미 있는 세션은 계획대로 방문했다. 순서를 바꾸는 기능이 없었으므로 사실이다.
UPDATE session_steps SET visit_seq = step_order WHERE arrived_at IS NOT NULL;

-- 현재 단계 — '방문을 시작한 것 중 아직 안 끝난 가장 최근 것'.
--
-- visit_seq 가 없는 세션(아직 아무 데도 안 간 세션)에서는 계획의 첫 단계가
-- 현재다. 안 그러면 안내를 시작하기 전 화면에 현재 단계가 비어 보인다.
CREATE OR REPLACE VIEW session_current_step AS
SELECT DISTINCT ON (ss.session_id)
       ss.session_id,
       ss.step_order,
       ss.visit_name,
       ss.arrived_at
FROM session_steps ss
JOIN guidance_sessions gs USING (session_id)
WHERE ss.completed_at IS NULL
  AND gs.ended_at IS NULL
ORDER BY ss.session_id,
         -- 방문에 들어간 것을 먼저 본다. 그중에서는 가장 최근 순번이 현재고,
         -- 하나도 없으면 계획의 첫 단계로 떨어진다.
         (ss.visit_seq IS NULL),
         ss.visit_seq DESC,
         ss.step_order;

COMMIT;
