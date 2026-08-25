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


def test_account_round_trips_from_a_telethon_user(db_session):
    """Слушатель пишет сюда объект от Telethon, карточка читает отсюда поля."""
    from types import SimpleNamespace

    me = SimpleNamespace(id=7, username="spy4u", first_name="spy4u", last_name=None, phone="79990000000")

    store.save_account(me, db_session)
    account = store.load_account(db_session)

    assert account["username"] == "spy4u"
    assert account["first_name"] == "spy4u"


def test_clearing_the_account_removes_it(db_session):
    from types import SimpleNamespace

    store.save_account(SimpleNamespace(id=7, username="spy4u"), db_session)
    store.clear_account(db_session)

    assert store.load_account(db_session) is None
