from rankwell import db as dbmod


def test_migrations_apply_once(db: dbmod.Database) -> None:
    assert db.value("SELECT count(*) FROM schema_migrations") >= 1
    assert dbmod.migrate(db.conn) == []


def test_upsert_updates_non_key_columns(db: dbmod.Database) -> None:
    row = {"country": "us", "term": "pill reminder", "date": "2026-11-07", "popularity": 50, "difficulty": 70}
    db.upsert("keyword_scores", [row], ["country", "term", "date"])
    db.upsert("keyword_scores", [{**row, "popularity": 60}], ["country", "term", "date"])
    assert db.all("SELECT popularity FROM keyword_scores") == [{"popularity": 60}]


def test_upsert_serialises_lists(db: dbmod.Database) -> None:
    db.upsert("snapshots", [{"store_id": 1, "country": "us", "date": "2026-11-07", "screenshots": ["a", "b"]}],
              ["store_id", "country", "date"])
    assert db.value("SELECT screenshots FROM snapshots") == '["a", "b"]'


def test_transaction_rolls_back(db: dbmod.Database) -> None:
    try:
        with db.tx():
            db.execute("INSERT INTO apps (slug, name) VALUES ('a', 'A')")
            raise RuntimeError
    except RuntimeError:
        pass
    assert db.value("SELECT count(*) FROM apps") == 0


def test_backup(db: dbmod.Database, tmp_path) -> None:  # type: ignore[no-untyped-def]
    db.execute("INSERT INTO apps (slug, name) VALUES ('a', 'A')")
    out = db.backup(tmp_path / "copy.db")
    assert dbmod.Database(out).value("SELECT name FROM apps") == "A"
