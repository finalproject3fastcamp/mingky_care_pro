"""안내 세션 종료 이벤트와 DB 제약조건의 계약을 확인한다."""

from pathlib import Path


MIGRATIONS = Path(__file__).parents[2] / "database" / "migrations"


def test_fire_is_allowed_as_session_end_reason():
    migration = MIGRATIONS / "007_fire_session_end_reason.sql"

    assert migration.is_file()
    assert "'fire'" in migration.read_text(encoding="utf-8")


def test_robot_type_rename_moves_existing_rows_before_re_arming_the_check():
    """010 은 이미 데이터가 있는 DB 를 대상으로 한다.

    CHECK 가 'manipulator' 를 막고 있으므로 제약을 먼저 떼야 UPDATE 가
    통과한다. 순서가 뒤집히면 빈 DB 에서는 통과하고 운영 DB 에서만 죽는다 —
    가장 늦게 발견되는 종류의 실수다.
    """
    sql = (MIGRATIONS / "010_robot_type_manipulator.sql").read_text(encoding="utf-8")

    drop = sql.index("DROP CONSTRAINT")
    update = sql.index("UPDATE robots")
    add = sql.index("ADD CONSTRAINT")

    assert drop < update < add
    assert "'arm'" in sql and "'manipulator'" in sql


def test_003_still_records_the_original_arm_constraint():
    """적용된 마이그레이션은 고치지 않는다.

    003 을 제자리에서 고치면 파일이 기존 DB 의 실제 이력을 더는 서술하지
    않고, 004~009 가 전제하는 상태와도 어긋난다. 이름 변경은 010 의 일이다.
    """
    sql = (MIGRATIONS / "003_sessions_and_events.sql").read_text(encoding="utf-8")

    assert "CHECK (robot_type IN ('mobile', 'arm'))" in sql


def test_migration_ledger_bootstraps_existing_databases():
    """schema_migrations 도입 시점에 001~009 는 이미 적용돼 있다.

    표시해 두지 않으면 러너가 001부터 다시 돌려 CREATE TABLE 에서 죽는다.
    빈 볼륨에서는 근거로 삼는 테이블이 아직 없으므로 backfill 이 걸리지 않고
    전체가 정상 실행된다.
    """
    sql = (MIGRATIONS / "000_schema_migrations.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS schema_migrations" in sql
    assert "to_regclass('public.robot_inventory')" in sql

    for version in sorted(p.stem for p in MIGRATIONS.glob("00[1-9]_*.sql")):
        assert f"('{version}')" in sql, f"{version} 가 backfill 목록에 없습니다"


def test_every_migration_is_named_so_the_runner_can_order_it():
    """러너가 파일명을 버전으로 쓴다. 접두사가 없으면 순서도 이력도 깨진다."""
    for migration in MIGRATIONS.glob("*.sql"):
        assert migration.stem[:3].isdigit(), migration.name
