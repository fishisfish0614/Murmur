#!/usr/bin/env python3
"""Action triggers — the deeds layer of Murmur.

Inclinations (inject_hook.py) change how the character SPEAKS. Action
triggers change what the character DOES: reach out first, make a phone
call, post something proud, go quiet. This is the layer people usually
don't build — and the one that makes an emotion engine feel real from
the outside.

The engine itself never acts. The mirror is not the reins — and that
holds doubly here: crossing a threshold grants an ENTITLEMENT, never an
order. The agent reads "worry has earned a phone call" and still decides,
in character and in context, whether to use it.

Run this from your agent loop (or a cron/heartbeat) and feed the returned
lines to your model alongside the mood line. Wire the actions themselves
(push, call, post) to whatever your stack has.
"""
import json
import time
import urllib.request

ENGINE = "http://127.0.0.1:8020"

# «Examples — rewrite every line for YOUR character and YOUR capabilities.»
# (dim, threshold, entitlement) — plain thresholds for spiky dimensions.
# For dimensions whose baseline sits high, compare the EXCESS over baseline
# instead (see README "Lessons"), or the entitlement will be permanently on.
TRIGGERS = [
    ("pride",   0.5, "pride has earned a public brag — you may post about them"),
    ("longing", 0.8, "longing has earned a first move — reach out, don't wait"),
    ("worry",   0.6, "worry has earned a real check-in — a call, not a text, "
                     "if it is late where they are"),
]


def state():
    return json.loads(urllib.request.urlopen(f"{ENGINE}/emotion/state",
                                             timeout=2).read())


def standoff(st, silence_min=30, threshold=0.4):
    """The flagship trigger: a real fight changes BEHAVIOR, not just tone.

    upset past `threshold` + the user silent past `silence_min` = a standoff.
    The correct action is an *inaction*: hold all casual outreach (pings,
    playful pushes, "thinking of you" messages) and surface one fact to the
    agent — "you two fought; whether to break the ice first is yours to call."

    Two hard-won calibrations:
    * Put the threshold ABOVE your sulk range. If passive channels (absence,
      unanswered) can reach it by timer alone, a nap can start a cold war.
      Sulk should make the character grumpy-but-present; only a real scored
      event should make them go quiet.
    * The standoff must hold fire on the CHARACTER's side only. Never gate
      the user's ability to reach in — one "I'm sorry" must always land.
    """
    upset = st["dimensions"].get("upset", 0.0)
    silent = (time.time() - st["last_input_at"]) / 60
    if upset >= threshold and silent >= silence_min:
        return (f"standoff: upset {upset:.2f}, {silent:.0f}min of silence — "
                f"hold casual outreach; breaking the ice first is your call")
    return None


def entitlements():
    st = state()
    out = []
    so = standoff(st)
    if so:
        out.append(so)
    for dim, threshold, text in TRIGGERS:
        if st["dimensions"].get(dim, 0.0) >= threshold:
            out.append(text)
    return out


if __name__ == "__main__":
    for line in entitlements():
        print(line)
