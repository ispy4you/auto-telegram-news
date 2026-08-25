import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import Project, SourceChannel


@pytest.fixture
def db_session():
    # StaticPool и check_same_thread нужны веб-тестам: TestClient выполняет
    # приложение в отдельном потоке, а у SQLite in-memory каждое соединение —
    # это своя отдельная база.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def source(db_session):
    src = SourceChannel(title="Test Source", username="testsource", url="https://t.me/testsource")
    db_session.add(src)
    db_session.commit()
    db_session.refresh(src)
    return src


CSRF_INPUT = re.compile(r'name="csrf_token"\s+value="([^"]+)"')


@pytest.fixture
def client(db_session):
    """Приложение поверх тестовой базы.

    TestClient создаётся без контекстного менеджера: тогда Starlette не запускает
    lifespan, и ни миграции, ни планировщик, ни слушатель в тестах не стартуют.
    """
    import app.main as main_module
    from app.web import auth as auth_module

    db_session.add(Project(id=1, name="Default", slug="default"))
    db_session.commit()

    main_module.app.dependency_overrides[get_db] = lambda: db_session
    auth_module._failed_attempts.clear()  # счётчик попыток живёт в памяти процесса
    try:
        yield TestClient(main_module.app, follow_redirects=False)
    finally:
        main_module.app.dependency_overrides.clear()
        auth_module._failed_attempts.clear()


def _csrf_token(client: TestClient, path: str = "/login") -> str:
    page = client.get(path)
    match = CSRF_INPUT.search(page.text)
    assert match, f"на странице {path} нет поля csrf_token"
    return match.group(1)


@pytest.fixture
def csrf():
    """Достаёт CSRF-токен со страницы: он нужен любому POST, кроме входа."""
    return _csrf_token


@pytest.fixture
def logged_in(client):
    """Клиент с открытой админской сессией."""
    client.post("/login", data={
        "username": "admin",
        "password": "change_me",
        "csrf_token": _csrf_token(client),
    })
    return client
