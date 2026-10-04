ALTER TABLE runs ADD COLUMN billing_mode TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE runs ADD COLUMN billing_source TEXT NOT NULL DEFAULT 'unknown';
