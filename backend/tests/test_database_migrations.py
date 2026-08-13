"""안내 세션 종료 이벤트와 DB 제약조건의 계약을 확인한다."""

from pathlib import Path


MIGRATIONS = Path(__file__).parents[2] / "database" / "migrations"


def test_fire_is_allowed_as_session_end_reason():
    migration = MIGRATIONS / "007_fire_session_end_reason.sql"

    assert migration.is_file()
    assert "'fire'" in migration.read_text(encoding="utf-8")
