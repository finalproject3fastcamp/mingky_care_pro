-- 서보 온도·전류 추이 — 팔에만 있는 예지보전 신호.
--
-- monitoring-spec.md §4.4 · 로드맵 11.
--
-- Dynamixel 은 온도·전류·전압·하드웨어 에러 비트를 스스로 보고한다. mobile
-- 에는 대응물이 없는 신호다 — 특정 조인트 온도가 회차마다 올라가면 그리퍼
-- 마모나 과부하 자세이고, 그건 고장 **전에** 보인다.
--
-- ## 왜 events 가 아닌가
--
-- events 는 불연속 사건이다(§5). 1분마다 찍히는 온도를 거기 넣으면 타임라인
-- 필터가 쓸모없어진다 — 배터리 추이를 robot_battery_log 로 뺀 것과 같은
-- 이유이고, 정본 주석에도 그렇게 적혀 있다("정기 샘플이 이벤트에 섞이면").
--
-- events 에 남는 것은 판정 결과뿐이다 — manipulator.servo_fault(하드웨어
-- 에러 비트)와 manipulator.servo_overheat(임계 통과).
--
-- ## 왜 인메모리가 아닌가
--
-- 원칙 2 는 "3~5초마다 덮어쓰는 값" 을 말한다. 여기서 필요한 것은 현재값이
-- 아니라 **추이**다. 메모리에 두면 서버 재시작마다 사라져서 "요즘 나빠지고
-- 있나" 를 영영 못 본다. §5 표의 '메트릭' 평면이 이 표로 처음 채워진다.
--
-- ## 왜 조인트별 행인가
--
-- 판정이 조인트 단위다("shoulder_lift 만 회차마다 오른다"). JSONB 한 칸에
-- 전체 스냅샷을 넣으면 추이 질의마다 경로 추출이 붙고, 조인트별 인덱스를
-- 걸 수 없다. 화면에 나가는 응답 쪽은 그대로 detail 한 칸이다(§7.3).
--
-- ## 보존
--
-- §5 의 초안은 90일이다(§13 미결정). 지금은 자동 정리를 걸지 않는다 —
-- 팔 2대 × 5조인트 × 1분이면 하루 14,400행이라 당장 문제가 되지 않고,
-- 삭제 주기를 실측 없이 코드에 박으면 그게 정본이 되어 버린다.

BEGIN;

CREATE TABLE robot_servo_log (
    servo_log_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    robot_id     VARCHAR(20) NOT NULL REFERENCES robots(robot_id),

    -- 로봇 시계가 아니라 서버 수신 시각. robot_battery_log 와 같은 판단이다 —
    -- 이 값으로 신선도를 판정하므로 전송 지연을 숨기지 않아야 한다.
    recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Dynamixel 조인트 이름. ID(정수)가 아니라 이름을 쓴다 — ID 는 배선을
    -- 바꾸면 달라지지만 사람이 조사할 때 묻는 것은 "어깨가 뜨겁다" 다.
    joint        VARCHAR(30) NOT NULL,

    -- XM430 은 1℃ 단위 정수로 보고하지만 REAL 로 받는다. 다른 모델이나
    -- 평활 필터가 소수를 낼 수 있고, 그때 스키마를 다시 고치지 않는다.
    -- 상한을 100 으로 둔 것은 명백한 오독(0xFF 등)을 거르기 위해서다.
    temp_c       REAL CHECK (temp_c IS NULL OR temp_c BETWEEN -20 AND 150),

    -- 부호가 있다. 방향에 따라 음수이고, 절대값이 부하다.
    current_ma   REAL CHECK (current_ma IS NULL OR abs(current_ma) <= 10000),

    voltage_v    REAL CHECK (voltage_v IS NULL OR voltage_v BETWEEN 0 AND 30),

    -- Dynamixel Hardware Error Status 비트필드. 0 은 정상이고, NULL 은
    -- '안 읽었다' 이다. 둘을 같은 값으로 저장하면 통신이 죽은 서보가
    -- 정상으로 집계된다.
    hardware_error SMALLINT CHECK (hardware_error IS NULL
                                   OR hardware_error BETWEEN 0 AND 255),

    -- 전부 비어 있는 행은 의미가 없다. robot_battery_log 와 같은 가드다.
    CONSTRAINT robot_servo_log_has_reading
        CHECK (temp_c IS NOT NULL OR current_ma IS NOT NULL
               OR voltage_v IS NOT NULL OR hardware_error IS NOT NULL)
);

-- 두 질의가 이 인덱스를 탄다.
--   최신값   DISTINCT ON (robot_id, joint) ... ORDER BY ... recorded_at DESC
--   추이     WHERE robot_id = $1 AND recorded_at >= now() - interval
CREATE INDEX idx_robot_servo_log_robot_joint_time
    ON robot_servo_log (robot_id, joint, recorded_at DESC);

COMMIT;
