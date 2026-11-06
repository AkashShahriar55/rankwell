-- Rankwell schema v1. Dates are ISO strings (YYYY-MM-DD), timestamps ISO 8601 UTC.

-- The user's own apps (live or not yet released).
CREATE TABLE apps (
    id            INTEGER PRIMARY KEY,
    slug          TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    store_id      INTEGER UNIQUE,              -- App Store id; NULL before release
    bundle_id     TEXT,
    repo_path     TEXT,                        -- local path of the app's code repo (.aso/ lives there)
    genre_id      INTEGER,                     -- primary category id
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    archived_at   TEXT
);

CREATE TABLE app_countries (
    app_id   INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    country  TEXT NOT NULL,
    PRIMARY KEY (app_id, country)
);

CREATE TABLE keywords (
    id        INTEGER PRIMARY KEY,
    app_id    INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    country   TEXT NOT NULL,
    term      TEXT NOT NULL,
    added_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (app_id, country, term)
);

CREATE TABLE competitors (
    id        INTEGER PRIMARY KEY,
    app_id    INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    store_id  INTEGER NOT NULL,
    name      TEXT NOT NULL,
    added_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (app_id, store_id)
);

-- Daily listing snapshot of any tracked store app (own apps and competitors).
CREATE TABLE snapshots (
    store_id        INTEGER NOT NULL,
    country         TEXT NOT NULL,
    date            TEXT NOT NULL,
    title           TEXT,
    subtitle        TEXT,
    seller          TEXT,
    version         TEXT,
    rating_count    INTEGER,
    rating_avg      REAL,
    price           REAL,
    genre_id        INTEGER,
    released_at     TEXT,
    description     TEXT,
    release_notes   TEXT,
    screenshots     TEXT,                      -- JSON list of URLs
    PRIMARY KEY (store_id, country, date)
);

CREATE TABLE changes (
    id        INTEGER PRIMARY KEY,
    store_id  INTEGER NOT NULL,
    country   TEXT NOT NULL,
    date      TEXT NOT NULL,
    field     TEXT NOT NULL,
    old       TEXT,
    new       TEXT,
    UNIQUE (store_id, country, date, field)
);
CREATE INDEX changes_by_date ON changes (date);

-- Rank of each tracked store app for a keyword; rank NULL = not in the top 200.
CREATE TABLE ranks (
    date      TEXT NOT NULL,
    country   TEXT NOT NULL,
    term      TEXT NOT NULL,
    store_id  INTEGER NOT NULL,
    rank      INTEGER,
    PRIMARY KEY (date, country, term, store_id)
);
CREATE INDEX ranks_by_app ON ranks (store_id, country, term, date);

-- Top 10 results of a keyword on a day (who owns the term).
CREATE TABLE keyword_top (
    date      TEXT NOT NULL,
    country   TEXT NOT NULL,
    term      TEXT NOT NULL,
    position  INTEGER NOT NULL,
    store_id  INTEGER NOT NULL,
    name      TEXT,
    ratings   INTEGER,
    PRIMARY KEY (date, country, term, position)
);

CREATE TABLE keyword_scores (
    country                TEXT NOT NULL,
    term                   TEXT NOT NULL,
    date                   TEXT NOT NULL,
    popularity             INTEGER,
    suggested_after_chars  INTEGER,
    difficulty             INTEGER,
    median_ratings         INTEGER,
    title_match            REAL,
    top_app                TEXT,
    PRIMARY KEY (country, term, date)
);

CREATE TABLE reviews (
    id           TEXT PRIMARY KEY,
    store_id     INTEGER NOT NULL,
    country      TEXT NOT NULL,
    rating       INTEGER NOT NULL,
    title        TEXT,
    content      TEXT,
    author       TEXT,
    version      TEXT,
    reviewed_at  TEXT
);
CREATE INDEX reviews_by_app ON reviews (store_id, reviewed_at);

CREATE TABLE chart_positions (
    date      TEXT NOT NULL,
    country   TEXT NOT NULL,
    genre_id  INTEGER NOT NULL,
    chart     TEXT NOT NULL,                   -- free | paid | grossing
    store_id  INTEGER NOT NULL,
    rank      INTEGER NOT NULL,
    PRIMARY KEY (date, country, genre_id, chart, store_id)
);

-- App Store Connect analytics: raw rows (deduplicated by hash) and daily totals.
CREATE TABLE asc_report_rows (
    store_id     INTEGER NOT NULL,
    report_name  TEXT NOT NULL,
    report_date  TEXT NOT NULL,
    row_hash     TEXT NOT NULL,
    data         TEXT NOT NULL,                -- JSON
    PRIMARY KEY (store_id, report_name, row_hash)
);
CREATE INDEX asc_rows_by_date ON asc_report_rows (store_id, report_date);

CREATE TABLE asc_instances (
    id               TEXT PRIMARY KEY,
    store_id         INTEGER NOT NULL,
    report_name      TEXT NOT NULL,
    processing_date  TEXT,
    rows             INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE asc_daily (
    store_id  INTEGER NOT NULL,
    date      TEXT NOT NULL,
    metric    TEXT NOT NULL,                   -- impressions | page_views | taps | first_downloads | redownloads | ...
    source    TEXT NOT NULL,                   -- all | App Store search | App Store browse | ...
    value     INTEGER NOT NULL,
    PRIMARY KEY (store_id, date, metric, source)
);

-- Two-step writes to App Store Connect: plan, then apply after the user confirms.
CREATE TABLE plans (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    app_id      INTEGER REFERENCES apps(id) ON DELETE CASCADE,
    payload     TEXT NOT NULL,                 -- JSON: changes and everything apply needs
    live_hash   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending', -- pending | applied | failed | expired
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    applied_at  TEXT,
    result      TEXT
);

CREATE TABLE audit_log (
    id       INTEGER PRIMARY KEY,
    at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    plan_id  TEXT,
    kind     TEXT NOT NULL,
    target   TEXT,
    field    TEXT,
    old      TEXT,
    new      TEXT,
    status   TEXT NOT NULL,
    error    TEXT
);

CREATE TABLE runs (
    id           INTEGER PRIMARY KEY,
    kind         TEXT NOT NULL,                -- collect | import | ...
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL DEFAULT 'running', -- running | ok | partial | failed
    summary      TEXT                          -- JSON
);
