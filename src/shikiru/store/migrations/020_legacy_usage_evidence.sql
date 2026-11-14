CREATE TABLE IF NOT EXISTS legacy_run_usage (
  run_id INTEGER PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  usage_reported INTEGER,
  input_tokens INTEGER,
  uncached_input_tokens INTEGER,
  output_tokens INTEGER,
  cache_read_tokens INTEGER,
  cache_write_tokens INTEGER
);

INSERT OR IGNORE INTO legacy_run_usage (
  run_id, source, usage_reported, input_tokens, uncached_input_tokens,
  output_tokens, cache_read_tokens, cache_write_tokens
)
SELECT id,
       CASE
         WHEN uncached_input_tokens IS NULL AND output_tokens IS NULL
           AND cache_read_tokens IS NULL AND cache_write_tokens IS NULL
         THEN 'previously_cleared_fields'
         ELSE 'previous_category_fields'
       END,
       usage_reported, NULL, uncached_input_tokens, output_tokens,
       cache_read_tokens, cache_write_tokens
FROM runs
WHERE usage_accounting = 'legacy';

UPDATE runs
SET uncached_input_tokens = NULL,
    output_tokens = NULL,
    cache_read_tokens = NULL,
    cache_write_tokens = NULL
WHERE usage_accounting = 'legacy';
