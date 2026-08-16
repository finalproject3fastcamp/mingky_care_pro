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


def test_control_audit_keeps_anonymous_rows_distinguishable():
    """익명 actor 를 센티널 문자열로 표시하지 않는다.

    'unknown' 같은 값을 기본값으로 박으면 조작자가 실제로 그렇게 적어 보낸
    행과 아무것도 안 온 행이 같아진다. 익명 비율을 보려고 만든 표가 익명을
    못 세게 되고, 그 사실은 몇 달 뒤 감사 요청이 들어와야 드러난다.

    NULL 로 두고 짝 제약으로 묶는 것이 이 설계의 핵심이라, 편의상
    NOT NULL DEFAULT 로 바꾸는 것이 가장 현실적인 붕괴 경로다.
    """
    sql = (MIGRATIONS / "011_control_audit.sql").read_text(encoding="utf-8")

    table = sql[sql.index("CREATE TABLE control_audit"):sql.index("CREATE INDEX")]
    # 주석은 뺀다. 이 파일은 'unknown' 을 쓰지 않는 이유를 주석으로 적고
    # 있으므로, 산문까지 훑으면 설명이 위반으로 잡힌다.
    audit_table = "\n".join(
        line for line in table.splitlines() if not line.strip().startswith("--"))

    actor_column = next(
        line for line in audit_table.splitlines()
        if line.strip().startswith("actor "))

    assert "CHECK ((actor IS NULL) = (actor_source = 'absent'))" in audit_table
    assert "'unknown'" not in audit_table
    assert "NOT NULL" not in actor_column, actor_column


def test_every_migration_is_named_so_the_runner_can_order_it():
    """러너가 파일명을 버전으로 쓴다. 접두사가 없으면 순서도 이력도 깨진다."""
    for migration in MIGRATIONS.glob("*.sql"):
        assert migration.stem[:3].isdigit(), migration.name


def test_servo_log_keeps_unread_and_healthy_apart():
    """hardware_error 의 0 과 NULL 은 다른 사실이다 (§4.4).

    전자는 '정상이라고 읽었다', 후자는 '못 읽었다' 이고, NOT NULL 로 묶어
    0 을 기본값으로 넣으면 통신이 죽은 서보가 정상으로 집계된다.
    """
    sql = (MIGRATIONS / "012_robot_servo_log.sql").read_text(encoding="utf-8")

    assert "hardware_error SMALLINT" in sql
    assert "hardware_error SMALLINT NOT NULL" not in sql
    # 전부 비어 있는 행은 의미가 없다. robot_battery_log 와 같은 가드다.
    assert "robot_servo_log_has_reading" in sql
    # 추이 질의가 조인트 단위로 돈다. 인덱스가 그 순서여야 한다.
    assert "(robot_id, joint, recorded_at DESC)" in sql
