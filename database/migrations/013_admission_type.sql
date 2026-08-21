-- 환자 외래/입원 구분 플래그.
--
-- ## 왜 환자 레코드인가
--
-- 검사가 끝난 뒤 로봇이 어디로 안내할지가 갈린다 — 외래는 수납·약국·약수령,
-- 입원은 병동이다. 이 갈림은 증상(condition)이 아니라 그 환자의 접수 형태가
-- 정한다. 같은 십자인대 파열이라도 당일 외래로 왔는지 입원 중인지에 따라
-- 경로가 다르다. 그래서 conditions 가 아니라 patients 에 둔다.
--
-- ## 왜 스냅샷이 아니라 여기서 읽는가
--
-- session_steps 는 스캔 시점에 examination_steps 를 복사한다(qr.py). 분기
-- 스텝도 그 순간 이 플래그를 읽어 이어 붙인다 — 나중에 환자의 접수 형태가
-- 바뀌어도 이미 시작된 안내의 경로는 그대로다.
--
-- ## 기본값
--
-- 대부분이 외래이고, 값이 없는 기존 행을 입원으로 두면 로봇이 병동으로
-- 보내 버린다. NOT NULL DEFAULT 'outpatient' 로 안전한 쪽에 세운다.

BEGIN;

ALTER TABLE patients
    ADD COLUMN IF NOT EXISTS admission_type TEXT NOT NULL DEFAULT 'outpatient'
        CHECK (admission_type IN ('outpatient', 'inpatient'));

COMMIT;
