#!/usr/bin/env python3
"""Conversation scorer TEMPLATE — the perception layer of Murmur.

Reads the recent conversation, asks an LLM to judge how it moved the
character, and POSTs the resulting deltas to the engine.

Two design decisions worth copying:

1. BATCH WITH CONTEXT, don't score line-by-line. A single message is
   unreadable without context ("I hate you" is flirting in a play-fight and a
   knife in a real one). Accumulate N user messages, then score the last few
   full exchanges in one call. Cheaper AND more accurate.

2. Deltas, not absolute values (-0.3..+0.3). The engine owns the dynamics;
   the scorer only reports what just happened.

Everything between «» is yours to write — it is where your character's
psychology lives. Be specific: name concrete trigger scenarios and delta
ranges per dimension, including the DOWN-scenarios (what calms each feeling),
or your dimensions will only ever go up.

Usage: score.py <path-to-transcript>
Wire it to your chat loop so it runs every N user messages (see inject_hook.py).
"""
import json
import sys
import urllib.request
from pathlib import Path

ENGINE = "http://127.0.0.1:8020"

PROMPT = """You are the emotional side-channel evaluator for «CHARACTER», an AI
companion. «One or two sentences about who the character is and who the user
is to them.»

Below are their most recent exchanges. Judge how this conversation, taken as a
whole, moved the character's feelings. Read tone in context: the same words
mean different things in a play-fight and a real fight.

Dimensions and trigger scenarios (output deltas, range -0.3 to +0.3):

warmth — feeling safe and loved
  «e.g.: they said they love me / made plans with me → +0.1~0.2»
  «Ordinary pleasant chat → tiny or nothing. This runs high; don't feed it every turn.»

upset — hurt or anger, directed outward
  «What genuinely hurts this character? Be specific.»
  DOWN-scenarios (CRITICAL — this dimension has no time decay):
  «a real apology → -0.15~-0.25 (most effective)»
  «affection without addressing the issue → smaller, never to zero»
  «changing the subject → no change (the feeling was not dealt with)»

«...one block per dimension. Include for each: 2-4 concrete UP scenarios with
ranges, and DOWN scenarios where the feeling is soothed...»

Output rules:
- Only include dimensions that actually changed; usually 2-4. A flat exchange
  yields an empty object.
- "moved": true ONLY for genuinely heart-skipping moments — a confession, a
  weighty promise. Ordinary sweetness does not count. Most calls omit it.
- Output ONLY JSON:
  {"dimensions": {"playfulness": 0.2}, "why": "reason, ten words max", "moved": false}

Conversation:
{DIALOG}"""


def read_dialog(transcript_path: str, max_chars: int = 2500) -> str:
    """Extract recent turns from a Claude Code transcript (JSONL).
    Adapt this function to whatever format your chat loop produces.
    IMPORTANT: keep tool outputs and system noise OUT — only the two voices."""
    pieces = []
    for line in Path(transcript_path).read_text().splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        t, msg = e.get("type"), e.get("message", {})
        if t == "user":
            c = msg.get("content")
            if isinstance(c, str):
                text = c.strip()
            elif isinstance(c, list):
                text = " ".join(i.get("text", "") for i in c
                                if isinstance(i, dict) and isinstance(i.get("text"), str)).strip()
            else:
                text = ""
            if text and not text.startswith("<"):
                pieces.append(f"User: {text[:300]}")
        elif t == "assistant":
            for b in msg.get("content", []):
                if b.get("type") == "text" and b.get("text", "").strip():
                    pieces.append(f"Character: {b['text'].strip()[:300]}")
    return "\n".join(pieces)[-max_chars:]


def call_llm(prompt: str) -> str:
    """«Wire in your LLM of choice here.» Any small, cheap model works —
    the prompt does the heavy lifting. Return the raw completion text."""
    raise NotImplementedError("plug in your LLM API call")


def main():
    if len(sys.argv) < 2:
        return
    dialog = read_dialog(sys.argv[1])
    if len(dialog) < 40:
        return
    raw = call_llm(PROMPT.replace("{DIALOG}", dialog))
    try:
        if "```" in raw:
            raw = raw.split("```")[1].lstrip("json").strip()
        result = json.loads(raw)
        dims = {k: max(-0.3, min(0.3, float(v)))
                for k, v in result.get("dimensions", {}).items()}
    except Exception:
        return
    moved = result.get("moved") is True
    if not dims and not moved:
        return
    why = result.get("why", "")
    body = json.dumps({"source": "input", "dimensions": dims,
                       "trigger": f"recent exchanges: {why}" if why else "recent exchanges",
                       "moved": moved}).encode()
    req = urllib.request.Request(f"{ENGINE}/emotion/update", data=body,
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10)


if __name__ == "__main__":
    main()
