"""Fast causal feature scan, followed by actual PulseFive Engine/Paper basket lifecycles.
No future tick contributes to a feature. Forward prices are used only to settle orders.
"""

import csv
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from numba import njit

from pulsefive.data import arrays
from pulsefive.engine import Engine, Memory
from pulsefive.model import FEATURES, STYLES, Rules, vector
from pulsefive.paper import Friction, Paper
from pulsefive.policy import GOLD, POLL, Feature, Quote, Tape
from pulsefive.sessions import NAMES, session

DAY = 86400000


@njit(cache=True)
def scan(tm, bi, ak):
    n = len(tm)
    ret = np.zeros((n, 9))
    edges = np.zeros(n, np.bool_)
    rng = np.zeros(20)
    rates = np.zeros(20)
    nbar = 0
    minute = -1
    full = False
    left = 0
    base = hz = op = hi = lo = 0.0
    count = 0
    previous = 0
    last_edge = -1
    changed = np.ones(n, np.bool_)
    for i in range(n):
        t = tm[i]
        k = t // 60000
        p = (bi[i] + ak[i]) * 0.5
        gap = i > 0 and t - tm[i - 1] > 10000
        if i:
            changed[i] = bi[i] != bi[i - 1] or ak[i] != ak[i - 1]
        if k != minute:
            if k != minute + 1 or gap or not full:
                nbar = 0
            else:
                rng[nbar % 20] = hi - lo
                rates[nbar % 20] = count / 60.0
                nbar += 1
            base = np.median(rng) if nbar >= 20 else 0.0
            hz = np.median(rates) if nbar >= 20 else 0.0
            minute = k
            full = i > 0 and not gap and t % 60000 <= 4000
            op = hi = lo = p
            count = 0
        if gap:
            full = False
            base = hz = 0.0
            nbar = 0
            left = i
        hi = max(hi, p)
        lo = min(lo, p)
        count += int(changed[i])
        side = 0
        while left < i and tm[left] < t - 2500:
            left += 1
        if full and base > 0 and hz > 0:
            first = p
            last = p
            num = 0
            up = down = 0.0
            for z in range(left, i + 1):
                if not changed[z]:
                    continue
                val = (bi[z] + ak[z]) * 0.5
                if num == 0:
                    first = val
                    last = val
                ch = val - last
                if ch > 0:
                    up += ch
                else:
                    down -= ch
                last = val
                num += 1
            move = p - first
            eff = move / (up + down) if up + down else 0.0
            pace = num / (2.5 * hz)
            ran = hi - lo
            ratio = ran / base
            body = (p - op) / ran if ran else 0.0
            s = 1 if body > 0 else -1
            spread = ak[i] - bi[i]
            if (
                6000 <= t % 60000 <= 52000
                and spread <= 0.30 + 1e-12
                and ran >= max(1.4 * base, 4 * spread, 0.008)
                and abs(body) >= 0.65
                and num >= 4
                and pace >= 1.35
                and s * eff >= 0.46
                and s * move >= max(0.7 * spread, 0.002)
            ):
                side = s
            score = ratio * min(pace, 4.0) * abs(eff) * abs(body) if side else 0.0
            ret[i, 0] = base
            ret[i, 1] = ratio
            ret[i, 2] = pace
            ret[i, 3] = eff
            ret[i, 4] = move
            ret[i, 5] = up / 2.5
            ret[i, 6] = down / 2.5
            ret[i, 7] = side
            ret[i, 8] = score
        if side and side != previous and last_edge != k:
            edges[i] = True
            last_edge = k
        previous = side
    return ret, edges


class Day:
    def __init__(self, ms, ba, stress):
        self.ms = ms
        if stress:
            extra = np.ceil((ba[:, 1] - ba[:, 0]) * 0.1).astype("int32")
            self.b = (ba[:, 0] - extra) / 1000.0
            self.a = (ba[:, 1] + extra) / 1000.0
        else:
            self.b = ba[:, 0] / 1000.0
            self.a = ba[:, 1] / 1000.0
        bins = (ms + POLL - 1) // POLL
        self.end = np.r_[np.flatnonzero(bins[1:] != bins[:-1]), len(ms) - 1]
        self.wall = bins[self.end] * POLL
        self.t = ms[self.end]
        self.bid = self.b[self.end]
        self.ask = self.a[self.end]
        self.f, edges = scan(self.t, self.bid, self.ask)
        self.edges = np.flatnonzero(edges)

    def current(self, k):
        a = self.f[k]
        return Quote(int(self.t[k]), self.bid[k].item(), self.ask[k].item()), Feature(
            *[v.item() for v in a[:7]], int(a[7]), a[8].item()
        ) if a[0] > 0 else None

    def parity(self, upto=None):
        t = Tape(GOLD)
        lastside = 0
        lastminute = -1
        actual = []
        n = len(self.end) if upto is None else min(upto, len(self.end))
        for k in range(n):
            q, f = self.current(k)
            assert t.update(q)
            g = t.f
            if g is None:
                assert f is None
                lastside = 0
                continue
            assert g.side == f.side and all(
                abs(a - b) <= 1e-10 * max(1, abs(a))
                for a, b in zip(
                    (
                        g.base,
                        g.ratio,
                        g.pace,
                        g.efficiency,
                        g.move,
                        g.up_speed,
                        g.down_speed,
                        g.score,
                    ),
                    (
                        f.base,
                        f.ratio,
                        f.pace,
                        f.efficiency,
                        f.move,
                        f.up_speed,
                        f.down_speed,
                        f.score,
                    ),
                )
            )
            if g.side and g.side != lastside and q.ms // 60000 != lastminute:
                actual.append(k)
                lastminute = q.ms // 60000
            lastside = g.side
        np.testing.assert_array_equal(actual, self.edges[self.edges < n])
        return n


def new_engine(rules, stress):
    p = Paper(
        [GOLD], 1000.0, 100.0, Friction(450, 50, 3.0, 5.0) if stress else Friction()
    )
    return Engine([GOLD], p, Memory(), rules)


def play(e, day, k):
    """At entry, only current quote/features are exposed. Subsequent ticks settle this basket."""
    p = e.broker
    t = e.tapes[GOLD.name]
    q, f = day.current(k)
    w = int(day.wall[k])
    t.q = q
    t.f = f
    p.last[GOLD.name] = q
    start = len(e.j.events)
    e.pump(w, [(f.score, GOLD.name)])
    if not e.s.get("batch"):
        return k, None
    for z in range(k + 1, len(day.end)):
        wall = int(day.wall[z])
        prevw = int(day.wall[z - 1])
        for a in range(prevw + POLL, min(wall, prevw + 95000), POLL):
            p.advance(a)
            e.pump(a)
            if e.s.get("hold"):
                raise RuntimeError(e.s["hold"])
            if not e.s.get("batch"):
                break
        if e.s.get("batch"):
            for j in range(int(day.end[z - 1]) + 1, int(day.end[z]) + 1):
                raw = Quote(int(day.ms[j]), float(day.b[j]), float(day.a[j]))
                p.arrive(GOLD.name, raw, raw.ms)
            t.q, t.f = day.current(z)
            e.pump(wall)
        if e.s.get("hold"):
            raise RuntimeError(e.s["hold"])
        if not e.s.get("batch"):
            rec = next(
                r.copy()
                for r in reversed(e.j.events[start:])
                if r["event"] == "settled"
            )
            rec.update(
                cancels=len(
                    [r for r in e.j.events[start:] if r["event"] == "cancel_intent"]
                )
            )
            return z, rec
    raise RuntimeError("Censored batch at file end: refusing to report it as flat")


def dates(cache, keys, stress):
    for key in keys:
        tm, book = arrays(cache, key)
        days = tm // DAY
        ends = np.r_[0, np.flatnonzero(days[1:] != days[:-1]) + 1, len(tm)]
        for a, b in zip(ends, ends[1:]):
            day = int(days[a])
            dt = date(1970, 1, 1) + timedelta(days=day)
            if dt.weekday() >= 5:
                continue
            yield dt.isoformat(), Day(tm[a:b], book[a:b], stress)


def labels(cache, key, stress, out):
    result = []
    parity = 0
    edges = 0
    days = []
    for date_s, day in dates(cache, [key], stress):
        days.append(date_s)
        if not parity:
            parity = day.parity(14000)
        for k in day.edges:
            q, f = day.current(k)
            ss = session(q.ms)
            if not ss:
                continue
            edges += 1
            x = vector(GOLD, q, f)
            for style in STYLES:
                e = new_engine(Rules("label", style=style), stress)
                _, row = play(e, day, k)
                if row is not None:
                    result.append(
                        dict(
                            date=date_s,
                            session=ss,
                            style=style,
                            ms=int(q.ms),
                            **dict(zip(FEATURES, x)),
                            net=row["net"],
                            filled=row["legs_filled"],
                            closed_ms=row["ms"],
                        )
                    )
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    name = f"{key}-" + ("stress" if stress else "base")
    import gzip

    with gzip.open(out / (name + ".csv.gz"), "wt", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(result[0]))
        writer.writeheader()
        writer.writerows(result)
    meta = dict(
        days=days,
        raw_eligible_edges=edges,
        trial_rows=len(result),
        scalar_parity_observations=parity,
    )
    (out / (name + ".json")).write_text(json.dumps(meta, indent=2) + "\n")
    print("labelled", name, len(result), flush=True)
    return meta


def aggregate(rows):
    a = np.array([r["net"] for r in rows], float)
    loss = -a[a < 0].sum()
    wins = a[a > 0].sum()
    return dict(
        attempts=len(rows),
        filled=sum(r["legs_filled"] > 0 for r in rows),
        full=sum(r["legs_filled"] == 5 for r in rows),
        net=round(float(a.sum()), 4),
        wins=int((a > 0).sum()),
        realized_target=int((a >= 2).sum()),
        pf=round(float(wins / loss), 4) if loss else None,
    )


def portfolio(cache, keys, stress, art, modes, loc):
    engines = {m: new_engine(Rules(m, art), stress) for m in modes}
    results = {m: [] for m in modes}
    obs = 0
    raw = 0
    parity = 0
    ds = []
    for ds1, day in dates(cache, keys, stress):
        raw += len(day.ms)
        obs += len(day.t)
        ds.append(ds1)
        if not parity:
            parity = day.parity(14000)
        for m, e in engines.items():
            last = -1
            for k in day.edges:
                if k <= last or not session(int(day.t[k])):
                    continue
                last, rec = play(e, day, k)
                if rec is not None:
                    results[m].append(rec)
    dest = Path(loc)
    dest.mkdir(parents=True, exist_ok=True)
    report = {}
    for mode, rows in results.items():
        p = engines[mode].broker
        by = {s: aggregate([r for r in rows if r["session"] == s]) for s in NAMES}
        rec = aggregate(rows)
        if abs(rec["net"] - (p.balance - 1000)) > 1e-3:
            raise RuntimeError("Cash ledger mismatch")
        rec.update(
            ending_balance=round(p.balance, 4),
            max_drawdown=round(p.max_dd, 4),
            sessions=by,
            fill_days={
                s: len(
                    {
                        r["born"] // DAY
                        for r in rows
                        if r["session"] == s and r["legs_filled"]
                    }
                )
                for s in NAMES
            },
            server_events=p.counts,
        )
        with (dest / (mode + ".csv")).open("w", newline="") as f:
            w = csv.DictWriter(
                f,
                fieldnames=list(rows[0])
                if rows
                else ["event", "id", "session", "born", "ms", "legs_filled", "net"],
            )
            w.writeheader()
            w.writerows(rows)
        report[mode] = rec
    summary = dict(
        raw_ticks=raw,
        observed_ticks=obs,
        dates=ds,
        scalar_parity_observations=parity,
        policies=report,
    )
    (dest / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary
