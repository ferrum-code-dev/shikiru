DELETE FROM agent_block_times WHERE agent NOT IN (SELECT agent FROM agent_blocks);
INSERT OR IGNORE INTO agent_block_times (agent, blocked_at)
SELECT agent, strftime('%Y-%m-%dT%H:%M:%f000+00:00', 'now') FROM agent_blocks
