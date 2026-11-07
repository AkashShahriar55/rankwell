-- Google Play: the app's package name and its Play reviews.

ALTER TABLE apps ADD COLUMN play_package TEXT;            -- e.g. com.example.app; NULL when not on Play
CREATE UNIQUE INDEX apps_play_package ON apps (play_package) WHERE play_package IS NOT NULL;

-- Play reviews (the API returns only those written or edited in the last 7 days, so they are kept here).
CREATE TABLE play_reviews (
    id            TEXT PRIMARY KEY,                     -- Play review id
    app_id        INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    rating        INTEGER,
    content       TEXT,
    author        TEXT,
    language      TEXT,
    version       TEXT,                                 -- app version name
    version_code  INTEGER,
    device        TEXT,
    reviewed_at   TEXT,                                 -- last change by the user, ISO 8601 UTC
    reply         TEXT,
    replied_at    TEXT,
    fetched_at    TEXT NOT NULL
);
CREATE INDEX play_reviews_by_app ON play_reviews (app_id, reviewed_at);
