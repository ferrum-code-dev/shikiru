UPDATE runs
SET usage_accounting = 'legacy'
WHERE agent IN ('claude', 'muse') AND usage_accounting = 'categories';
