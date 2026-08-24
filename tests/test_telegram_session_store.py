from app.services import telegram_session_store as store


def test_load_string_is_none_when_nothing_saved(db_session):
    assert store.load_string(db_session) is None


def test_save_then_load_roundtrip(db_session):
    store.save_string("1BVtsOHYBu0", db_session)
    assert store.load_string(db_session) == "1BVtsOHYBu0"


def test_save_overwrites_previous_session(db_session):
    store.save_string("first", db_session)
    store.save_string("second", db_session)
    assert store.load_string(db_session) == "second"


def test_clear_removes_session(db_session):
    store.save_string("1BVtsOHYBu0", db_session)
    store.clear(db_session)
    assert store.load_string(db_session) is None


def test_blank_value_counts_as_no_session(db_session):
    store.save_string("   ", db_session)
    assert store.load_string(db_session) is None


def test_load_session_without_stored_string_is_unauthorized(db_session):
    assert store.load_session(db_session).auth_key is None
