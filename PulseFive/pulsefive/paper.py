"""A modeled broker, not an MT5 execution emulator. All cash is in USD."""

from dataclasses import dataclass

from .engine import Block, View
from .policy import FEE, LOTS


@dataclass(frozen=True)
class Friction:
    latency: int = 150
    through: int = 1
    slip_multiple: float = 1.0
    fee: float = FEE


class Paper:
    def __init__(self, specs, balance=1000.0, leverage=100.0, friction=Friction()):
        self.specs = {x.name: x for x in specs}
        self.balance = balance
        self.leverage = leverage
        self.fr = friction
        self.orders = {}
        self.pos = {}
        self.settlements = {}
        self.seq = 100
        self.last = {}
        self.min_equity = balance
        self.high = balance
        self.max_dd = 0.0
        self.counts = {}

    def account(self):
        u = margin = 0.0
        for p in self.pos.values():
            sp = self.specs[p["name"]]
            q = self.last[p["name"]]
            u += p["s"] * (q.mark(p["s"]) - p["price"]) * sp.contract * LOTS
            margin += p["price"] * sp.contract * LOTS / self.leverage
        for p in self.orders.values():
            margin += p["price"] * self.specs[p["name"]].contract * LOTS / self.leverage
        eq = self.balance + u
        return self.balance, eq, eq - margin

    def admit(self, sp, q, s, legs, eq, free):
        if self.orders or self.pos:
            raise Block("inventory_busy")
        if (
            free - sum(l.price * sp.contract * LOTS / self.leverage for l in legs)
            < 0.20 * eq
        ):
            raise Block("margin_floor")

    def place(self, b, j, leg, now, q):
        self.seq += 1
        k = self.seq
        self.orders[k] = dict(
            name=b["name"],
            batch=b["id"],
            s=b["side"],
            price=leg.price,
            sl=leg.sl,
            live=now + self.fr.latency,
            expiry=now + 90000,
            cancel=0,
        )
        return k

    def cancel(self, b, k, now):
        if k in self.orders:
            self.orders[k]["cancel"] = now + self.fr.latency
        return True

    def close(self, b, k, vol, now, q):
        if k in self.pos:
            self.pos[k]["close"] = now + self.fr.latency
        return True

    def known_queued_exit(self, b, ticket):
        p = self.pos.get(ticket)
        if not (p and p["batch"] == b["id"] and p["close"]):
            return False
        if not p.get("gap_flag"):
            self.counts["exit_waited_over_5s_for_quote"] = (
                self.counts.get("exit_waited_over_5s_for_quote", 0) + 1
            )
            p["gap_flag"] = True
        return True

    def record(self, k):
        return self.settlements.setdefault(
            k, {"book": 0.0, "in": 0, "out": 0, "first": 0}
        )

    def advance(self, now):
        # A server can acknowledge a cancellation/expiry with no new price tick.
        for k, o in list(self.orders.items()):
            if (o["cancel"] and now >= o["cancel"]) or now >= o["expiry"]:
                del self.orders[k]

    def arrive(self, name, q, now):
        self.last[name] = q
        sp = self.specs[name]
        for k, o in list(self.orders.items()):
            if o["name"] != name:
                continue
            if (o["cancel"] and now >= o["cancel"]) or now >= o["expiry"]:
                del self.orders[k]
                continue
            if now < o["live"]:
                continue
            s = o["s"]
            touch = s * (o["price"] - q.entry(s))
            if touch + 1e-12 < sp.tick * self.fr.through:
                continue
            del self.orders[k]
            self.pos[k] = dict(o, close=0)
            ev = self.record(o["batch"])
            ev["in"] += 1
            if not ev["first"]:
                ev["first"] = now
            fee = self.fr.fee * LOTS
            ev["book"] -= fee
            self.balance -= fee
            if o["cancel"]:
                self.counts["fill_during_cancel"] = (
                    self.counts.get("fill_during_cancel", 0) + 1
                )
        for k, p in list(self.pos.items()):
            if p["name"] != name:
                continue
            s = p["s"]
            m = q.mark(s)
            stop = s * (m - p["sl"]) <= 0
            if not stop and not (p["close"] and now >= p["close"]):
                continue
            px = m - s * self.fr.slip_multiple * sp.reserve
            real = s * (px - p["price"]) * LOTS * sp.contract - self.fr.fee * LOTS
            ev = self.record(p["batch"])
            ev["out"] += 1
            ev["book"] += real
            self.balance += real
            del self.pos[k]
            if stop:
                self.counts["native_stops"] = self.counts.get("native_stops", 0) + 1
        eq = self.account()[1]
        self.min_equity = min(self.min_equity, eq)
        self.high = max(self.high, eq)
        self.max_dd = max(self.max_dd, self.high - eq)

    def view(self, b, now, q):
        key = b["id"]
        ev = self.record(key)
        orders = {k: o["price"] for k, o in self.orders.items() if o["batch"] == key}
        positions = {}
        u = 0.0
        for k, p in self.pos.items():
            if p["batch"] != key:
                continue
            positions[k] = LOTS
            sp = self.specs[b["name"]]
            if q:
                u += p["s"] * (q.mark(p["s"]) - p["price"]) * LOTS * sp.contract
        return View(
            orders,
            positions,
            ev["book"],
            u,
            ev["in"] * LOTS,
            ev["out"] * LOTS,
            ev["first"],
            not orders and not positions and ev["in"] == ev["out"],
            False,
            ev["in"],
        )
