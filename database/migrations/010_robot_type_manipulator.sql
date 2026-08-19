-- robot_type 의 'arm' 을 'manipulator' 로 바꾼다.
--
-- ## 왜
--
-- 저장소에서 arm 이 두 뜻으로 쓰였다.
--
--   POST /robots/{id}/arm, arming.py, activation.*  →  의료진이 로봇을 활성화
--   robot_type = 'arm'                              →  로봇팔(OMX)
--
-- 팔에 활성화를 붙이는 순간 "arm robot arming" 같은 읽을 수 없는 이름이 나온다.
-- 활성화 쪽을 assignment 로 바꾸는 선택지도 있었으나 백엔드·프론트 전역에
-- 걸쳐 있어 영향 범위가 훨씬 넓다. robot_type 쪽이 시드 2행과 CHECK 하나다.
--
-- ## 왜 제약 이름을 찾아서 지우는가
--
-- 003 이 인라인 컬럼 CHECK 로 선언해서 이름을 PostgreSQL 이 붙였다. 규칙은
-- <테이블>_<컬럼>_check 지만, 이름에 기대는 대신 카탈로그에서 확인하고 지운다.
--
-- 단 컬럼 이름으로 찾으면 안 된다. robots 에는 CHECK 가 둘이고 **둘 다** 정의에
-- robot_type 이 들어간다.
--
--   robots_check             CHECK (robot_type <> 'mobile' OR domain_id IS NOT NULL)
--   robots_robot_type_check  CHECK (robot_type IN ('mobile', 'arm'))
--
-- LIKE '%robot_type%' 는 둘 다 잡고, SELECT ... INTO 는 ORDER BY 가 없으면
-- 아무거나 하나를 에러 없이 가져간다. 앞엣것을 집으면 domain_id 무결성 제약이
-- 사라지고, 남아 있는 뒤엣것 때문에 UPDATE 가 거부된다. 'arm' 을 열거하는
-- 제약만 고른다 — 그건 하나뿐이다.
--
-- ## 순서
--
-- CHECK 가 'manipulator' 를 막고 있으므로 제약을 먼저 떼야 UPDATE 가 통과한다.
-- 003 은 고치지 않았다. 이미 적용된 마이그레이션은 그 DB 가 실제로 무엇을
-- 거쳤는지에 대한 기록이고, 004~009 는 003 이 'arm' 인 DB 를 전제로 쓰여 있다.

BEGIN;

DO $$
DECLARE
    constraint_name text;
BEGIN
    SELECT conname INTO constraint_name
    FROM pg_constraint
    WHERE conrelid = 'robots'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) LIKE '%''arm''%';

    -- 이미 옮긴 DB. UPDATE 도 ADD 도 할 일이 없다.
    IF constraint_name IS NULL THEN
        RETURN;
    END IF;

    EXECUTE format('ALTER TABLE robots DROP CONSTRAINT %I', constraint_name);

    UPDATE robots SET robot_type = 'manipulator' WHERE robot_type = 'arm';

    -- 원래 이름을 그대로 다시 쓴다. 이름이 바뀌면 이 파일을 이미 적용한 DB 와
    -- 나중에 만든 DB 의 제약 이름이 갈린다.
    EXECUTE format(
        'ALTER TABLE robots ADD CONSTRAINT %I CHECK (robot_type IN (''mobile'', ''manipulator''))',
        constraint_name);
END $$;

COMMIT;
