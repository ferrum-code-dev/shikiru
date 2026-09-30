ALTER TABLE tasks ADD COLUMN complexity_explicit INTEGER NOT NULL DEFAULT 0;

UPDATE tasks SET complexity_explicit = 1 WHERE complexity != 'normal'
