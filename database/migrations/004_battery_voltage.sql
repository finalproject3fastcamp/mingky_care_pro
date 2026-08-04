-- 배터리 원측정값(전압) 저장.
--
-- pinkylib 의 퍼센트는 전압을 선형 변환한 파생값이다.
--     percent = (V - 6.8) / (7.6 - 6.8) * 100
-- 리튬이온 방전 곡선은 선형이 아니므로 이 식은 나중에 실측으로 보정될 가능성이 높다.
-- 퍼센트만 저장하면 그 시점에 과거 기록과 새 기록의 의미가 갈라지고 되돌릴 수 없다.
-- 전압을 함께 남겨야 곡선을 고친 뒤 과거를 다시 계산할 수 있다.

BEGIN;

ALTER TABLE robot_battery_log
    ADD COLUMN voltage REAL;

-- 퍼센트는 변환 노드가 계산한다. 그 노드가 죽어도 전압 기록은 계속되어야 한다.
ALTER TABLE robot_battery_log
    ALTER COLUMN battery_percent DROP NOT NULL;

-- 둘 다 비어 있는 행은 의미가 없다.
ALTER TABLE robot_battery_log
    ADD CONSTRAINT robot_battery_log_has_reading
    CHECK (voltage IS NOT NULL OR battery_percent IS NOT NULL);

-- 2셀 리튬이온. pinkylib 기준 6.8V(0%) ~ 7.6V(100%).
-- 범위를 넉넉히 잡아 충전 중 전압과 이상값 모두 담되, 명백한 오독은 거른다.
ALTER TABLE robot_battery_log
    ADD CONSTRAINT robot_battery_log_voltage_range
    CHECK (voltage IS NULL OR voltage BETWEEN 0 AND 12);

COMMIT;
