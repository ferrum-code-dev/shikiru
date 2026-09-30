UPDATE tasks
SET repo = json_set(repo, '$.base_ref', json_extract(repo, '$.base'))
WHERE repo IS NOT NULL AND json_extract(repo, '$.base_ref') IS NULL
