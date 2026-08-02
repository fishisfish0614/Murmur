#!/usr/bin/env python3
"""SessionEnd hook example — the "sleep on it" decision.

When a session ends GRACEFULLY, someone must decide which unresolved feelings
survive into the next session. Default rule: any carry-eligible dimension
(restore: carry_if_interrupted) at or above 0.3 is worth sleeping on — the
matter isn't settled, and the next instance should still care. Below 0.3, a
proper goodbye lets small things go.

Interrupted sessions never reach this hook; the engine keeps everything as-is
for them (carry_if_interrupted does what it says).

For Claude Code, register under hooks.SessionEnd. For any other loop, call it
when your session closes cleanly.
"""
import json
import urllib.request

ENGINE = "http://127.0.0.1:8020"
CARRY_THRESHOLD = 0.3

try:
    state = json.loads(urllib.request.urlopen(
        f"{ENGINE}/emotion/state", timeout=2).read())
    # Ask the engine which dims are even eligible, then apply the threshold.
    # (Eligibility lives in the config; policy lives here — keep them separate.)
    carry = [d for d, v in state["dimensions"].items() if v >= CARRY_THRESHOLD]
    body = json.dumps({"end_type": "graceful", "carry": carry}).encode()
    req = urllib.request.Request(f"{ENGINE}/emotion/snapshot", data=body,
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=2)
except Exception:
    pass  # engine down → session ends anyway, engine-side restore covers it
