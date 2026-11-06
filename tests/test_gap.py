import json

from rankwell import apps, gap
from rankwell.db import Database

AS_OF = "2026-11-07"
DAY = "2026-11-06"
OWN, MEDI, ROUND = 111, 222, 333
TERMS = ("pill reminder", "medication tracker", "pill tracker", "med alarm")


def snap(sid: int, title: str, subtitle: str, count: int, avg: float, shots: int, desc: int, released: str,
         date: str = DAY) -> dict:
    return {"store_id": sid, "country": "us", "date": date, "title": title, "subtitle": subtitle,
            "rating_count": count, "rating_avg": avg, "price": 0.0, "released_at": released, "version": "1.0",
            "description": "x" * desc, "screenshots": json.dumps([f"u{i}" for i in range(shots)])}


def seed(db: Database) -> dict:
    a = apps.add(db, "Pill Pal", store_id=OWN, countries=["us"])
    apps.add_keywords(db, a["id"], list(TERMS), "us")
    apps.add_competitor(db, a["id"], MEDI, "Medisafe")
    apps.add_competitor(db, a["id"], ROUND, "Round Health")
    db.upsert("keyword_scores", [{"country": "us", "term": t, "date": "2026-11-01", "popularity": p, "difficulty": 50}
                                 for t, p in (("pill reminder", 60), ("medication tracker", 40), ("med alarm", 20))],
              ["country", "term", "date"])
    db.upsert("snapshots", [
        snap(OWN, "Pill Pal: Pill Reminder", "Medication tracker & alarm", 53, 4.0, 3, 300, "2026-06-01",
             date="2026-08-01"),
        snap(OWN, "Pill Pal: Pill Reminder", "Medication tracker & alarm", 53, 4.0, 3, 300, "2026-06-01"),
        snap(MEDI, "Medisafe Pill Reminder", "Med Alarm & Pill Tracker", 50_000, 4.7, 8, 3000, "2015-01-01"),
        snap(ROUND, "Round Health", "Medication tracker", 8_000, 4.6, 10, 2500, "2018-01-01"),
    ], ["store_id", "country", "date"])
    db.upsert("changes", [
        {"store_id": MEDI, "country": "us", "date": "2026-10-10", "field": "version", "old": "9.1", "new": "9.2"},
        {"store_id": MEDI, "country": "us", "date": "2026-09-01", "field": "version", "old": "9.0", "new": "9.1"},
        {"store_id": ROUND, "country": "us", "date": "2026-10-20", "field": "version", "old": "3.0", "new": "3.1"},
    ], ["store_id", "country", "date", "field"])
    ranks = {OWN: {"pill reminder": 12, "medication tracker": 60, "pill tracker": 80, "med alarm": None},
             MEDI: {"pill reminder": 2, "medication tracker": 1, "pill tracker": None, "med alarm": 4},
             ROUND: {"pill reminder": 9, "medication tracker": None, "pill tracker": None, "med alarm": None}}
    db.upsert("ranks", [{"date": DAY, "country": "us", "term": t, "store_id": sid, "rank": r}
                        for sid, by in ranks.items() for t, r in by.items()], ["date", "country", "term", "store_id"])
    db.upsert("keyword_top", [{"date": DAY, "country": "us", "term": "medication tracker", "position": 1,
                               "store_id": MEDI, "name": "Medisafe Pill Reminder", "ratings": 50_000}],
              ["date", "country", "term", "position"])
    db.upsert("chart_positions", [{"date": DAY, "country": "us", "genre_id": 6013, "chart": "free", "store_id": sid,
                                   "rank": r} for sid, r in ((MEDI, 15), (ROUND, 40))],
              ["date", "country", "genre_id", "chart", "store_id"])
    reviews = [
        (MEDI, 2, "Sync broken", "Sync between devices stopped working after the last update and support never "
                                 "answers my emails, which is frustrating because I rely on it every single day."),
        (MEDI, 1, "Too many ads", "Way too many ads, and the subscription price is ridiculous for a pill reminder."),
        (ROUND, 3, "No dark mode", "Please add dark mode. Also sync between devices loses my history."),
        (ROUND, 2, "Subscription", "The subscription price went up again; dark mode would be nice."),
        (ROUND, 5, "Perfect", "Dark mode dark mode dark mode is fine."),
        (OWN, 2, "Crashes on launch", "The app crashes on launch every morning."),
        (OWN, 3, "Reminder never fires", "Never fires when locked and it crashes sometimes."),
    ]
    db.upsert("reviews", [{"id": f"r{i}", "store_id": sid, "country": "us", "rating": stars, "title": title,
                           "content": text, "reviewed_at": "2026-10-15T10:00:00Z"}
                          for i, (sid, stars, title, text) in enumerate(reviews)], ["id"])
    return a


def test_covers_whole_words() -> None:
    assert gap.covers("pill tracker", "Pill Pal: Reminder & Tracker")
    assert not gap.covers("med alarm", "Medication alarm")
    assert not gap.covers("pill", "Pills")
    assert not gap.covers("", "anything")


def test_themes_counts_once_per_review_and_prefers_phrases() -> None:
    texts = ["Dark mode please. Dark mode!", "No dark mode at all", "Sync fails", "sync again", "ads"]
    t = gap.themes(texts)
    assert [x["term"] for x in t] == ["dark mode", "sync"]
    assert t[0]["reviews"] == 2 and t[0]["share"] == 40
    long = ["word " * 40 + "battery drain is awful " + "word " * 40, "battery drain again"]
    q = gap.themes(long)[0]
    assert q["term"] == "battery drain" and "battery drain" in q["example"] and len(q["example"]) <= 122
    assert q["example"].startswith("…") and q["example"].endswith("…")
    assert gap.themes([]) == []


def test_themes_never_span_sentences_and_quotes_never_raise() -> None:
    # title and content are joined with ". ": "crashes. login" must not become the phrase "crashes login"
    texts = [gap._text({"title": "Constant crashes", "content": "Login broken"}) for _ in range(3)]
    t = gap.themes(texts)
    assert "crashes login" not in {x["term"] for x in t}
    assert {"constant crashes", "login broken"} <= {x["term"] for x in t}
    assert all(x["example"] for x in t)
    # apostrophes stripped by the tokenizer and digits between words still find a quote
    texts = ["My users' data vanished", "users' data gone again", "Lost users' data", "iOS17 crashes daily",
             "ios17 crashes", "iOS17 crashes all day"]
    by = {x["term"]: x for x in gap.themes(texts)}
    assert "users' data" in (by["users data"]["example"] or "")
    assert "crashes" in (by["ios crashes"]["example"] or "")


def test_theme_example_falls_back_and_never_raises() -> None:
    docs = ["the 'cause' was bad", "nothing here"]
    assert gap._example(docs, "cause") == docs[0]
    assert gap._example(docs, "cause missing") == docs[0]  # first word only
    assert gap._example(docs, "zebra stripes") is None
    assert gap._quoted({"example": None}) == "" and gap._quoted({"example": "x"}) == ": “x”"


def test_report_strengths_and_weaknesses(db: Database) -> None:
    seed(db)
    r = gap.report(db, "Pill Pal", as_of=AS_OF)
    own = r["own"]
    assert own["coverage"] == 3
    assert sorted(own["covered"]) == ["medication tracker", "pill reminder", "pill tracker"]
    assert own["screenshots"] == 3 and own["days_since_update"] == 98 and own["update_lower_bound"] is True
    assert r["median"]["rating_count"] == 29_000 and r["median"]["coverage"] == 2
    assert r["median"]["versions_90d"] == 1.5 and r["median"]["days_since_update"] == 23
    weak = {w["area"]: w for w in r["weaknesses"]}
    strong = {s["area"]: s for s in r["strengths"]}
    assert "53 ratings vs median competitor 29k" in [w["evidence"] for w in r["weaknesses"]]
    assert weak["screenshots"]["evidence"] == "3 screenshots vs median 9"
    assert weak["description"]["evidence"] == "300 characters vs median 2,750"
    assert weak["charts"]["evidence"] == "2 of 2 competitors charted on 2026-11-06"
    assert weak["reviews"]["point"] == "Your low reviews keep mentioning “crashes”"
    assert any(w["evidence"].startswith("last update over 98 days ago") for w in r["weaknesses"])
    behind = next(w for w in r["weaknesses"] if w["point"].startswith("Competitors hold the top 10 on 2"))
    assert behind["evidence"] == "medication tracker (Medisafe #1, you #60), med alarm (Medisafe #4, you –)"
    assert strong["keywords"]["evidence"] == "title + subtitle cover 3 tracked keywords vs median competitor 2"
    assert all(x["evidence"] for x in r["strengths"] + r["weaknesses"])


def test_report_keyword_battle_and_complaints(db: Database) -> None:
    seed(db)
    r = gap.report(db, "Pill Pal", as_of=AS_OF)
    battle = {b["term"]: b for b in r["keyword_battle"]}
    assert battle["pill reminder"]["owner"] == "Medisafe" and battle["pill reminder"]["best_competitor"] == 2
    assert battle["pill tracker"]["owner"] == "you" and battle["pill tracker"]["own"] == 80
    assert battle["medication tracker"]["leader"] == "Medisafe Pill Reminder"
    assert r["keyword_battle"][0]["term"] == "pill reminder"  # most popular first
    comp = r["complaints"]["competitors"]
    assert comp["reviews"] == 4  # the 5★ review is ignored
    assert [t["term"] for t in comp["themes"]][:2] == ["dark mode", "subscription price"]
    assert {t["term"] for t in comp["themes"]} >= {"sync", "devices"}
    assert all(len(t["example"]) <= 122 for t in comp["themes"])
    assert r["opportunities"][0]["point"] == "Competitors' users complain about “dark mode”"
    assert "Keyword battle" in r["display"] and "_median competitor_" in r["display"]


def test_report_without_competitors_or_data(db: Database) -> None:
    a = apps.add(db, "Solo", store_id=7)
    assert "No competitors tracked" in gap.report(db, a)["display"]
    apps.add_competitor(db, a["id"], 8, "Other")
    out = gap.report(db, a)
    assert "No listing snapshots" in out["display"] and out["strengths"] == []


def test_report_for_concept_app_is_market_baseline(db: Database) -> None:
    seed(db)
    c = apps.add(db, "Concept")
    apps.add_competitor(db, c["id"], MEDI, "Medisafe")
    apps.add_competitor(db, c["id"], ROUND, "Round Health")
    r = gap.report(db, c, as_of=AS_OF)
    assert r["own"] is None and r["strengths"] == [] and r["weaknesses"] == []
    assert r["opportunities"] and "market baseline only" in r["display"]
