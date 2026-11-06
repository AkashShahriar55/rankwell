-- Listing and release: metadata drafts, background build jobs, App Review rejections.

-- Draft metadata per app and locale (JSON object of field → value), edited in chat before a plan.
CREATE TABLE listing_drafts (
    app_id      INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    locale      TEXT NOT NULL,
    fields      TEXT NOT NULL,                 -- JSON: name, subtitle, keywords, description, ...
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (app_id, locale)
);

-- Builds started in the background (flutter / xcodebuild / fastlane / custom) and where their log is.
CREATE TABLE build_jobs (
    id           INTEGER PRIMARY KEY,
    app_id       INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,                -- flutter | xcode | fastlane | custom
    command      TEXT NOT NULL,
    cwd          TEXT NOT NULL,
    log_path     TEXT NOT NULL,
    pid          INTEGER,
    status       TEXT NOT NULL DEFAULT 'running', -- running | ok | failed | stopped
    exit_code    INTEGER,
    started_at   TEXT NOT NULL,
    finished_at  TEXT
);
CREATE INDEX build_jobs_by_app ON build_jobs (app_id, id);

-- What App Review said, as pasted by the user (the API does not expose Resolution Center messages).
CREATE TABLE rejections (
    id             INTEGER PRIMARY KEY,
    app_id         INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    version        TEXT,
    guideline      TEXT,
    text           TEXT NOT NULL,
    submission_id  TEXT,
    received_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    resolved_at    TEXT
);
CREATE INDEX rejections_by_app ON rejections (app_id, id);
