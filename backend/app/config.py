import os
from pathlib import Path

from dotenv import load_dotenv

DATABASE_ENV_PATH = Path(__file__).resolve().parents[2] / "database" / ".env"
load_dotenv(DATABASE_ENV_PATH)

_HOST = os.environ.get("POSTGRES_HOST", "localhost")
_PORT = os.environ["POSTGRES_PORT"]
_USER = os.environ["POSTGRES_USER"]
_PASSWORD = os.environ["POSTGRES_PASSWORD"]
_DB = os.environ["POSTGRES_DB"]

DATABASE_URL = f"postgresql://{_USER}:{_PASSWORD}@{_HOST}:{_PORT}/{_DB}"
