"""Tick replay. No broker import. On disk: verified source ZIP or prepared numpy arrays."""

import csv
import hashlib
import json
import zipfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from .engine import Engine, Memory
from .paper import Friction, Paper
from .policy import GOLD, POLL, Quote

SHA = "f9066c09dc1d8d6e55b5a89dda37f33994fd69a605c96eccf29ead9c05278feb"
MEMBER = "Exness_XAUUSDm_2026_10.csv"
URL = "https://ticks.ex2archive.com/ticks/XAUUSDm/2026/10/Exness_XAUUSDm_2026_10.zip"


def prepare(archive, cache):
    import numpy as np
    import pandas as pd

    with Path(archive).open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    if digest != SHA:
        raise ValueError(
            "This research loader requires the original October 2026 XAUUSDm ZIP: "
            + URL
        )
    cache = Path(cache) / SHA[:16]
    cache.mkdir(parents=True, exist_ok=True)
    if (cache / "complete").exists():
        return cache
    n = 5914197
    off = 0
    last = -1
    m = np.lib.format.open_memmap(
        cache / "ms.npy", mode="w+", dtype="int64", shape=(n,)
    )
    ba = np.lib.format.open_memmap(
        cache / "ba.npy", mode="w+", dtype="int32", shape=(n, 2)
    )
    with zipfile.ZipFile(archive) as z, z.open(MEMBER) as f:
        for a in pd.read_csv(f, chunksize=200000):
            ts = (
                pd.to_datetime(a.Timestamp, utc=True)
                .to_numpy(dtype="datetime64[ms]")
                .astype("int64")
            )
            pr = a[["Bid", "Ask"]].to_numpy()
            pi = np.rint(pr * 1000).astype("int32")
            end = off + len(ts)
            if not (
                (a.Symbol == "XAUUSDm").all()
                and end <= n
                and last <= ts[0]
                and (np.diff(ts) >= 0).all()
                and (pi[:, 0] > 0).all()
                and (pi[:, 1] >= pi[:, 0]).all()
                and np.allclose(pr, pi / 1000, atol=1e-10, rtol=0)
            ):
                raise ValueError("Invalid quote archive")
            m[off:end] = ts
            ba[off:end] = pi
            off = end
            last = ts[-1]
    if off != n:
        raise ValueError("Unexpected archive length")
    m.flush()
    ba.flush()
    (cache / "complete").write_text(digest)
    return cache


def replay(folder, start, end, stress=False, capital=1000.0, output=None):
    import numpy as np

    day0 = date.fromisoformat(start)
    day1 = date.fromisoformat(end)
    if not date(2026, 10, 1) <= day0 <= day1 <= date(2026, 10, 30):
        raise ValueError("Select days within this archive (Oct 1–30, 2026)")
    folder = Path(folder)
    ts = np.load(folder / "ms.npy", mmap_mode="r")
    ba = np.load(folder / "ba.npy", mmap_mode="r")
    fr = Friction(450, 50, 3.0, 5.0) if stress else Friction()
    p = Paper([GOLD], capital, 100.0, fr)
    j = Memory()
    engine = Engine([GOLD], p, j)
    name = GOLD.name
    d = day0
    nd = 0
    examined = 0
    sampled = 0
    while d <= day1:
        day = (d - date(1970, 1, 1)).days
        d += timedelta(days=1)
        if (day + 3) % 7 >= 5:
            continue
        begin = day * 86400000 + (12 * 60 - 23) * 60000
        endms = day * 86400000 + (16 * 60 + 2) * 60000
        lo, hi = map(int, np.searchsorted(ts, [begin, endms]))
        if lo == hi:
            continue
        nd += 1
        m = np.asarray(ts[lo:hi])
        b = np.asarray(ba[lo:hi, 0])
        a = np.asarray(ba[lo:hi, 1])
        if stress:
            extra = np.ceil((a - b) * 0.1).astype("int32")
            b = b - extra
            a = a + extra
        buckets = (m + POLL - 1) // POLL
        ends = np.r_[np.flatnonzero(buckets[1:] != buckets[:-1]), len(m) - 1]
        prev = -1
        oldwall = 0
        for idx in ends:
            i = int(idx)
            wall = int(buckets[i]) * POLL
            active = bool(j.s.get("batch"))
            if active:
                # Dispatch quiet timer ticks before each later price update; never fill on a fictitious quote.
                for w in range(oldwall + POLL, wall, POLL):
                    p.advance(w)
                    engine.pump(w)
                    if not j.s.get("batch") or j.s.get("hold"):
                        break
                for k in range(prev + 1, i + 1):
                    px = Quote(int(m[k]), int(b[k]) / 1000, int(a[k]) / 1000)
                    p.arrive(name, px, px.ms)
            q = Quote(int(m[i]), int(b[i]) / 1000, int(a[i]) / 1000)
            p.last[name] = q
            edge = engine.observe(name, q)
            if edge or active:
                engine.pump(wall, [edge] if edge else ())
            examined += i - prev
            sampled += 1
            prev = i
            oldwall = wall
            if j.s.get("hold"):
                raise RuntimeError(j.s["hold"])
            if (
                j.s.get("day") == day
                and not j.s.get("batch")
                and (j.s["attempts"] >= 20 or j.s["day_locked"])
            ):
                break
        if j.s.get("batch"):
            raise RuntimeError(
                "Censored open batch at end of observation; results invalid"
            )
    rows = [e for e in j.events if e["event"] == "settled"]
    filled = [r for r in rows if r["lots_filled"]]
    wins = sum(r["net"] > 0 for r in filled)
    loss = -sum(min(0, r["net"]) for r in filled)
    summary = dict(
        start=start,
        end=end,
        days=nd,
        scenario="stress" if stress else "base",
        starting_balance=capital,
        ending_balance=round(p.balance, 2),
        net=round(p.balance - capital, 2),
        attempts=len(rows),
        filled_batches=len(filled),
        full_five=sum(r["legs_filled"] == 5 for r in rows),
        tickets_submitted=sum(r["legs_sent"] for r in rows),
        tickets_filled=sum(r["legs_filled"] for r in rows),
        profitable_batches=wins,
        realized_at_least_2=sum(r["net"] >= 2 for r in rows),
        target_intents=sum(r["reason"] == "basket_target" for r in rows),
        profit_factor=round(sum(max(0, r["net"]) for r in filled) / loss, 3)
        if loss
        else None,
        max_mark_to_market_drawdown=round(p.max_dd, 2),
        exit_reasons=dict(Counter(r["reason"] for r in rows)),
        cancel_reasons=dict(
            Counter(e["reason"] for e in j.events if e["event"] == "cancel_intent")
        ),
        server_events=p.counts,
        raw_rows_in_processed_windows=examined,
        occupied_poll_bins=sampled,
        archive_sha256=SHA,
    )
    if abs(sum(r["net"] for r in rows) - (p.balance - capital)) > 1e-4:
        raise RuntimeError("Ledger does not reconcile")
    if output:
        out = Path(output)
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        with (out / "batches.csv").open("w", newline="") as f:
            w = csv.DictWriter(
                f, fieldnames=list(rows[0]) if rows else ["event", "ms", "net"]
            )
            w.writeheader()
            w.writerows(rows)
    return summary
