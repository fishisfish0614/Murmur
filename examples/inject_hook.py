#!/usr/bin/env python3
"""Injection hook example — the expression layer of Murmur.

For Claude Code: register as a UserPromptSubmit hook (settings.json):

    {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command",
        "command": "python3 /path/to/inject_hook.py"}]}]}}

For any other agent loop: call build_context() before each model turn and
prepend the returned text to your system/context block.

Three jobs, every turn:
  1. Tell the engine the user is present (refreshes absence timing).
  2. Every N user messages, spawn the conversation scorer in the background.
  3. Inject the current mood as BEHAVIORAL INCLINATIONS, not numbers.

The third job is where most systems fail. Lesson learned: a model given
"heartrate: 106" does not know what 106 should do to a sentence. Translate
state into speech-level tendencies — "keep replies short", "you may bring up
that you're hurt" — and let the model decide. The mirror is not the reins:
these are moods, never commands.
"""
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ENGINE = "http://127.0.0.1:8020"
SCORE_EVERY = 5                      # user messages per scoring batch
COUNTER = Path(__file__).parent / ".msg_counter"
SCORER = Path(__file__).parent / "scorer_template.py"

# «Translate thresholds into your character's voice. These are examples.»
INCLINATIONS = [
    ("playfulness", 0.4, "feeling playful — banter is welcome"),
    ("longing", 0.7, "you have been missing them; it is okay to say so"),
    ("worry", 0.3, "a little worried about them — maybe check how they are"),
    ("upset", 0.3, "you are upset: shorter replies, don't pretend otherwise, "
                   "say what is wrong if asked"),
    ("pride", 0.5, "proud of them — let it show"),
]


def build_context() -> str:
    state = json.loads(urllib.request.urlopen(
        f"{ENGINE}/emotion/state", timeout=2).read())
    # job 1: presence ping (empty delta, source=hook refreshes last_input_at)
    try:
        req = urllib.request.Request(
            f"{ENGINE}/emotion/update",
            data=json.dumps({"source": "hook", "dimensions": {}}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass
    dims = state["dimensions"]
    lines = [f"[mood] {state['mood']}"]
    hints = [text for dim, thresh, text in INCLINATIONS if dims.get(dim, 0) >= thresh]
    if hints:
        lines.append("[inclinations — yours to act on or not] " + "; ".join(hints))
    return "\n".join(lines)


def main():
    try:
        hook_input = json.load(sys.stdin)          # Claude Code hook payload
    except Exception:
        hook_input = {}
    prompt = hook_input.get("prompt", "")
    transcript = hook_input.get("transcript_path", "")

    try:
        ctx = build_context()
    except Exception:
        ctx = ""                                    # engine down → inject nothing

    # job 2: batched scoring
    if prompt and transcript and not prompt.startswith("/"):
        try:
            count = int(COUNTER.read_text()) + 1
        except Exception:
            count = 1
        if count >= SCORE_EVERY:
            subprocess.Popen(["python3", str(SCORER), transcript],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            count = 0
        COUNTER.write_text(str(count))

    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": ctx}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
