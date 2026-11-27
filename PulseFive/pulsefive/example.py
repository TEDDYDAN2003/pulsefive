"""Deterministic execution examples; injected signal, illustrative prices, no market evidence."""

from datetime import UTC, datetime

from .engine import Engine, Memory
from .model import Rules
from .paper import Paper
from .policy import GOLD, Feature, Quote

CASES = ("target", "partial", "nonfill", "late-fill", "reversal", "target-missed")


def run(case):
    if case not in CASES:
        raise ValueError(case)
    p = Paper([GOLD])
    j = Memory()
    e = Engine([GOLD], p, j, Rules("baseline"))
    start = int(datetime(2026, 11, 3, 12, 12, 15, tzinfo=UTC).timestamp() * 1000)
    name = GOLD.name
    for age in range(0, 32000, 50):
        bid = 2000.0
        eff = 0.80
        side = 1
        move = 0.42
        if age >= 400:
            if case in ("target", "target-missed"):
                bid = (
                    1999.82
                    if age < 800
                    else (
                        1999.88 if case == "target-missed" and age >= 900 else 2000.90
                    )
                )
            elif case == "partial":
                bid = 1999.935 if age < 800 else 2003.30
            elif case == "nonfill":
                bid = 2000.65
            elif case == "late-fill":
                if age < 450:
                    bid = 2000.65
                    eff = -0.8
                    move = -0.40
                    side = 0
                else:
                    bid = 1999.82 if age < 850 else 1999.35
                    eff = -0.8
                    move = -0.40
                    side = 0
            else:
                bid = 1999.82 if age < 800 else 1999.24
                eff = -0.8
                move = -0.40
                side = 0
        q = Quote(start + age, bid, round(bid + 0.24, 3))
        f = Feature(0.65, 2.0, 2.1, eff, move, 0.45, 0.05, side, 2.3 if side else 0.0)
        p.arrive(name, q, q.ms)
        t = e.tapes[name]
        t.q = q
        t.f = f
        e.pump(q.ms, [(f.score, name)] if age == 0 else ())
        if e.s.get("hold"):
            raise RuntimeError(e.s["hold"])
        if age and not e.s.get("batch"):
            break
    settled = [r for r in j.events if r["event"] == "settled"]
    assert len(settled) == 1
    return dict(
        synthetic=True,
        case=case,
        settled=settled[0],
        cash=round(p.balance, 4),
        server_events=p.counts,
        events=j.events,
    )
