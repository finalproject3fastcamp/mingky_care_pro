BEGIN;

CREATE TABLE conditions (
    condition_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    condition_name VARCHAR(100) NOT NULL UNIQUE
);

CREATE TABLE patients (
    patient_id VARCHAR(20) PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    age SMALLINT NOT NULL CHECK (age >= 0),
    birth_date DATE NOT NULL,
    gender VARCHAR(10) NOT NULL,
    condition_id BIGINT NOT NULL REFERENCES conditions(condition_id)
);

CREATE TABLE examination_steps (
    examination_step_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    condition_id BIGINT NOT NULL REFERENCES conditions(condition_id) ON DELETE CASCADE,
    step_order SMALLINT NOT NULL CHECK (step_order > 0),
    examination_name VARCHAR(100) NOT NULL,
    UNIQUE (condition_id, step_order)
);

CREATE TABLE medications (
    medication_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    medication_name VARCHAR(100) NOT NULL UNIQUE,
    color VARCHAR(20) NOT NULL
);

CREATE TABLE condition_medications (
    condition_id BIGINT NOT NULL REFERENCES conditions(condition_id) ON DELETE CASCADE,
    medication_id BIGINT NOT NULL REFERENCES medications(medication_id),
    PRIMARY KEY (condition_id, medication_id)
);

COMMIT;
