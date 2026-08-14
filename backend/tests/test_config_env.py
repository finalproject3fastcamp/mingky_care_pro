"""DB 환경 변수가 없는 곳에서도 앱을 import 할 수 있어야 한다.

CI 러너에는 database/.env 가 없다(.gitignore). 이 계약이 깨지면 DB 를 쓰지도
않는 테스트까지 수집 단계에서 무너져서 CI 자체가 성립하지 않는다.

동시에 "없으면 조용히 기본값으로 붙는" 것도 막는다. 오타 하나로 엉뚱한 DB 에
연결되는 쪽이 기동 실패보다 나쁘다.
"""

import importlib

import pytest

from app import config


def _clear_db_env(monkeypatch):
    for key in config._REQUIRED_DB_KEYS + ("POSTGRES_HOST",):
        monkeypatch.delenv(key, raising=False)


def test_app_imports_without_db_env(monkeypatch):
    """.env 가 없어도 import 는 통과한다."""
    _clear_db_env(monkeypatch)

    importlib.reload(config)
    from app import db, main   # noqa: F401  — import 자체가 검증 대상이다

    assert hasattr(config, "database_url")


def test_missing_env_fails_at_call_time_naming_every_missing_key(monkeypatch):
    """실패는 남긴다. 어느 키가 빠졌는지 한 번에 다 알려준다.

    하나씩 알려주면 네 번 고쳐 넣어야 한다.
    """
    _clear_db_env(monkeypatch)

    with pytest.raises(RuntimeError) as exc:
        config.database_url()

    message = str(exc.value)
    for key in config._REQUIRED_DB_KEYS:
        assert key in message


def test_empty_string_counts_as_missing(monkeypatch):
    """빈 값으로 채워진 .env 는 안 채운 것과 같다.

    postgresql://:@:/ 로 조립돼서 asyncpg 가 훨씬 뒤에서 이상한 소리를 한다.
    """
    _clear_db_env(monkeypatch)
    for key in config._REQUIRED_DB_KEYS:
        monkeypatch.setenv(key, "")

    with pytest.raises(RuntimeError):
        config.database_url()


def test_host_defaults_to_localhost(monkeypatch):
    """POSTGRES_HOST 만 옵션이다. 나머지 넷은 필수."""
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("POSTGRES_USER", "mingky")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("POSTGRES_DB", "mingky")
    monkeypatch.setenv("POSTGRES_PORT", "5432")

    assert config.database_url() == "postgresql://mingky:secret@localhost:5432/mingky"

    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    assert config.database_url() == "postgresql://mingky:secret@postgres:5432/mingky"
