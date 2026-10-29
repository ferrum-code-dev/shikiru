DELETE FROM quotas
WHERE (agent = 'claude' AND window IN ('five_hour', 'seven_day'))
   OR (agent = 'codex' AND window IN ('primary', 'secondary'));
