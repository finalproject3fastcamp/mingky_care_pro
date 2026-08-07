"""환자 프로필 사진을 PostgreSQL BYTEA 컬럼에 적재한다."""

import asyncio
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

DATABASE_DIR = Path(__file__).resolve().parents[1]
PHOTO_DIR = Path(__file__).resolve().parent / "assets" / "patient_photos"
MAX_IMAGE_BYTES = 5 * 1024 * 1024

PATIENT_PHOTOS = {
    "p001": PHOTO_DIR / "p001.jpg",
    "p002": PHOTO_DIR / "p002.jpg",
    "p003": PHOTO_DIR / "p003.jpg",
}


def database_url() -> str:
    load_dotenv(DATABASE_DIR / ".env")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    return (
        f"postgresql://{os.environ['POSTGRES_USER']}:"
        f"{os.environ['POSTGRES_PASSWORD']}@{host}:"
        f"{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
    )


def read_jpeg(path: Path) -> bytes:
    image_data = path.read_bytes()
    if not image_data.startswith(b"\xff\xd8\xff"):
        raise ValueError(f"JPEG 파일이 아닙니다: {path}")
    if not image_data.endswith(b"\xff\xd9"):
        raise ValueError(f"완전한 JPEG 파일이 아닙니다: {path}")
    if len(image_data) > MAX_IMAGE_BYTES:
        raise ValueError(f"사진이 5MB를 초과합니다: {path}")
    return image_data


async def load() -> None:
    connection = await asyncpg.connect(database_url())
    try:
        patient_rows = await connection.fetch(
            "SELECT patient_id FROM patients WHERE patient_id = ANY($1::varchar[])",
            list(PATIENT_PHOTOS),
        )
        known_ids = {row["patient_id"] for row in patient_rows}
        missing_ids = set(PATIENT_PHOTOS) - known_ids
        if missing_ids:
            missing = ", ".join(sorted(missing_ids))
            raise RuntimeError(f"DB에 없는 환자 ID입니다: {missing}")

        async with connection.transaction():
            for patient_id, path in PATIENT_PHOTOS.items():
                image_data = read_jpeg(path)
                await connection.execute(
                    """
                    INSERT INTO patient_photos (
                        patient_id, content_type, image_data, updated_at
                    )
                    VALUES ($1, 'image/jpeg', $2, NOW())
                    ON CONFLICT (patient_id) DO UPDATE SET
                        content_type = EXCLUDED.content_type,
                        image_data = EXCLUDED.image_data,
                        updated_at = NOW()
                    """,
                    patient_id,
                    image_data,
                )
                print(f"{patient_id}: {len(image_data)} bytes 적재")
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(load())
