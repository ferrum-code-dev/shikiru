CREATE TABLE IF NOT EXISTS feedback_descriptions (
    round_id INTEGER PRIMARY KEY REFERENCES feedback_rounds(id),
    description TEXT NOT NULL
);
