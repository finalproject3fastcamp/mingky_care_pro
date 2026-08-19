-- 화재 대피가 진행 중인 환자 안내를 종료할 수 있도록 종료 사유를 추가한다.

BEGIN;

ALTER TABLE guidance_sessions
    DROP CONSTRAINT IF EXISTS guidance_sessions_end_reason_check;

ALTER TABLE guidance_sessions
    ADD CONSTRAINT guidance_sessions_end_reason_check CHECK (
        end_reason IN (
            'completed', 'aborted', 'battery', 'patient_lost',
            'robot_offline', 'system_failure', 'fire'
        )
    );

COMMIT;
