import pytest

from rankwell import apps
from rankwell.db import Database
from rankwell.errors import RankwellError


def test_add_and_find(db: Database) -> None:
    a = apps.add(db, "NostalgAI: Retro Camera", store_id=6759825215, countries=["us", "gb"])
    assert a["slug"] == "nostalgai-retro-camera"
    assert a["stage"] == "live"
    assert a["countries"] == ["us", "gb"]
    for ref in (a["id"], 6759825215, "nostalgai-retro-camera", "nostalgai: retro camera", "nostal"):
        assert apps.get(db, ref)["id"] == a["id"]


def test_prelaunch_and_unknown(db: Database) -> None:
    a = apps.add(db, "Quran Path")
    assert a["stage"] == "prelaunch"
    with pytest.raises(RankwellError, match="Your apps: Quran Path"):
        apps.get(db, "nothing")


def test_duplicate_slug(db: Database) -> None:
    apps.add(db, "Spark")
    with pytest.raises(RankwellError):
        apps.add(db, "spark")


def test_keywords_and_competitors(db: Database) -> None:
    a = apps.add(db, "PassFirst", store_id=111111111)
    assert apps.add_keywords(db, a["id"], ["2x2 Photo", "us visa photo", " "], "us") == 2
    assert apps.add_keywords(db, a["id"], ["2x2 photo"], "us") == 0
    assert apps.remove_keywords(db, a["id"], ["2x2 photo"], "us") == 1
    apps.add_competitor(db, a["id"], 222222222, "Rival")
    apps.add_competitor(db, a["id"], 222222222, "Rival renamed")
    assert apps.tracked_store_ids(db, a) == [111111111, 222222222]
    assert apps.remove_competitor(db, a["id"], 222222222)


def test_update_rejects_unknown_fields(db: Database) -> None:
    a = apps.add(db, "Spark")
    with pytest.raises(RankwellError):
        apps.update(db, a["id"], slug="x")
    assert apps.update(db, a["id"], store_id=123456789)["stage"] == "live"
