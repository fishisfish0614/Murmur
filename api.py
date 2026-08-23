"""Murmur REST API — bind to localhost; put auth in front before exposing it.

Vitals (heart rate etc.) are DERIVED, never stored: computed from the current
emotional state at read time. They can never disagree with the state, survive
restarts by re-derivation, and cost zero persistence.
"""
import asyncio
import json
import math
import random
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

from engine import EmotionEngine

CONFIG_PATH = Path(__file__).parent / "config.yaml"
if not CONFIG_PATH.exists():  # fall back to the example so `uvicorn api:app` just works
    CONFIG_PATH = Path(__file__).parent / "config.example.yaml"
engine = EmotionEngine(CONFIG_PATH)

TICK_SECONDS = 300  # built-in heartbeat: one tick every 5 minutes

_moved_at = 0.0  # most recent "moved" moment; the spike is transient by design
MOVED_LOG = Path(__file__).parent / "data" / "moved_log.jsonl"


async def _ticker():
    while True:
        await asyncio.sleep(TICK_SECONDS)
        engine.tick()


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(_ticker())
    yield
    task.cancel()
    # A service stop is an involuntary ending: unresolved feelings are kept.
    engine.end_session(end_type="interrupted")


app = FastAPI(title=f"Murmur ({engine.character})", lifespan=lifespan)


class Delta(BaseModel):
    source: str = "input"          # input | internal | absence
    dimensions: dict[str, float]
    trigger: str = ""
    moved: bool = False            # a heart-skipping moment; adds a transient spike


class EndSession(BaseModel):
    end_type: str = "graceful"
    carry: list[str] | None = None  # dims that survive a graceful goodbye


@app.post("/emotion/update")
def update(delta: Delta):
    global _moved_at
    if delta.moved:
        _moved_at = time.time()
        try:
            with MOVED_LOG.open("a") as f:
                f.write(json.dumps({"ts": _moved_at, "trigger": delta.trigger},
                                   ensure_ascii=False) + "\n")
        except Exception:
            pass  # failing to log must not break the spike itself
    applied = engine.update(delta.dimensions, source=delta.source, trigger=delta.trigger)
    return {"applied": applied, "state": engine.state, "mood": engine.mood()}


@app.get("/emotion/vitals")
def vitals():
    """Derived vitals, fully config-driven (see the `vitals:` section).

    The heart-rate formula weights each dimension's EXCESS over its baseline,
    not its absolute value. Lesson learned the hard way: some dimensions sit
    chronically high because that is who the character is — absolute-value
    formulas leave such characters permanently 'tachycardic'. A `floor` on a
    term goes further: only the part above the floor counts at all.
    """
    vcfg = engine.config.get("vitals")
    if not vcfg:
        return {"mood": engine.mood()}
    s = engine.state

    def excess(d):
        return max(0.0, s.get(d, 0.0) - engine.dims.get(d, {}).get("initial", 0.0))

    hr_cfg = vcfg.get("heartrate", {})
    contrib = {}
    for term in hr_cfg.get("terms", []):
        d = term["dim"]
        floor = term.get("floor")
        ex = max(0.0, s.get(d, 0.0) - floor) if floor is not None else excess(d)
        val = term.get("weight", 0) * ex + term.get("quadratic", 0) * ex * ex
        contrib[d] = contrib.get(d, 0.0) + val
    spike_cfg = hr_cfg.get("moved_spike", {})
    spike = 0.0
    if _moved_at and spike_cfg:
        spike = spike_cfg.get("amount", 30) * math.exp(
            -(time.time() - _moved_at) / spike_cfg.get("tau_seconds", 30))
        if spike < 0.5:
            spike = 0.0
    contrib["moved"] = spike
    baseline = hr_cfg.get("baseline", 66)
    jitter = hr_cfg.get("jitter", 3)
    heartrate = baseline + sum(contrib.values()) + random.uniform(-jitter, jitter)

    # Dominant cause (for whatever frontend you build). The threshold keeps a
    # chronically-slightly-elevated dimension from claiming the calm state.
    thresh = hr_cfg.get("dominance_threshold", 5.0)
    pos = {k: v for k, v in contrib.items() if v >= thresh}
    cause = max(pos, key=pos.get) if pos else "baseline"

    gauges = {}
    for g in vcfg.get("gauges", []):
        raw = s.get(g["dim"], 0.0) * g.get("scale", 100) + g.get("offset", 0)
        gauges[g["name"]] = round(max(g.get("min", 0), min(g.get("max", 100), raw)), 1)

    return {
        "heartrate": round(heartrate, 1),
        "cause": cause,
        "gauges": gauges,
        "contrib": {k: round(v, 1) for k, v in contrib.items()},
        "mood": engine.mood(),
    }


@app.get("/emotion/moved")
def moved_log(n: int = 20):
    """The spike is fleeting; the record stays."""
    if not MOVED_LOG.exists():
        return {"count": 0, "recent": []}
    lines = MOVED_LOG.read_text().strip().splitlines()
    recent = []
    for l in lines[-n:]:
        try:
            recent.append(json.loads(l))
        except Exception:
            continue
    return {"count": len(lines), "recent": recent}


@app.get("/emotion/state")
def state():
    return engine._state_doc()


@app.get("/emotion/baselines")
def baselines():
    """Each dimension's baseline (`initial`) — where every feeling returns
    when nothing touches it. Useful for drawing tick marks in a frontend."""
    return {d: cfg.get("initial", 0.0) for d, cfg in engine.dims.items()}


@app.get("/emotion/mood")
def mood():
    return {"mood": engine.mood()}


@app.get("/emotion/history")
def history(n: int = 50):
    return engine.recent_events(n)


@app.post("/emotion/sleep")
def sleep_mode():
    """Goodnight mode: absence rules hold fire until the user reappears
    (or mid-morning, whichever comes first). Decay continues as usual.
    `sleeping_since` powers goodnight stickiness (config
    goodnight_grace_minutes): trailing messages within the grace window
    don't lift sleep mode; saying goodnight again extends it."""
    import time as _t
    engine.sleeping = True
    engine.sleeping_since = _t.time()
    return {"sleeping": True}


@app.post("/emotion/tick")
def tick():
    return engine.tick()


@app.post("/emotion/snapshot")
def snapshot(req: EndSession):
    return engine.end_session(end_type=req.end_type, carry=req.carry)


@app.post("/emotion/restore")
def restore():
    engine._load_or_init()
    return engine._state_doc()
