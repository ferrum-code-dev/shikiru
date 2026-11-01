CREATE TABLE IF NOT EXISTS agent_block_times (
    agent TEXT PRIMARY KEY,
    blocked_at TEXT NOT NULL
);
INSERT OR IGNORE INTO agent_block_times (agent, blocked_at)
SELECT agent, strftime('%Y-%m-%dT%H:%M:%f000+00:00', 'now') FROM agent_blocks
