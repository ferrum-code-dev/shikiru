CREATE TABLE IF NOT EXISTS check_feedback_rounds (
    check_round_id INTEGER PRIMARY KEY REFERENCES check_repair_rounds(id),
    feedback_round_id INTEGER NOT NULL UNIQUE REFERENCES feedback_rounds(id)
);
