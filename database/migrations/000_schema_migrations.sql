-- 적용된 마이그레이션 이력.
--
-- ## 왜 이제서야 필요한가
--
-- 여기까지는 "빈 볼륨에 001부터 끝까지 한 번 돌린다" 로 충분했다. 운영 DB 를
-- 고치는 마이그레이션이 없었기 때문이다. 010 이 그 전제를 깼다 — 이미 데이터가
-- 들어 있는 DB 의 robot_type 을 'arm' 에서 'manipulator' 로 옮겨야 하는데,
-- init-db.sh 는 볼륨 최초 생성 시에만 돌아서 파일을 만들어도 실행되지 않았다.
--
-- ## 왜 파일이 스스로 기록하지 않는가
--
-- 각 마이그레이션 끝에 INSERT 를 붙이는 방법도 있지만, 그러면 마이그레이션이
-- 러너의 사정을 알아야 한다. 넣는 걸 잊은 파일 하나가 매번 다시 도는 조용한
-- 버그가 된다. 기록은 러너(init-db.sh)가 성공 후에 한다.
--
-- ## 기존 DB 부트스트랩
--
-- 이 테이블을 처음 도입하는 시점에 운영 DB 는 이미 001~009 를 적용한 상태다.
-- 표시해 두지 않으면 러너가 001부터 다시 돌려 CREATE TABLE 에서 죽는다.
-- 009 가 만드는 robot_inventory 의 존재를 "001~009 적용됨" 의 근거로 쓴다.
-- 빈 볼륨에서는 이 파일이 001보다 먼저 도므로 robot_inventory 가 없고,
-- 따라서 아무것도 표시되지 않아 전체가 정상적으로 실행된다.

BEGIN;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO schema_migrations (version)
SELECT version
FROM (VALUES
    ('001_initial_schema'),
    ('002_drop_patient_age'),
    ('003_sessions_and_events'),
    ('004_battery_voltage'),
    ('005_patient_photos'),
    ('006_session_failure_reasons'),
    ('007_fire_session_end_reason'),
    ('008_events_unknown_code_index'),
    ('009_robot_inventory')
) AS applied(version)
WHERE to_regclass('public.robot_inventory') IS NOT NULL
ON CONFLICT (version) DO NOTHING;

COMMIT;
