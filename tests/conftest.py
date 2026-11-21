"""Task 1 tests always redirect sessions and exports to pytest's temporary folder."""
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from observability import database


@pytest.fixture(autouse=True)
def offline_by_default(monkeypatch):
    """Tests never inherit permission to send data to a configured live provider."""
    monkeypatch.setenv("LLM_MODE", "offline")
    monkeypatch.setenv("LLM_ALLOW_EXTERNAL", "false")


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'task1.db'}",
                           connect_args={"check_same_thread": False}, hide_parameters=True)

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr("observability.service.DATA_DIR", tmp_path)
    database.init_db()
    yield engine
    engine.dispose()
