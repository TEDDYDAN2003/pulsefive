"""Local forward PAPER ONLY: read quotes from an already-open MT5 demo, simulate all orders.
A process restart is a new observation, never a recovery of imaginary broker inventory.
"""

import csv
import json
import time
import uuid
from datetime import UTC, datetime

from .engine import Engine
from .model import FROZEN_SHA, Rules
from .paper import Paper
from .policy import GOLD, POLL, Quote
from .sessions import NAMES, session
from .store import Store


class FeedError(RuntimeError):
    pass


def utcstr(w):
    return datetime.fromtimestamp(w / 1000, UTC).isoformat(timespec="milliseconds")


def clock():
    return time.time_ns() // 1_000_000


class Cursor:
    """Inclusive broker history can contain several different quotes in the same ms.
    Verify the already-read prefix instead of blindly skipping the whole millisecond.
    """

    def __init__(self, begin):
        self.ms = begin
        self.prefix = []

    def consume(self, rows, ceiling):
        old = self.ms
        prefix = self.prefix
        seen = []
        out = []
        now = old
        last = []
        prev = -1
        for x in rows:
            w = int(x["time_msc"])
            bid = float(x["bid"])
            ask = float(x["ask"])
            pair = (bid, ask)
            if w < prev:
                raise FeedError("MT5 quote history is not in time order")
            prev = w
            if w < old or w > ceiling:
                continue
            if w == old:
                seen.append(pair)
                if len(seen) <= len(prefix):
                    if prefix[len(seen) - 1] != pair:
                        raise FeedError("Previously observed quote prefix changed")
                    continue
            q = Quote(w, bid, ask)
            if not q.valid():
                raise FeedError("Invalid bid/ask; run censored")
            if w != now:
                last = []
                now = w
            last.append(pair)
            out.append(q)
        if len(seen) < len(prefix):
            raise FeedError(
                "History response lost the last acknowledged quote; run censored"
            )
        if now == old:
            self.prefix = prefix + last
        else:
            self.ms = now
            self.prefix = last
        return out


class QuoteFeed:
    def __init__(self, mt, now):
        self.mt = mt
        a = mt.account_info()
        term = mt.terminal_info()
        i = mt.symbol_info(GOLD.name)
        if (
            a is None
            or a.trade_mode != mt.ACCOUNT_TRADE_MODE_DEMO
            or term is None
            or not term.connected
        ):
            raise FeedError(
                "Open an already-connected MT5 DEMO terminal. No login or account creation is performed."
            )
        if not i or not i.visible:
            raise FeedError("Make XAUUSDm visible in Market Watch first")
        if (
            i.currency_base != "XAU"
            or i.currency_profit != "USD"
            or abs(i.trade_contract_size - GOLD.contract) > 1e-8
            or abs(i.trade_tick_size - GOLD.tick) > 1e-12
        ):
            raise FeedError("Broker symbol differs from the studied XAUUSDm spec")
        self.identity = (a.login, a.server)
        self.cursor = Cursor(now)
        self.next_check = now

    def read(self, until):
        mt = self.mt
        if until >= self.next_check:
            a = mt.account_info()
            t = mt.terminal_info()
            if (
                not a
                or (a.login, a.server) != self.identity
                or a.trade_mode != mt.ACCOUNT_TRADE_MODE_DEMO
                or not t
                or not t.connected
            ):
                raise FeedError("Account/feed changed; paper run censored")
            self.next_check = until + 5000
        # Some bindings round endpoints to seconds. Request inclusively, then fence by UTC ms.
        a = datetime.fromtimestamp(self.cursor.ms // 1000, UTC)
        b = datetime.fromtimestamp(until // 1000 + 1, UTC)
        arr = mt.copy_ticks_range(GOLD.name, a, b, mt.COPY_TICKS_INFO)
        if arr is None:
            raise FeedError("Quote history unavailable; refusing to infer fills")
        return self.cursor.consume(arr, until)


def pump_quotes(engine, quotes, wall):
    """Prices already received since the previous poll can hit existing orders, not new ones."""
    p = engine.broker
    for q in quotes:
        p.arrive(GOLD.name, q, q.ms)
    p.advance(wall)
    edge = engine.observe(GOLD.name, quotes[-1]) if quotes else None
    engine.pump(wall, [edge] if edge else ())
    return bool(edge)


def observe(mt, minutes, root, profile="release", record=False):
    if profile not in ("release", "challenger") or not 0 < minutes <= 1440:
        raise ValueError("Use release/challenger and 0 < minutes <= 1440")
    started = clock()
    feed = QuoteFeed(mt, started)
    p = Paper([GOLD])
    j = Store(root, "paper-" + str(started) + "-" + uuid.uuid4().hex)
    e = Engine([GOLD], p, j, Rules(profile))
    folder = j.dir
    j.s.update(
        mode="forward-paper",
        frozen_sha256=FROZEN_SHA,
        status="in_progress",
        profile=profile,
        started=utcstr(started),
    )
    j.save()
    counts = dict(
        raw_quotes=0,
        nonempty_polls=0,
        polls=0,
        raw_edges=0,
        late_quotes_while_exposed=0,
    )
    coverage = {
        s: {
            "polls": 0,
            "raw_quotes": 0,
            "fresh_warm_polls": 0,
            "edges": 0,
            "settled": 0,
            "filled": 0,
            "realized_ge_2": 0,
            "settled_net": 0.0,
        }
        for s in (*NAMES, "outside")
    }
    stop_at = started + int(minutes * 60000)
    prev = started
    drain = None
    raw = None
    result = "complete"
    reason = ""
    end = started
    print("PAPER ONLY | " + profile + " | " + str(folder), flush=True)
    if record:
        raw = (folder / "ticks.csv").open("w", newline="")
        writer = csv.writer(raw)
        writer.writerow(["observed_wall_ms", "source_utc_ms", "bid", "ask"])
    try:
        while True:
            try:
                w = clock()
                if w >= stop_at:
                    e.stopping = True
                if e.stopping:
                    if drain is None:
                        drain = w
                    if not e.s.get("batch"):
                        break
                    if w - drain > 35000:
                        raise FeedError(
                            "Paper inventory unresolved at stop; not booked as flat"
                        )
                if w < prev or w - prev > 5000:
                    raise FeedError(
                        "Host clock/poll interruption; forward run censored"
                    )
                start_read = time.monotonic()
                qs = feed.read(w)
                end = clock()
                if not w <= end <= w + 1000:
                    raise FeedError(
                        "Quote query slow or clock changed; no backdated decisions"
                    )
                if (p.orders or p.pos) and qs:
                    counts["late_quotes_while_exposed"] += sum(q.ms < prev for q in qs)
                    if qs[0].ms < prev - 1000:
                        raise FeedError(
                            "Materially delayed quote while exposed; timing ambiguous"
                        )
                if raw:
                    writer.writerows((end, q.ms, q.bid, q.ask) for q in qs)
                counts["raw_quotes"] += len(qs)
                counts["nonempty_polls"] += bool(qs)
                counts["polls"] += 1
                edge = pump_quotes(e, qs, end)
                counts["raw_edges"] += edge
                h = coverage[session(end) or "outside"]
                h["polls"] += 1
                h["raw_quotes"] += len(qs)
                h["edges"] += edge
                tape = e.tapes[GOLD.name]
                h["fresh_warm_polls"] += bool(
                    tape.f and tape.q and 0 <= end - tape.q.ms <= 500
                )
                if e.s.get("hold"):
                    raise FeedError(e.s["hold"])
                prev = end
                time.sleep(max(0, POLL / 1000 - (time.monotonic() - start_read)))
            except KeyboardInterrupt:
                if e.stopping:
                    raise FeedError(
                        "User interrupted drain; check uncensored/settled records only"
                    )
                e.stopping = True
                print("Stopping entries and draining modeled exposure…", flush=True)
    except Exception as exc:
        result = "censored"
        reason = str(exc)
    finally:
        if raw:
            raw.close()
        ev = folder / "events.jsonl"
        rows = (
            [
                x
                for x in (json.loads(l) for l in ev.read_text().splitlines())
                if x["event"] == "settled"
            ]
            if ev.exists()
            else []
        )
        inventory = bool(p.pos or p.orders or e.s.get("batch"))
        if not counts["raw_quotes"] and result == "complete":
            result = "no_quotes"
            reason = "No eligible incoming quotes; not evidence"
        for r in rows:
            h = coverage[r["session"]]
            h["settled"] += 1
            h["filled"] += r["legs_filled"] > 0
            h["realized_ge_2"] += r["net"] >= 2
            h["settled_net"] = round(h["settled_net"] + r["net"], 6)
        summary = dict(
            frozen_sha256=FROZEN_SHA,
            scenario="base",
            by_session=coverage,
            status=result,
            reason=reason,
            profile=profile,
            started=utcstr(started),
            ended=utcstr(clock()),
            **counts,
            settled_batches=len(rows),
            settled_net=round(sum(r["net"] for r in rows), 6),
            cash=round(p.balance, 6),
            marked_drawdown=round(p.max_dd, 6),
            unresolved_inventory=inventory,
            broker_orders_sent=0,
        )
        j.s["status"] = result
        j.save()
        (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        j.close()
    print(json.dumps(summary, indent=2))
    return summary
