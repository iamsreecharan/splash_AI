import os

# Isolated SQLite is used only by these unit tests. The release smoke suite uses
# the actual PostgreSQL/pgvector and Redis/Celery development stack.
os.environ["SPLASH_ENV"] = "test"
os.environ["SPLASH_DATABASE_URL"] = "sqlite:////tmp/splash-tests-bootstrap.db"
os.environ["SPLASH_DEMO_MODE"] = "true"
os.environ["SPLASH_CELERY_ALWAYS_EAGER"] = "true"
os.environ["SPLASH_AI_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import get_engine, session_factory
from app import main, tasks


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SPLASH_DATABASE_URL", f"sqlite:///{tmp_path / 'unit.db'}")
    monkeypatch.setenv("SPLASH_GITHUB_WEBHOOK_SECRET", "unit-test-webhook-secret")
    get_settings.cache_clear()
    get_engine.cache_clear()
    settings = get_settings()
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(tasks, "settings", settings)
    main.app.dependency_overrides.clear()
    with TestClient(main.app) as test_client:
        yield test_client
    main.app.dependency_overrides.clear()
    get_engine().dispose()
    get_engine.cache_clear()
    get_settings.cache_clear()


@pytest.fixture
def db(client):
    with session_factory()() as session:
        yield session
