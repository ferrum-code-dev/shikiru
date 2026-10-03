from __future__ import annotations

REAL_AGENT = "codex"
REAL_MODEL = "gpt-6-luna"
REAL_EFFORT = "low"
REAL_ROLE_LADDERS = {
    role: ({"agent": REAL_AGENT, "model": REAL_MODEL, "effort": REAL_EFFORT},)
    for role in ("leader", "implementer", "helper")
}
