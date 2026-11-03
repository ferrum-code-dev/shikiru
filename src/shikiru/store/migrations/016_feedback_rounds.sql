CREATE TABLE IF NOT EXISTS feedback_rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    pull_request_url TEXT NOT NULL,
    review_ids TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    stage TEXT NOT NULL,
    analysis TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    blocked INTEGER NOT NULL DEFAULT 0,
    expected_remote_sha TEXT NOT NULL,
    verified_sha TEXT,
    published_sha TEXT,
    checks TEXT NOT NULL DEFAULT '',
    verified_replies TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS feedback_rounds_task ON feedback_rounds (task_id, pull_request_url);

CREATE TABLE IF NOT EXISTS feedback_replies (
    round_id INTEGER NOT NULL REFERENCES feedback_rounds(id),
    source_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    marker TEXT NOT NULL,
    body TEXT NOT NULL,
    state TEXT NOT NULL,
    posted_url TEXT,
    PRIMARY KEY (round_id, source_id)
);
