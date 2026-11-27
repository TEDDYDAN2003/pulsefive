"""Price decisions shared by historical replay, forward paper and the demo desk.
All quotes and minutes are UTC. Nothing in this module can send an order.
"""

from collections import deque
from dataclasses import dataclass
from math import ceil, floor, isfinite
from statistics import median

LEGS, LOTS, TARGET = 5, 0.01, 2.0
MAGIC = 261118503
POLL, TTL, HOLD, COOLDOWN = 50, 6000, 25000, 2000  # milliseconds
DAILY_ATTEMPTS, FEE = 20, 3.50  # fee allowance USD per lot per SIDE


def window(ms):
    day, rem = divmod(ms, 86400000)
    return (day + 3) % 7 < 5 and 12 * 3600000 <= rem < 16 * 3600000


def risk_budget(equity):
    return min(5.0, 0.005 * equity)


def daily_budget(equity):
    return min(20.0, 0.02 * equity)


@dataclass(frozen=True, slots=True)
class Spec:
    name: str
    tick: float
    contract: float  # USD P&L per 1.0 price movement per lot; USD-linear products only
    cap: float
    reserve: float  # assumed exit slippage allowance in price units
    gap: float = 0.0


GOLD = Spec("XAUUSDm", 0.001, 100.0, 0.30, 0.02)
EUR = Spec("EURUSDm", 0.00001, 100000.0, 0.00018, 0.00002)


@dataclass(frozen=True, slots=True)
class Quote:
    ms: int
    bid: float
    ask: float

    def valid(self):
        return self.ms > 0 and 0 < self.bid <= self.ask and isfinite(self.ask)

    @property
    def mid(self):
        return (self.bid + self.ask) * 0.5

    @property
    def spread(self):
        return self.ask - self.bid

    def entry(self, s):
        return self.ask if s == 1 else self.bid

    def mark(self, s):
        return self.bid if s == 1 else self.ask


@dataclass(frozen=True, slots=True)
class Feature:
    base: float
    ratio: float
    pace: float
    efficiency: float
    move: float
    up_speed: float
    down_speed: float
    side: int
    score: float


class Tape:
    """20 complete M1 midpoint ranges and observed quote-change counts; 2.5s micro window."""

    def __init__(self, spec):
        self.spec = spec
        self.bars = deque(maxlen=20)
        self.micro = deque()
        self.q = None
        self.minute = -1
        self.base = self.hz = 0.0
        self.full = False
        self.f = None

    def update(self, q):
        if not q.valid() or (self.q and q.ms < self.q.ms):
            return False
        if self.q == q:
            return False
        old = self.q
        self.q = q
        mid = q.mid
        k = q.ms // 60000
        gap = bool(old and q.ms - old.ms > 10000)
        if k != self.minute:
            if k != self.minute + 1 or gap:
                self.bars.clear()
            elif self.full:
                self.bars.append((self.hi - self.lo, self.count / 60.0))
            else:
                self.bars.clear()
            self.base = median(x[0] for x in self.bars) if len(self.bars) == 20 else 0.0
            self.hz = median(x[1] for x in self.bars) if len(self.bars) == 20 else 0.0
            self.minute = k
            self.full = old is not None and not gap and q.ms % 60000 <= 4000
            self.op = self.hi = self.lo = mid
            self.count = 0
        if gap:
            self.micro.clear()
            self.full = False
            self.base = self.hz = 0.0
            self.bars.clear()
        self.hi = max(self.hi, mid)
        self.lo = min(self.lo, mid)
        if not old or (q.ask, q.bid) != (old.ask, old.bid):
            self.count += 1
            self.micro.append((q.ms, mid))
        while self.micro and self.micro[0][0] < q.ms - 2500:
            self.micro.popleft()
        self.f = None
        if not self.full or self.base <= 0 or self.hz <= 0:
            return True
        w = self.micro
        n = len(w)
        first = w[0][1] if n else mid
        move = mid - first
        up = down = 0.0
        last = first
        for _, p in w:
            change = p - last
            if change > 0:
                up += change
            else:
                down -= change
            last = p
        eff = move / (up + down) if up + down > 0 else 0.0
        pace = n / (2.5 * self.hz)
        ran = self.hi - self.lo
        ratio = ran / self.base
        body = (mid - self.op) / ran if ran else 0.0
        side = 1 if body > 0 else -1
        eligible = (
            6000 <= q.ms % 60000 <= 52000
            and q.spread <= self.spec.cap + 1e-12
            and ran >= max(1.4 * self.base, 4 * q.spread, 8 * self.spec.tick)
            and abs(body) >= 0.65
            and n >= 4
            and pace >= 1.35
            and side * eff >= 0.46
            and side * move >= max(0.7 * q.spread, 2 * self.spec.tick)
        )
        self.f = Feature(
            self.base,
            ratio,
            pace,
            eff,
            move,
            up / 2.5,
            down / 2.5,
            side if eligible else 0,
            ratio * min(pace, 4.0) * abs(eff) * abs(body) if eligible else 0.0,
        )
        return True


@dataclass(frozen=True, slots=True)
class Leg:
    price: float
    sl: float


def grid(value, step, up):
    return round(
        (ceil(value / step - 1e-9) if up else floor(value / step + 1e-9)) * step, 10
    )


def plan(spec, q, f, equity, style="shallow"):
    if not f or f.side not in (-1, 1) or not q.valid() or q.spread > spec.cap + 1e-12:
        return []
    s = f.side
    near = max(2 * spec.tick, spec.gap + spec.tick, 0.22 * q.spread, 0.035 * f.base)
    if style == "patient":
        near = max(near, 0.90 * q.spread, 0.22 * f.base)
    elif style != "shallow":
        return []
    spacing = max(spec.tick, 0.12 * q.spread)
    stop = max(2 * q.spread, 0.30 * f.base, spec.gap + q.spread + 2 * spec.tick)
    legs = []
    for j in range(LEGS):
        p = grid(q.entry(s) - s * (near + j * spacing), spec.tick, s < 0)
        sl = grid(p - s * stop, spec.tick, s < 0)
        legs.append(Leg(p, sl))
    if min(min(x.price, x.sl) for x in legs) <= 0:
        return []
    risk = sum(
        LOTS * ((abs(l.price - l.sl) + spec.reserve) * spec.contract + 2 * FEE)
        for l in legs
    )
    return legs if isfinite(risk) and risk <= risk_budget(equity) + 1e-9 else []


def cancel_reason(spec, q, f, s, price, age):
    if age >= TTL:
        return "ttl"
    if q.spread > spec.cap + 1e-12:
        return "spread"
    if not f:
        return "unverified_flow"
    if s * f.efficiency < -0.30 and s * f.move < -0.25 * q.spread:
        return "opposite_flow"
    if age < 650:
        return ""
    if f.pace < 0.55:
        return "quiet_tape"
    distance = max(0.0, s * (q.entry(s) - price))
    toward = f.down_speed if s == 1 else f.up_speed
    remaining = max(0, TTL - age) / 1000
    if distance > max(q.spread * 0.7, 3 * spec.tick) and distance > max(
        2 * spec.tick, toward * remaining
    ):
        return "unlikely_reach"
    if distance > max(1.6 * q.spread, 0.35 * f.base) and s * f.efficiency > 0.5:
        return "runaway"
    return ""


def early_exit(q, f, s, net, peak, age, adverse_ms, limit):
    if net >= TARGET:
        return "basket_target"
    if net <= -limit:
        return "basket_loss"
    if peak >= 1.20 and net <= peak - 0.65:
        return "profit_giveback"
    if (
        adverse_ms >= 350
        and f
        and s * f.efficiency < -0.38
        and s * f.move < -0.35 * q.spread
        and f.pace >= 0.65
        and net < 0
    ):
        return "flow_reversal"
    if age >= 10000 and net <= 0:
        return "stalled"
    if age >= HOLD:
        return "time_stop"
    return ""
