CREATE TABLE IF NOT EXISTS check_repair_rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    pull_request_url TEXT NOT NULL,
    head_sha TEXT NOT NULL,
    failures TEXT NOT NULL,
    stage TEXT NOT NULL,
    verified_sha TEXT,
    published_sha TEXT,
    UNIQUE (task_id, pull_request_url, head_sha)
);

CREATE INDEX IF NOT EXISTS check_repair_rounds_task ON check_repair_rounds (task_id, pull_request_url);
