"""Small, inspectable expected-value model. Inference needs no ML runtime."""

import hashlib
import json
import math
from pathlib import Path

from .policy import GOLD
from .sessions import NAMES, session

FEATURES = (
    "range_ratio",
    "activity",
    "directional_efficiency",
    "move_over_spread",
    "spread_over_range",
    "base_range",
    "candle_progress",
    "direction",
)
STYLES = ("shallow", "patient")
FROZEN_SHA = "14ee65478a3987cec09e12680f4d09beb2fac1a82fcb2791f2ba0e91772c53f9"


def vector(sp, q, f):
    spread = max(q.spread, sp.tick)
    return [
        math.log1p(min(10.0, f.ratio)),
        math.log1p(min(12.0, f.pace)),
        abs(f.efficiency),
        math.log1p(min(15.0, abs(f.move) / spread)),
        min(2.0, spread / max(f.base, sp.tick)),
        math.log1p(min(25.0, f.base)),
        (q.ms % 60000) / 60000.0,
        float(f.side),
    ]


def predict(model, x):
    return model["intercept"] + sum(
        w * (v - m) / s
        for w, v, m, s in zip(
            model["coef"], x, model["mean"], model["scale"], strict=True
        )
    )


class Rules:
    def __init__(self, mode="release", artifact=None, style="shallow"):
        if mode not in ("release", "challenger", "baseline", "label"):
            raise ValueError("Invalid profile")
        self.mode = mode
        self.style = style
        self.artifact = artifact
        if mode in ("release", "challenger"):
            if artifact is None:
                raw = (Path(__file__).parent / "model_v1.json").read_bytes()
                if hashlib.sha256(raw).hexdigest() != FROZEN_SHA:
                    raise ValueError("Built-in frozen policy changed; refusing to run")
                artifact = json.loads(raw)
                self.artifact = artifact
            if (
                artifact.get("schema") != "pulsefive-session-ridge-1"
                or list(FEATURES) != artifact.get("features")
                or set(artifact.get("sessions", {})) != set(NAMES)
            ):
                raise ValueError("Incompatible session artifact")

    def bucket(self, ms):
        return session(ms)

    def decide(self, sp, q, f):
        k = session(q.ms)
        if (
            not k
            or sp.name != GOLD.name
            or sp.contract != 100
            or abs(sp.tick - 0.001) > 1e-12
        ):
            return None
        if self.mode in ("label", "baseline"):
            return self.style, 0.0
        m = self.artifact["sessions"][k]
        if self.mode == "release" and not m["approved"]:
            return None
        if not m.get("fit"):
            return None
        y = predict(m["fit"], vector(sp, q, f))
        return (m["style"], y) if math.isfinite(y) and y >= m["threshold"] else None
