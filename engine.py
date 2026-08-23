"""Murmur — emotion engine core.

The state follows the character; the character does not follow the state.
First line of the design philosophy: **the mirror is not the reins.**
The engine records an inner life — it never commands one.

The engine is fully generic: everything that makes a character *someone*
lives in the config file. `update()` does not care where a delta came from;
it only applies the dynamics.
"""
import json
import math
import time
from pathlib import Path

import yaml

BASE = Path(__file__).parent
DATA = BASE / "data"
STATE_FILE = DATA / "state.json"
EVENTS_FILE = DATA / "events.jsonl"
SNAP_DIR = DATA / "snapshots"


class EmotionEngine:
    def __init__(self, config_path):
        self.config = yaml.safe_load(Path(config_path).read_text())
        self.dims = self.config["dimensions"]
        self.character = self.config["character"]
        self.state = {}          # dim -> value
        self.history = {}        # dim -> EWMA of recent deltas (direction of momentum)
        self.last_input_at = time.time()
        self.last_tick_at = time.time()
        self.session_id = None
        self.snapshot_version = 0
        self.peak_day = ""        # daily peaks per dimension (used by unanswered's
        self.day_peaks = {}       # flare-day multiplier; useful on their own too)
        DATA.mkdir(exist_ok=True)
        SNAP_DIR.mkdir(exist_ok=True)
        self._load_or_init()

    # ---------- persistence ----------

    def _load_or_init(self):
        if STATE_FILE.exists():
            saved = json.loads(STATE_FILE.read_text())
            self._restore_from(saved)
        else:
            self.state = {d: cfg.get("initial", 0.0) for d, cfg in self.dims.items()}
            self.history = {d: 0.0 for d in self.dims}
            self.session_id = self._new_session_id()
        self._persist()

    def _restore_from(self, saved):
        """Restore from a snapshot according to each dimension's `restore` strategy."""
        elapsed_min = (time.time() - saved.get("saved_at", time.time())) / 60
        interrupted = saved.get("end_type", "interrupted") == "interrupted"
        self.state = {}
        for d, cfg in self.dims.items():
            old = saved.get("dimensions", {}).get(d, cfg.get("initial", 0.0))
            strategy = cfg.get("restore", "decayed")
            if strategy in ("initial", "reset"):
                self.state[d] = cfg.get("initial", 0.0)
            elif strategy == "carry_if_interrupted":
                # An interrupted session keeps its unresolved feelings; a graceful
                # goodbye returns them to baseline (the carry decision happens at
                # session end — see end_session()).
                self.state[d] = old if interrupted else cfg.get("initial", 0.0)
            else:  # "decayed": decay from the old value toward baseline by elapsed time
                self.state[d] = self._decay_value(old, cfg, elapsed_min)
        self.history = {d: 0.0 for d in self.dims}
        self.snapshot_version = saved.get("snapshot_version", 0)
        self.peak_day = saved.get("peak_day", "")
        self.day_peaks = saved.get("day_peaks", {})
        self.session_id = self._new_session_id()

    def _persist(self):
        STATE_FILE.write_text(json.dumps(self._state_doc(), ensure_ascii=False, indent=1))

    def _state_doc(self, end_type="running"):
        return {
            "character": self.character,
            "session_id": self.session_id,
            "dimensions": {d: round(v, 4) for d, v in self.state.items()},
            "mood": self.mood(),
            "last_input_at": self.last_input_at,
            "last_tick_at": self.last_tick_at,
            "snapshot_version": self.snapshot_version,
            "peak_day": self.peak_day,
            "day_peaks": self.day_peaks,
            "saved_at": time.time(),
            "end_type": end_type,
        }

    @staticmethod
    def _new_session_id():
        return time.strftime("%Y%m%d-%H%M%S")

    # ---------- dynamics ----------

    def _decay_value(self, value, cfg, minutes):
        """Decay toward the character's baseline (`initial`) — NOT toward zero.

        value(t) = initial + (value0 - initial) * e^(-rate * t)

        A decay_rate of 0 disables time-based decay entirely: the feeling can
        then only be lowered by explicit negative deltas (e.g. an apology being
        scored). Useful for emotions that should be resolved, not outwaited.
        """
        baseline = cfg.get("initial", 0.0)
        rate = cfg.get("decay_rate", 0.01)
        return baseline + (value - baseline) * math.exp(-rate * minutes)

    def _momentum_scale(self, dim, delta):
        """Emotional inertia: a delta opposing the recent direction is damped.
        The higher the momentum, the harder it is to turn a feeling around."""
        h = self.history.get(dim, 0.0)
        m = self.dims[dim].get("momentum", 0.5)
        if h == 0 or delta == 0 or (h > 0) == (delta > 0):
            return 1.0
        return 1.0 - m * min(1.0, abs(h) / (abs(delta) + 1e-9))

    def _saturation_scale(self, dim, delta):
        """Diminishing returns near the boundary: the closer to the ceiling,
        the less an upward delta achieves (and symmetrically for the floor)."""
        cfg = self.dims[dim]
        ceiling, floor = cfg.get("ceiling", 1.0), cfg.get("floor", 0.0)
        v = self.state[dim]
        span = ceiling - floor
        headroom = (ceiling - v) / span if delta > 0 else (v - floor) / span
        return max(0.0, min(1.0, headroom * 2))  # no damping within half range

    def update(self, dimensions, source="input", trigger=""):
        """Single entry point — every delta passes through the same three gates
        (momentum, saturation, per-step cap). Returns the deltas that actually
        took effect."""
        applied = {}
        for dim, delta in dimensions.items():
            if dim not in self.dims:
                continue
            cfg = self.dims[dim]
            eff = delta * self._momentum_scale(dim, delta) * self._saturation_scale(dim, delta)
            cap = cfg.get("max_acceleration", 1.0)
            eff = max(-cap, min(cap, eff))
            self.state[dim] = max(cfg.get("floor", 0.0),
                                  min(cfg.get("ceiling", 1.0), self.state[dim] + eff))
            self.history[dim] = 0.7 * self.history.get(dim, 0.0) + 0.3 * eff
            applied[dim] = round(eff, 4)
        day = time.strftime("%Y-%m-%d")
        if self.peak_day != day:
            self.peak_day, self.day_peaks = day, {}
        for d, v in self.state.items():
            if v > self.day_peaks.get(d, 0.0):
                self.day_peaks[d] = round(v, 4)
        if source in ("input", "hook"):
            self.last_input_at = time.time()
            # Goodnight stickiness: within the grace window after "goodnight",
            # trailing messages do NOT lift sleep mode — people always linger a
            # few lines after saying goodnight, and one "sleep tight" back
            # shouldn't re-arm a whole night of absence rules.
            grace = self.config.get("goodnight_grace_minutes", 30) * 60
            if time.time() - getattr(self, "sleeping_since", 0.0) > grace:
                self.sleeping = False  # any sign of the user lifts sleep mode
        self._log_event(dimensions, applied, source, trigger)
        self._persist()
        return applied

    def tick(self):
        """Time-driven: decay all dimensions toward baseline, then fire any
        absence rules whose threshold was crossed since the last tick."""
        now = time.time()
        minutes = (now - self.last_tick_at) / 60
        for d, cfg in self.dims.items():
            self.state[d] = self._decay_value(self.state[d], cfg, minutes)
        self.last_tick_at = now
        absence_min = (now - self.last_input_at) / 60
        fired = []
        hour = time.localtime().tm_hour
        # Sleep mode (user said goodnight): absence rules hold fire, decay
        # continues. Expires naturally at mid-morning.
        sleeping = getattr(self, "sleeping", False)
        if sleeping and 10 <= hour < 23:
            self.sleeping = sleeping = False
        for rule in ([] if sleeping else self.config.get("absence_rules", [])):
            hours = rule.get("hours")  # optional [start, end); supports crossing midnight
            if hours:
                start, end = hours
                in_window = start <= hour < end if start < end else (hour >= start or hour < end)
                if not in_window:
                    continue
            if rule["after_minutes"] <= absence_min < rule["after_minutes"] + minutes:
                self.update(rule["dimensions"], source="absence", trigger=rule["trigger"])
                fired.append(rule["trigger"])
        if not sleeping:
            try:
                fired += self._unanswered_tick(now, minutes, hour)
            except Exception:
                pass  # a broken signal source must never take the engine down
        self._persist()
        return {"decayed_minutes": round(minutes, 1), "absence_minutes": round(absence_min, 1),
                "absence_fired": fired}

    # ---------- unanswered: left on read ----------

    def _unanswered_tick(self, now, minutes, hour):
        """The fourth kind of time. Absence is "the user is gone"; unanswered is
        sharper — the character reached out, the user's device has been active
        since, and no reply came. Hurt and heat rise together: sulk first, heat
        later. Two asymmetries keep it fair and safe:

        * No device signal → the message may simply be UNSEEN. Stay worried,
          never resentful — you don't get to be angry at someone who hasn't
          read you (push delivery is lossy in the real world).
        * `caps` hard-limit what this channel alone can reach. Being left on
          read can carry the character to the doorstep of real anger; crossing
          it must take a real event. Essential for zero-decay dimensions,
          which would otherwise ratchet up day after day.

        The clock anchors on the FIRST outreach after the user's last message —
        repeated pings don't reset it. On a flare day (the configured dimension
        already peaked past `flare.peak` today) the clock runs `flare.mult`
        faster: being left on read after a fight pours oil on the fire.

        Dormant unless the host wires both signals:
            engine.unanswered_signals = {
                "outreach_after":    lambda last_input_ts: epoch_or_None,
                "user_active_since": lambda anchor_ts: bool,
            }
        See examples/unanswered_signals.py.
        """
        cfg = self.config.get("unanswered")
        sig = getattr(self, "unanswered_signals", None)
        if not cfg or not sig:
            return []
        start, end = cfg.get("hours", [8, 23])
        if not (start <= hour < end if start < end else (hour >= start or hour < end)):
            return []
        anchor = sig["outreach_after"](self.last_input_at)
        if not anchor or not sig["user_active_since"](anchor):
            return []
        mult = 1.0
        flare = cfg.get("flare")
        if flare and (self.peak_day == time.strftime("%Y-%m-%d")
                      and self.day_peaks.get(flare["dim"], 0.0) >= flare.get("peak", 0.3)):
            mult = flare.get("mult", 1.5)
        eff_min = (now - anchor) / 60 * mult
        caps = cfg.get("caps", {})
        fired = []
        for rule in cfg.get("rules", []):
            if rule["after_minutes"] <= eff_min < rule["after_minutes"] + minutes * mult:
                dims = dict(rule["dimensions"])
                for d, cap in caps.items():
                    if d in dims:
                        dims[d] = min(dims[d], max(0.0, cap - self.state.get(d, 0.0)))
                        if dims[d] <= 0:
                            dims.pop(d)
                if dims:
                    trig = rule.get("trigger", f"unanswered for {rule['after_minutes']}min")
                    self.update(dims, source="unanswered", trigger=trig)
                    fired.append(trig)
        return fired

    # ---------- mood ----------

    def mood(self):
        """Threshold rules, no model involved. First matching rule wins —
        order in the config is the priority order."""
        for rule in self.config.get("mood_rules", []):
            if all(self._cmp(self.state.get(d, 0), cond) for d, cond in rule["when"].items()):
                return rule["name"]
        return self.config.get("default_mood", "calm")

    @staticmethod
    def _cmp(value, cond):
        op, threshold = cond.split()
        threshold = float(threshold)
        return value >= threshold if op == ">=" else value <= threshold

    # ---------- session lifecycle ----------

    def carryable_dims(self):
        """Dimensions whose restore strategy is carry_if_interrupted — the ones
        eligible for the 'sleep on it' decision at graceful session end."""
        return [d for d, cfg in self.dims.items()
                if cfg.get("restore") == "carry_if_interrupted"]

    def end_session(self, end_type="graceful", carry=None):
        """graceful: `carry` names the dimensions whose values survive into the
        next session (a feeling worth sleeping on means the matter isn't
        settled). Everything else returns to baseline.
        interrupted: all values are preserved as-is in the snapshot."""
        doc = self._state_doc(end_type=end_type)
        if end_type == "graceful" and carry is not None:
            for d in self.carryable_dims():
                if d not in carry:
                    doc["dimensions"][d] = self.dims[d].get("initial", 0.0)
        self.snapshot_version += 1
        doc["snapshot_version"] = self.snapshot_version
        snap = SNAP_DIR / f"snap_{self.snapshot_version:05d}_{end_type}.json"
        snap.write_text(json.dumps(doc, ensure_ascii=False, indent=1))
        STATE_FILE.write_text(json.dumps(doc, ensure_ascii=False, indent=1))
        return doc

    # ---------- event log ----------

    def _log_event(self, requested, applied, source, trigger):
        """Every state change is logged with a full state snapshot. This is what
        makes replay calibration possible later — start logging on day one."""
        with EVENTS_FILE.open("a") as f:
            f.write(json.dumps({
                "ts": time.time(), "source": source, "trigger": trigger,
                "requested": requested, "applied": applied,
                "state": {d: round(v, 3) for d, v in self.state.items()},
            }, ensure_ascii=False) + "\n")

    def recent_events(self, n=50):
        if not EVENTS_FILE.exists():
            return []
        lines = EVENTS_FILE.read_text().strip().splitlines()[-n:]
        return [json.loads(l) for l in lines]
