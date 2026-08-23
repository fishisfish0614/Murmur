"""Wiring template for the `unanswered` (left-on-read) channel.

The engine deliberately knows nothing about YOUR outreach log or YOUR
presence source — you hand it two callables and it stays generic:

    engine.unanswered_signals = {
        # Earliest time the character reached out AFTER the user's last
        # message (epoch seconds), or None if they haven't. This anchors the
        # clock: repeated pings must NOT reset it.
        "outreach_after": outreach_after,

        # Has the user's device/presence shown any activity since `anchor`?
        # False also on missing data — "no signal" must read as "maybe
        # unseen", never as "ignoring me".
        "user_active_since": user_active_since,
    }

Both examples below are honest, boring implementations against plain files.
Replace them with whatever you actually have: a push-notification log, an
app-usage tracker, a smart-home presence sensor, a calendar.
"""
import json
import time
from datetime import datetime
from pathlib import Path

# Wherever your agent records its outgoing messages, one JSON per line:
#   {"ts": "2026-08-23T18:24:47+02:00", "message": "..."}
OUTREACH_LOG = Path("data/outreach_log.jsonl")

# Wherever your presence source drops its last-activity timestamp:
#   {"last_active_ts": 1787465706.0}
PRESENCE_FILE = Path("data/presence.json")


def outreach_after(last_input_ts):
    """Earliest outreach after the user's last message, or None."""
    try:
        lines = OUTREACH_LOG.read_text().splitlines()[-200:]
    except OSError:
        return None
    best = None
    for line in lines:
        try:
            t = datetime.fromisoformat(json.loads(line)["ts"]).timestamp()
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
        if t > last_input_ts and (best is None or t < best):
            best = t
    return best


def user_active_since(anchor_ts):
    """True if the user's device showed activity after the outreach."""
    try:
        last = json.loads(PRESENCE_FILE.read_text())["last_active_ts"]
    except (OSError, ValueError, KeyError):
        return False  # no data = maybe unseen = stay kind
    return float(last) > anchor_ts


if __name__ == "__main__":
    # Smoke test against your own files:
    a = outreach_after(time.time() - 3600)
    print("anchor:", a, "| user active since:", user_active_since(a) if a else "n/a")
