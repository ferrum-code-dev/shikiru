CREATE TABLE IF NOT EXISTS model_context_windows (
    agent TEXT NOT NULL,
    model TEXT NOT NULL,
    tokens INTEGER NOT NULL,
    PRIMARY KEY (agent, model)
);
