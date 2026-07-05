import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import SourceChannel


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
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
