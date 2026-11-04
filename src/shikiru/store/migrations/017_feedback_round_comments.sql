CREATE TABLE IF NOT EXISTS feedback_round_comments (
    round_id INTEGER NOT NULL REFERENCES feedback_rounds(id),
    comment_id TEXT NOT NULL,
    PRIMARY KEY (round_id, comment_id)
);
