BEGIN;

CREATE TABLE patient_photos (
    patient_id VARCHAR(20) PRIMARY KEY
        REFERENCES patients(patient_id) ON DELETE CASCADE,
    content_type VARCHAR(50) NOT NULL
        CHECK (content_type IN ('image/jpeg', 'image/png', 'image/webp')),
    image_data BYTEA NOT NULL
        CHECK (octet_length(image_data) BETWEEN 1 AND 5242880),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMIT;
