import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import SourceChannel


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
