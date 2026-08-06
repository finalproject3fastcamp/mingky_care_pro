-- 로봇 마스터.
--
-- guidance_sessions.robot_id 가 이 테이블을 참조하므로, 이 시드가 없으면
-- POST /qr/scan 이 외래키 위반으로 실패한다.
--
-- domain_id 는 ROS 2 도메인이며 각 장비의 운영 설정과 맞춘다
-- (pinky1=21, pinky2=20). IP 끝자리에서 유도하는 값이 아니다.
-- 식별자가 아니라 현재 네트워크 구성값이라 별도 컬럼에 둔다.
-- OMX 는 ROS 가 아니라 LeRobot 으로 직접 제어하므로 NULL 이다.
--
-- OMX 는 리더/팔로워 한 쌍이 스테이션 하나다. 리더 암은 사람이 잡는
-- 입력 장치이므로 등록 대상이 아니다.

BEGIN;

INSERT INTO robots (robot_id, robot_type, display_name, domain_id, is_active)
VALUES
    ('pinky-01', 'mobile', '핑키 1호',        21,   TRUE),
    ('pinky-02', 'mobile', '핑키 2호',        20,   TRUE),
    ('omx-01',   'arm',    '조제 스테이션 1', NULL, TRUE),
    ('omx-02',   'arm',    '조제 스테이션 2', NULL, TRUE)
ON CONFLICT (robot_id) DO UPDATE SET
    robot_type   = EXCLUDED.robot_type,
    display_name = EXCLUDED.display_name,
    domain_id    = EXCLUDED.domain_id,
    is_active    = EXCLUDED.is_active;

COMMIT;
