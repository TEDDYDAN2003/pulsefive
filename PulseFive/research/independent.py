"""Second full-stream path: use the scalar forward Tape & Engine.observe on every observed tick.
Compare actual ledgers, cash and tick-marked drawdown. No trained/research policy is changed.
"""

import argparse
import csv
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from pulsefive.data import arrays, manifest
from pulsefive.model import Rules
from pulsefive.policy import GOLD, POLL, Quote, Tape

from .ticklab import new_engine


def check(cache, study, stress):
    c = "stress" if stress else "base"
    path = study / ("test-" + c)
    art = json.loads((study / "frozen.json").read_text())
    modes = ["baseline", "challenger", "release"]
    eng = {m: new_engine(Rules(m, art), stress) for m in modes}
    controller = eng["baseline"]
    n = GOLD.name
    rawcount = obs = 0
    for key in sorted(k for k in manifest() if k.startswith("2026-11-")):
        ms, ba = arrays(cache, key)
        rawcount += len(ms)
        s = np.ceil((ba[:, 1] - ba[:, 0]) * 0.1).astype("int32") if stress else 0
        bid = (ba[:, 0] - s) / 1000.0
        ask = (ba[:, 1] + s) / 1000.0
        poll = (ms + POLL - 1) // POLL
        ends = np.r_[np.flatnonzero(poll[1:] != poll[:-1]), len(poll) - 1]
        obs += len(ends)
        last = -1
        pw = 0
        tape = Tape(GOLD)
        for e in eng.values():
            e.tapes[n] = tape
            e.seen.pop(n, None)
            e.edge_minute.pop(n, None)
        for it in ends:
            i = int(it)
            w = int(poll[i]) * POLL
            q = Quote(int(ms[i]), float(bid[i]), float(ask[i]))
            for e in eng.values():
                if not e.s.get("batch"):
                    continue
                for z in range(pw + POLL, w, POLL):
                    e.broker.advance(z)
                    e.pump(z)
                    if not e.s.get("batch"):
                        break
                for k in range(last + 1, i + 1):
                    tick = Quote(int(ms[k]), float(bid[k]), float(ask[k]))
                    e.broker.arrive(n, tick, tick.ms)
            edge = controller.observe(n, q)
            for e in eng.values():
                e.broker.last[n] = q
                if edge or e.s.get("batch"):
                    e.pump(w, [edge] if edge else ())
                if e.s.get("hold"):
                    raise RuntimeError(e.s["hold"])
            pw = w
            last = i
        if any(e.s.get("batch") for e in eng.values()):
            raise AssertionError("Unclosed batch at date end")
    reports = json.loads((path / "summary.json").read_text())
    assert rawcount == reports["raw_ticks"] and obs == reports["observed_ticks"]
    answer = {}
    for m, e in eng.items():
        rows = [r for r in e.j.events if r["event"] == "settled"]
        expected = list(csv.DictReader((path / (m + ".csv")).open()))
        assert len(rows) == len(expected), (m, len(rows), len(expected))
        for i, (r, other) in enumerate(zip(rows, expected, strict=True)):
            for k, v in r.items():
                if isinstance(v, (float, int)):
                    assert abs(float(other[k]) - v) < 1e-7, (m, i, k, v, other[k])
                else:
                    assert str(v) == other[k], (m, i, k, v, other[k])
        a = reports["policies"][m]
        assert (
            abs(e.broker.balance - a["ending_balance"]) < 0.0001
            and abs(e.broker.max_dd - a["max_drawdown"]) < 0.0001
        )
        assert e.broker.counts == a["server_events"]
        answer[m] = len(rows)
    return dict(
        raw=rawcount,
        observed=obs,
        matching_ledgers=answer,
        matching_cash_and_drawdowns=True,
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--cache", type=Path, required=True)
    p.add_argument("--study", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    with ProcessPoolExecutor(max_workers=2) as pool:
        fs = {
            x: pool.submit(check, args.cache, args.study, y)
            for x, y in [("base", False), ("stress", True)]
        }
        res = {x: f.result() for x, f in fs.items()}
    args.output.write_text(json.dumps(res, indent=2) + "\n")
    print(res)
