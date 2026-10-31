CREATE TABLE IF NOT EXISTS push_targets (
    base TEXT PRIMARY KEY,
    registered INTEGER NOT NULL,
    pushed INTEGER NOT NULL,
    due_at TEXT,
    last_pushed_at TEXT
)
