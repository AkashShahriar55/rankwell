-- Data quality: Google demand per keyword, and chart-position → downloads curves fitted on the user's own apps.

-- Google signal next to the App Store popularity proxy (NULL = not checked or Google did not answer).
ALTER TABLE keyword_scores ADD COLUMN google_demand INTEGER;

-- Weekly Google demand of tracked keywords. demand NULL with checked rows = Google never suggested it.
CREATE TABLE google_demand (
    country                TEXT NOT NULL,
    term                   TEXT NOT NULL,
    date                   TEXT NOT NULL,
    demand                 INTEGER,          -- 0-100, same letters-typed scale as App Store popularity
    suggested_after_chars  INTEGER,
    position               INTEGER,
    trend_change_pct       REAL,             -- Google Trends: last 13 weeks vs the 39 before; NULL = unknown
    trend_low_volume       INTEGER,          -- 1 = Trends has too little data (few searches); NULL = unknown
    PRIMARY KEY (country, term, date)
);

-- Power law downloads = a × rank^b fitted in log-log space on days where one of the user's apps had both a
-- category chart position and App Store Connect first downloads in that country. key: chart/genre/country, or
-- 'global' for the pooled fit. Estimates from these are never real numbers.
CREATE TABLE download_curves (
    key          TEXT PRIMARY KEY,
    chart        TEXT,
    genre_id     INTEGER,
    country      TEXT,
    a            REAL NOT NULL,
    b            REAL NOT NULL,
    slope_fixed  INTEGER NOT NULL DEFAULT 0,  -- 1 = too few points for a slope; b is the assumed slope
    sigma        REAL NOT NULL,               -- spread of ln(downloads) used for the range
    points       INTEGER NOT NULL,
    rank_min     INTEGER,
    rank_max     INTEGER,
    date_min     TEXT,
    date_max     TEXT,
    fitted_on    TEXT NOT NULL
);
