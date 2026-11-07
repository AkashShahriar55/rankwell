-- Replies to customer reviews sent through App Store Connect (one row per review; a new reply replaces it).
CREATE TABLE review_replies (
    review_id   TEXT PRIMARY KEY,
    app_id      INTEGER NOT NULL REFERENCES apps (id) ON DELETE CASCADE,
    rating      INTEGER,
    territory   TEXT,
    body        TEXT NOT NULL,
    state       TEXT,
    plan_id     TEXT,
    replied_at  TEXT NOT NULL
);
CREATE INDEX review_replies_by_app ON review_replies (app_id, replied_at);
