from __future__ import annotations

import os

REAL_AGENT = os.environ.get("SHIKIRU_E2E_AGENT", "codex")
REAL_MODELS = {"codex": "gpt-6-luna", "muse": "muse-spark-1.2"}
REAL_MODEL = REAL_MODELS[REAL_AGENT]
REAL_EFFORT = "low"
REAL_ROLE_LADDERS = {
    role: ({"agent": REAL_AGENT, "model": REAL_MODEL, "effort": REAL_EFFORT},)
    for role in ("leader", "implementer", "helper")
}
