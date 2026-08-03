BEGIN;

INSERT INTO conditions (condition_name)
VALUES
    ('퇴행성 무릎 관절염'),
    ('단순 팔 골절'),
    ('십자인대 파열')
ON CONFLICT (condition_name) DO NOTHING;

INSERT INTO medications (medication_name, color)
VALUES
    ('소염진통제', '초록색'),
    ('신경진정제', '빨간색'),
    ('관절재활제', '노란색')
ON CONFLICT (medication_name)
DO UPDATE SET color = EXCLUDED.color;

INSERT INTO patients (patient_id, name, age, birth_date, gender, condition_id)
SELECT patient_id, name, age, birth_date, gender, condition_id
FROM (
    VALUES
        ('p001', '윤동수', 73, DATE '1953-01-15', '남자', '퇴행성 무릎 관절염'),
        ('p002', '권민수', 50, DATE '1976-08-22', '남자', '단순 팔 골절'),
        ('p003', '김지우', 21, DATE '2005-04-09', '여자', '십자인대 파열')
) AS source(patient_id, name, age, birth_date, gender, condition_name)
JOIN conditions USING (condition_name)
ON CONFLICT (patient_id) DO UPDATE SET
    name = EXCLUDED.name,
    age = EXCLUDED.age,
    birth_date = EXCLUDED.birth_date,
    gender = EXCLUDED.gender,
    condition_id = EXCLUDED.condition_id;

INSERT INTO examination_steps (condition_id, step_order, examination_name)
SELECT condition_id, step_order, examination_name
FROM (
    VALUES
        ('퇴행성 무릎 관절염', 1, 'X-ray'),
        ('퇴행성 무릎 관절염', 2, '임상병리실'),
        ('퇴행성 무릎 관절염', 3, '물리치료실'),
        ('단순 팔 골절', 1, 'X-ray'),
        ('단순 팔 골절', 2, 'CT'),
        ('십자인대 파열', 1, 'X-ray'),
        ('십자인대 파열', 2, 'MRI'),
        ('십자인대 파열', 3, '물리치료실')
) AS source(condition_name, step_order, examination_name)
JOIN conditions USING (condition_name)
ON CONFLICT (condition_id, step_order)
DO UPDATE SET examination_name = EXCLUDED.examination_name;

INSERT INTO condition_medications (condition_id, medication_id)
SELECT condition_id, medication_id
FROM (
    VALUES
        ('퇴행성 무릎 관절염', '소염진통제'),
        ('퇴행성 무릎 관절염', '관절재활제'),
        ('단순 팔 골절', '관절재활제'),
        ('십자인대 파열', '소염진통제'),
        ('십자인대 파열', '신경진정제'),
        ('십자인대 파열', '관절재활제')
) AS source(condition_name, medication_name)
JOIN conditions USING (condition_name)
JOIN medications USING (medication_name)
ON CONFLICT (condition_id, medication_id) DO NOTHING;

COMMIT;
