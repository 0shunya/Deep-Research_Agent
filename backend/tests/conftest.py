import os
import sys
from pathlib import Path

# Configure before importing the app: isolated SQLite DB, no scheduler thread, and never a real API key.
os.environ.update({
    "APP_ENV": "test",
    "GEMINI_API_KEY": "",
    "TAVILY_API_KEY": "",
    "WORKER_MODE": "off",
    "DEMO_ACCESS_CODE": "",
    "DAILY_MODEL_CALL_LIMIT": "0",
})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import Base, get_engine, reset_engine  # noqa: E402


@pytest.fixture()
def database(tmp_path, monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL") if os.environ.get("USE_TEST_POSTGRES") else None
    monkeypatch.setenv("DATABASE_URL", url or f"sqlite:///{tmp_path / 'test.db'}")
    get_settings.cache_clear()
    reset_engine()
    from app import models  # noqa: F401
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture()
def settings_override(monkeypatch):
    def apply(**values):
        for key, value in values.items():
            monkeypatch.setenv(key.upper(), str(value))
        get_settings.cache_clear()
    yield apply
    get_settings.cache_clear()


@pytest.fixture()
def make_env(database):
    """A ResearchEnv over the simulated web and the scripted model, logging to the test database."""
    from app.llm.gateway import ModelGateway
    from app.research.env import ResearchEnv
    from app.research.simulated import scripted_provider, static_web
    from app.services.observability import record_call_to_db

    async def no_sleep(_):
        return None

    def build(provider=None, web=None, **kwargs):
        web = web or static_web()
        provider = provider or scripted_provider(**kwargs)
        gateway = ModelGateway([provider], record_call_to_db, max_attempts=2, sleep=no_sleep)
        return ResearchEnv(gateway, web, web, get_settings())

    return build


@pytest.fixture()
def new_run(database):
    from app.db import session_scope
    from app.models import Run

    def create(question="When was the Eiffel Tower completed and how tall is it?", depth="standard",
               budget=100_000, workers=3):
        with session_scope() as db:
            run = Run(question=question, depth=depth, budget_tokens=budget, max_workers=workers,
                      search_provider="static")
            db.add(run)
            db.flush()
            return run.id

    return create
