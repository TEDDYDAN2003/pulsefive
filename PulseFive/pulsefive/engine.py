"""Single global basket. Broker inventory, not a send receipt, determines flatness."""

from dataclasses import asdict, dataclass, field
from math import isfinite

from .policy import (
    COOLDOWN,
    DAILY_ATTEMPTS,
    FEE,
    LEGS,
    Leg,
    Tape,
    cancel_reason,
    daily_budget,
    early_exit,
    plan,
    risk_budget,
    window,
)


class Block(Exception):
    """Definite pre-send veto or known rejection."""


class Review(Exception):
    """Unknown mutation / ownership: freeze and reconcile manually."""


@dataclass
class View:
    orders: dict = field(default_factory=dict)  # accepted id -> working limit
    positions: dict = field(default_factory=dict)  # owned ticket -> remaining lots
    booked: float = 0.0  # booked deals incl entry/exit fees
    floating: float = 0.0  # exit-side mark incl open swap
    entered: float = 0.0
    exited: float = 0.0
    first_fill: int = 0
    proven: bool = False  # all accepted orders terminal & volume closed
    unsafe: bool = False
    filled_legs: int = 0
    can_stage: bool = True


class Memory:
    def __init__(self):
        self.s = {}
        self.events = []

    def save(self):
        pass

    def log(self, event, **kw):
        self.events.append(dict(event=event, **kw))


class Engine:
    def __init__(self, specs, broker, journal, rules=None):
        self.rules = rules
        self.edge_minute = {}
        self.specs = {sp.name: sp for sp in specs}
        self.tapes = {k: Tape(sp) for k, sp in self.specs.items()}
        self.broker = broker
        self.j = journal
        self.s = journal.s
        self.seen = {}
        self.stopping = False
        if self.s.get("intent"):
            self.hold("unfinished broker request; check terminal and history manually")
        if self.s.get("batch") and not self.s.get("hold"):
            self.latch(
                self.s["batch"], "restart_drain"
            )  # never resume placement after restart

    def hold(self, msg):
        self.s["hold"] = str(msg)
        self.j.save()
        self.j.log("manual_review", reason=str(msg))

    def latch(self, b, why):
        if not b["exit"]:
            b["exit"] = why
            b["stage"] = False
            self.j.save()
            self.j.log("exit_intent", id=b["id"], reason=why)

    def observe(self, name, q):
        t = self.tapes[name]
        if not t.update(q):
            return None
        f = t.f
        side = f.side if f else 0
        prev = self.seen.get(name, 0)
        self.seen[name] = side
        if not side or side == prev:
            return None
        if self.rules:
            minute = q.ms // 60000
            if self.edge_minute.get(name) == minute:
                return None
            self.edge_minute[name] = minute
        return f.score, name

    def bucket(self, ms):
        return self.rules.bucket(ms) if self.rules else ("Legacy" if window(ms) else "")

    def mutate(self, b, op, fn, now):
        self.s["intent"] = {"batch": b["id"], "op": op, "ms": now}
        self.j.save()
        try:
            value = fn()
        except Block as exc:
            self.s.pop("intent", None)
            self.j.save()
            self.j.log("reject", id=b["id"], op=op, reason=str(exc))
            return None
        except Exception as exc:
            self.hold(f"{op}: {exc}")
            return None
        # Caller records the resulting ticket/action AND clears intent in the same save.
        return value

    def commit_action(self):
        self.s.pop("intent", None)
        self.j.save()

    def pump(self, now, edges=()):
        if self.s.get("hold"):
            return
        try:
            self._pump(now, edges)
        except Review as exc:
            self.hold(exc)

    def _pump(self, now, edges):
        b = self.s.get("batch")
        if b:
            self.manage(now, b)
            return
        if (
            self.stopping
            or not edges
            or not self.bucket(now)
            or now < self.s.get("after", 0)
        ):
            return
        day = now // 86400000
        bal, eq, free = self.broker.account()
        if not all(isfinite(v) for v in (bal, eq, free)) or min(bal, eq, free) <= 0:
            return
        if self.s.get("day") != day:
            self.s.update(
                day=day,
                start_eq=eq,
                attempts=0,
                day_pnl=0.0,
                day_locked=False,
                per_session={},
            )
            self.j.save()
        if eq <= self.s["start_eq"] - daily_budget(self.s["start_eq"]) or self.s[
            "day_pnl"
        ] <= -daily_budget(self.s["start_eq"]):
            self.s["day_locked"] = True
            self.j.save()
        if self.s["day_locked"] or self.s["attempts"] >= DAILY_ATTEMPTS:
            return
        for _, name in sorted(edges, reverse=True):
            t = self.tapes[name]
            sp = t.spec
            q = t.q
            f = t.f
            if (
                not f
                or not f.side
                or not 0 <= now - q.ms <= 500
                or self.bucket(q.ms) != self.bucket(now)
            ):
                continue
            session = self.bucket(now)
            if self.rules and self.s.get("per_session", {}).get(session, 0) >= 4:
                continue
            choice = self.rules.decide(sp, q, f) if self.rules else ("shallow", 0.0)
            if not choice:
                continue
            style, estimate = choice
            legs = plan(sp, q, f, eq, style)
            if not legs:
                continue
            try:
                self.broker.admit(sp, q, f.side, legs, eq, free)
            except Block as exc:
                self.j.log("admission_veto", ms=now, reason=str(exc))
                return
            b = dict(
                id=f"PF{now:x}",
                name=name,
                side=f.side,
                session=session,
                style=style,
                estimate=round(estimate, 6),
                born=now,
                signal_ms=q.ms,
                score=round(f.score, 4),
                range_ratio=round(f.ratio, 3),
                pace=round(f.pace, 3),
                eff=round(f.efficiency, 3),
                legs=[asdict(l) for l in legs],
                ids=[],
                stage=True,
                exit="",
                limit=risk_budget(eq),
                peak=-1e6,
                reverse_ms=0,
                last_flow=0,
                actions={},
                retry={},
                cancels={},
                first=0,
            )
            self.s["batch"] = b
            self.s.setdefault("per_session", {})[session] = (
                self.s.get("per_session", {}).get(session, 0) + 1
            )
            self.s["attempts"] += 1
            self.j.save()
            self.j.log("batch_start", id=b["id"], ms=now, name=name, side=f.side)
            self.manage(now, b)
            return

    def manage(self, now, b):
        t = self.tapes[b["name"]]
        sp = t.spec
        q = t.q
        f = t.f
        s = b["side"]
        age = now - b["born"]
        fresh = q is not None and 0 <= now - q.ms <= 1000
        v = self.broker.view(b, now, q)
        if not all(isfinite(x) for x in (v.booked, v.floating, v.entered, v.exited)):
            raise Review("invalid account valuation")
        if self.stopping:
            self.latch(b, "operator_stop")
        if age < 0:
            raise Review("clock went backwards")
        if not self.bucket(now) or (
            self.rules
            and (
                self.bucket(now) != b["session"]
                or now // 86400000 != b["born"] // 86400000
            )
        ):
            self.latch(b, "session_end")
        if not fresh:
            self.latch(b, "stale_quote")
        if v.unsafe:
            self.latch(b, "protection_missing")
        if v.exited > 0:
            self.latch(b, "broker_exit")
        if v.first_fill and not b["first"]:
            b["first"] = v.first_fill
            self.j.save()
        net = (
            v.booked
            + v.floating
            - sum(v.positions.values()) * (FEE + sp.reserve * sp.contract)
        )
        if self.s["day_pnl"] + net <= -daily_budget(self.s["start_eq"]):
            self.s["day_locked"] = True
            self.latch(b, "daily_loss")
            self.j.save()
        if v.positions and fresh:
            b["peak"] = max(b["peak"], net)
            neg = (
                f
                and s * f.efficiency < -0.38
                and s * f.move < -0.35 * q.spread
                and f.pace >= 0.65
            )
            if not neg:
                b["reverse_ms"] = 0
            elif not b["reverse_ms"] or q.ms - b["last_flow"] > 1000:
                b["reverse_ms"] = q.ms
            b["last_flow"] = q.ms
            why = early_exit(
                q,
                f,
                s,
                net,
                b["peak"],
                now - (b["first"] or now),
                q.ms - b["reverse_ms"] if b["reverse_ms"] else 0,
                b["limit"],
            )
            if why:
                self.latch(b, why)
        # In-flight actions are reconciled by actual inventory. A partial close may
        # be resumed only after observed volume reduction, never on timeout alone.
        for key, (when, vol) in list(b["actions"].items()):
            ticket = int(key[1:])
            current = v.orders if key[0] == "c" else v.positions
            if ticket not in current or (
                key[0] == "x" and current[ticket] < vol - 1e-9
            ):
                del b["actions"][key]
                self.j.save()
            elif now - when > 5000:
                # A simulator can *prove* that its local, unfilled market-exit instruction
                # is waiting for a real subsequent quote. An external broker cannot.
                waits = getattr(self.broker, "known_queued_exit", None)
                if not (key[0] == "x" and waits and waits(b, ticket)):
                    raise Review("acknowledged action not reconciled: " + key)

        def act(key, fn, volume):
            if key in b["actions"] or now < b["retry"].get(key, 0):
                return
            ans = self.mutate(b, key, fn, now)
            if self.s.get("hold"):
                return
            if ans is None:
                b["retry"][key] = now + 1000
            else:
                b["actions"][key] = [now, volume]
            self.commit_action()

        # Latch withdrawals: a receding quote cannot undo a cancellation decision.
        for oid, price in v.orders.items():
            key = str(oid)
            why = (
                b["exit"]
                or b["cancels"].get(key)
                or cancel_reason(sp, q, f, s, price, age)
            )
            if why and key not in b["cancels"]:
                b["cancels"][key] = why
                b["stage"] = False
                self.j.save()
                self.j.log("cancel_intent", id=b["id"], order=oid, reason=why, ms=now)
            if key in b["cancels"]:
                act("c" + key, lambda: self.broker.cancel(b, oid, now), 0)
            if self.s.get("hold"):
                return
        if b["exit"] and fresh:
            for ticket, vol in v.positions.items():
                act(
                    "x" + str(ticket),
                    lambda: self.broker.close(b, ticket, vol, now, q),
                    vol,
                )
                if self.s.get("hold"):
                    return
        if b["stage"]:
            j = len(b["ids"])
            if (
                not v.can_stage
                or j >= LEGS
                or age > 650
                or not f
                or f.side != s
                or not fresh
                or v.entered > 0
                or q.spread > sp.cap + 1e-12
            ):
                b["stage"] = False
                self.j.save()
            else:
                leg = Leg(**b["legs"][j])
                dist = s * (q.entry(s) - leg.price)
                if dist < max(sp.gap, sp.tick) - sp.tick * 1e-6:
                    b["stage"] = False
                    self.j.save()
                else:
                    oid = self.mutate(
                        b,
                        "place:" + str(j + 1),
                        lambda: self.broker.place(b, j, leg, now, q),
                        now,
                    )
                    if self.s.get("hold"):
                        return
                    if oid is None:
                        b["stage"] = False
                    elif int(oid) <= 0 or int(oid) in b["ids"]:
                        self.hold("ambiguous opening ticket")
                        return
                    else:
                        b["ids"].append(int(oid))
                        self.j.log("placed", id=b["id"], leg=j + 1, ticket=oid, ms=now)
                        if len(b["ids"]) == LEGS:
                            b["stage"] = False
                    self.commit_action()
                    return
        if not b["stage"] and not v.orders and not v.positions and v.proven:
            self.s["day_pnl"] += v.booked
            self.s["after"] = now + COOLDOWN
            self.s["batch"] = None
            self.j.log(
                "settled",
                id=b["id"],
                symbol=b["name"],
                side=s,
                session=b["session"],
                style=b["style"],
                ms=now,
                born=b["born"],
                legs_sent=len(b["ids"]),
                legs_filled=v.filled_legs,
                lots_filled=round(v.entered, 6),
                net=round(v.booked, 6),
                reason=b["exit"] or ("no_fill" if not v.entered else "broker_exit"),
                score=b["score"],
            )
            self.j.save()
        elif age > 120000 and not v.positions and not v.orders and not v.proven:
            raise Review("flat snapshot without complete order/deal history")
