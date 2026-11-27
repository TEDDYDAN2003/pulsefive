"""Quarantined MT5 demo receipt/preflight reference. No native order transport in this release."""

import time
from datetime import UTC, datetime
from math import isfinite

from .engine import Block, Review, View
from .policy import EUR, FEE, GOLD, LEGS, LOTS, MAGIC, Leg, Quote, Spec, risk_budget
from .sessions import session


def ms():
    return time.time_ns() // 1_000_000


def must(x, why):
    if x is None:
        raise Review(why + " unavailable (not an empty result)")
    return x


def read_quote(mt, name):
    x = mt.symbol_info_tick(name)
    if x is None:
        return None
    q = Quote(int(x.time_msc), float(x.bid), float(x.ask))
    return q if q.valid() else None


def specifications(mt, names):
    out = []
    if not names or len(set(names)) != len(names):
        raise Block("Use unique, exact broker symbols")
    for name in names:
        if not mt.symbol_select(name, True):
            raise Block("Unknown symbol " + name)
        i = must(mt.symbol_info(name), "specification")
        base = {"XAU": GOLD, "EUR": EUR}.get(i.currency_base)
        if (
            not base
            or i.currency_profit != "USD"
            or abs(i.trade_contract_size - base.contract) > 1e-8
        ):
            raise Block(
                "Only standard XAU/USD (100) or EUR/USD (100000) linear USD contracts supported"
            )
        if (
            min(i.trade_tick_size, i.point, i.volume_step) <= 0
            or not i.volume_min <= LOTS <= i.volume_max
            or abs(LOTS / i.volume_step - round(LOTS / i.volume_step)) > 1e-8
        ):
            raise Block("0.01 lot is not tradable on " + name)
        out.append(
            Spec(
                name,
                i.trade_tick_size,
                base.contract,
                base.cap,
                base.reserve,
                i.point * max(i.trade_stops_level, i.trade_freeze_level),
            )
        )
    return out


class MT5Demo:
    def __init__(self, mt, specs, login, server, killed=lambda: False, clock=ms):
        self.mt = mt
        self.sp = {s.name: s for s in specs}
        self.login = int(login)
        self.server = server
        self.killed = killed
        self.clock = clock
        self.account()

    def identity(self):
        a = must(self.mt.account_info(), "account")
        t = must(self.mt.terminal_info(), "terminal")
        if (
            a.login != self.login
            or a.server != self.server
            or a.trade_mode != self.mt.ACCOUNT_TRADE_MODE_DEMO
            or a.margin_mode != self.mt.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING
            or a.currency != "USD"
        ):
            raise Review(
                "Refusing: wrong account/server, real/contest, non-USD or non-hedging"
            )
        if (
            not t.connected
            or not t.trade_allowed
            or getattr(t, "tradeapi_disabled", False)
            or not a.trade_allowed
            or not a.trade_expert
        ):
            raise Block("Terminal/demo permissions unavailable")
        return a

    def account(self):
        a = self.identity()
        vals = (a.balance, a.equity, a.margin_free)
        if not all(isfinite(x) for x in vals):
            raise Review("Bad account values")
        return vals

    def inventory(self):
        return (
            must(self.mt.orders_get(), "orders"),
            must(self.mt.positions_get(), "positions"),
        )

    def fresh(self, name):
        q = read_quote(self.mt, name)
        if not q or not 0 <= self.clock() - q.ms <= 1000:
            raise Block("Stale or future quote")
        return q

    def capability(self, sp):
        i = must(self.mt.symbol_info(sp.name), "symbol")
        m = self.mt
        # MQL5 SYMBOL_ORDER_MODE bit flags: market=1, limit=2, SL=16.
        # SYMBOL_EXPIRATION_SPECIFIED=4. No fallback to an unbounded GTC order.
        if (
            i.trade_mode != m.SYMBOL_TRADE_MODE_FULL
            or (i.order_mode & 19) != 19
            or not (i.expiration_mode & 4)
            or not (i.filling_mode & 3)
            or i.trade_tick_size != sp.tick
            or i.trade_contract_size != sp.contract
            or i.currency_profit != "USD"
            or not i.volume_min <= LOTS <= i.volume_max
            or i.volume_step <= 0
            or abs(LOTS / i.volume_step - round(LOTS / i.volume_step)) > 1e-8
        ):
            raise Block(
                "Symbol mode, size, protection, exit fill or server expiration unsupported"
            )
        return i

    def money(self, sp, s, legs, eq, free, remaining):
        m = self.mt
        typ = m.ORDER_TYPE_BUY if s == 1 else m.ORDER_TYPE_SELL
        risk = margin = 0.0
        for l in legs:
            loss = m.order_calc_profit(typ, sp.name, LOTS, l.price, l.sl)
            if loss is None or not isfinite(loss) or loss >= 0:
                raise Block("Unverified cash stop")
            risk += -loss + LOTS * (sp.reserve * sp.contract + 2 * FEE)
        for l in remaining:
            v = m.order_calc_margin(typ, sp.name, LOTS, l.price)
            if v is None or not isfinite(v) or v <= 0:
                raise Block("Unverified margin")
            margin += v
        if risk > risk_budget(eq) + 1e-7 or free - margin < 0.20 * eq:
            raise Block("Batch risk or free-margin reserve")

    def admit(self, sp, q, s, legs, eq, free):
        self.capability(sp)
        orders, pos = self.inventory()
        if orders or pos:
            raise Block("A dedicated flat account is required")
        self.money(sp, s, legs, eq, free, legs)

    def _mutation(self, req, operation, check_again):
        # A simulated gain is not authorization to trade. This distribution has
        # no order_send call, real or demo. Keep request shapes for receipt tests.
        raise Block("Native transmission disabled in this research release")

    def place(self, b, j, leg, now, q):
        m = self.mt
        sp = self.sp[b["name"]]
        s = b["side"]
        self.capability(sp)
        legs = [Leg(**l) for l in b["legs"]]
        if not (
            j == len(b["ids"]) < LEGS
            and len(legs) == LEGS
            and leg == legs[j]
            and b["stage"]
            and not b["exit"]
        ):
            raise Block("Unreserved leg")
        typ = m.ORDER_TYPE_BUY_LIMIT if s == 1 else m.ORDER_TYPE_SELL_LIMIT

        def recheck():
            now2 = self.clock()
            z = self.fresh(sp.name)
            info = self.capability(sp)
            a = self.identity()
            if (
                self.killed()
                or session(now2) != b["session"]
                or now2 - b["born"] > 650
                or not 0 <= now2 - b["signal_ms"] <= 700
                or z.ms < q.ms
                or (z.bid, z.ask) != (q.bid, q.ask)
                or z.spread > sp.cap + 1e-12
            ):
                raise Block("Signal/quote changed or kill/session gate")
            oo, pp = self.inventory()
            ids = set(b["ids"])
            if (
                pp
                or {o.ticket for o in oo} != ids
                or any(
                    o.magic != MAGIC or o.symbol != sp.name or o.type != typ for o in oo
                )
            ):
                raise Block("Inventory changed during staging")
            gap = max(
                sp.tick,
                info.point * max(info.trade_stops_level, info.trade_freeze_level),
            )
            if (
                s * (z.entry(s) - leg.price) < gap - sp.tick * 1e-6
                or s * (leg.price - leg.sl) < gap - sp.tick * 1e-6
            ):
                raise Block("Price/stop/freeze distance")
            # Conservative reservation includes accepted pending orders even if a broker already reserves their margin.
            self.money(sp, s, legs, a.equity, a.margin_free, legs)

        recheck()
        req = dict(
            action=m.TRADE_ACTION_PENDING,
            symbol=sp.name,
            type=typ,
            volume=LOTS,
            price=leg.price,
            sl=leg.sl,
            magic=MAGIC,
            comment=b["id"] + "-" + str(j + 1),
            type_filling=m.ORDER_FILLING_RETURN,
            type_time=m.ORDER_TIME_SPECIFIED,
            expiration=now // 1000 + 90,
        )
        return self._mutation(req, "place", recheck)

    def view(self, b, now, q):
        m = self.mt
        name = b["name"]
        ids = set(b["ids"])
        s = b["side"]
        sp = self.sp[name]
        result = View()
        self.identity()
        orders, pos = self.inventory()
        if not ids:
            result.proven = True
            result.can_stage = not orders and not pos
            return result
        limit_type = m.ORDER_TYPE_BUY_LIMIT if s == 1 else m.ORDER_TYPE_SELL_LIMIT
        pos_type = m.POSITION_TYPE_BUY if s == 1 else m.POSITION_TYPE_SELL
        history = must(
            m.history_orders_get(
                datetime.fromtimestamp((b["born"] - 60000) / 1000, UTC),
                datetime.fromtimestamp(now / 1000 + 120, UTC),
            ),
            "order history",
        )
        deals = must(
            m.history_deals_get(
                datetime.fromtimestamp((b["born"] - 60000) / 1000, UTC),
                datetime.fromtimestamp(now / 1000 + 120, UTC),
            ),
            "deal history",
        )
        known = {}
        terminal = {}
        expected = {}
        foreign = False
        byid = {k: b["legs"][i] for i, k in enumerate(b["ids"])}
        for o in (*history, *orders):
            if o.ticket not in ids:
                continue
            if (
                o.magic != MAGIC
                or o.symbol != name
                or o.type != limit_type
                or abs(o.volume_initial - LOTS) > 1e-8
                or abs(o.price_open - byid[o.ticket]["price"]) > sp.tick * 0.51
            ):
                raise Review("Opening order identity mismatch")
            known[o.ticket] = o
        finals = {
            m.ORDER_STATE_CANCELED,
            m.ORDER_STATE_FILLED,
            m.ORDER_STATE_REJECTED,
            m.ORDER_STATE_EXPIRED,
        }
        for o in history:
            if o.ticket in ids and o.state in finals:
                terminal[o.ticket] = o
                expected[o.ticket] = (
                    o.volume_initial
                    if o.state == m.ORDER_STATE_FILLED
                    else o.volume_initial - o.volume_current
                )
        for o in orders:
            if o.ticket not in ids:
                foreign = True
                continue
            result.orders[o.ticket] = o.price_open
            if abs(o.sl - byid[o.ticket]["sl"]) > sp.tick * 0.51:
                result.unsafe = True
        openings = {}
        pids = {}
        opened_by = {}
        dedup = {d.ticket: d for d in deals}
        for d in dedup.values():
            if d.order not in ids:
                continue
            if (
                d.magic != MAGIC
                or d.symbol != name
                or d.entry != m.DEAL_ENTRY_IN
                or d.type != (m.DEAL_TYPE_BUY if s == 1 else m.DEAL_TYPE_SELL)
                or d.position_id <= 0
                or d.volume <= 0
            ):
                raise Review("Entry deal identity mismatch")
            if d.position_id in pids and pids[d.position_id] != d.order:
                raise Review("Conflicting position origin")
            pids[d.position_id] = d.order
            openings[d.order] = openings.get(d.order, 0.0) + d.volume
            opened_by[d.position_id] = opened_by.get(d.position_id, 0.0) + d.volume
            result.first_fill = min(result.first_fill or d.time_msc, d.time_msc)
        exited_by = {}
        observed = {}
        for d in dedup.values():
            if d.position_id not in pids:
                continue
            if d.symbol != name:
                raise Review("Position history symbol changed")
            if d.entry == m.DEAL_ENTRY_IN and d.order in ids:
                result.entered += d.volume
            elif d.entry in (m.DEAL_ENTRY_OUT, m.DEAL_ENTRY_OUT_BY):
                result.exited += d.volume
                exited_by[d.position_id] = exited_by.get(d.position_id, 0.0) + d.volume
            else:
                raise Review("Unattributed deal on owned position")
            result.booked += d.profit + d.commission + d.fee + d.swap
        for p in pos:
            if p.identifier not in pids:
                foreign = True
                continue
            if p.symbol != name or p.magic != MAGIC or p.type != pos_type:
                raise Review("Position identity mismatch")
            result.positions[p.ticket] = p.volume
            observed[p.identifier] = p.volume
            result.floating += p.profit + p.swap
            if abs(p.sl - byid[pids[p.identifier]]["sl"]) > sp.tick * 0.51:
                result.unsafe = True
        result.filled_legs = len(openings)
        if not all(isfinite(v) and v <= LOTS + 1e-8 for v in openings.values()):
            raise Review("Excess opening volume")
        aligned = all(
            abs(vol - exited_by.get(pid, 0.0) - observed.get(pid, 0.0)) < 1e-8
            for pid, vol in opened_by.items()
        )
        result.can_stage = (
            not foreign and set(known) == ids and set(result.orders) == ids and aligned
        )
        result.proven = (
            not (result.orders or result.positions)
            and set(terminal) == ids
            and aligned
            and all(abs(openings.get(k, 0.0) - v) < 1e-8 for k, v in expected.items())
        )
        # If unknown inventory exists while history is still publishing, never call it a clean flat account.
        if foreign:
            result.proven = False
        return result

    def cancel(self, b, k, now):
        m = self.mt

        def check():
            rows = must(m.orders_get(ticket=k), "cancel read-back")
            typ = m.ORDER_TYPE_BUY_LIMIT if b["side"] == 1 else m.ORDER_TYPE_SELL_LIMIT
            if (
                k not in b["ids"]
                or len(rows) != 1
                or (rows[0].ticket, rows[0].symbol, rows[0].magic, rows[0].type)
                != (k, b["name"], MAGIC, typ)
            ):
                raise Block("Order no longer verified working")

        check()
        return self._mutation(
            dict(action=m.TRADE_ACTION_REMOVE, symbol=b["name"], order=k, magic=MAGIC),
            "cancel",
            check,
        )

    def close(self, b, k, vol, now, q):
        m = self.mt
        name = b["name"]
        s = b["side"]
        i = must(m.symbol_info(name), "exit spec")
        # Current price only when the execution mode requires it. Always pin the hedging ticket.
        z = self.fresh(name)
        req = dict(
            action=m.TRADE_ACTION_DEAL,
            symbol=name,
            position=k,
            volume=vol,
            type=m.ORDER_TYPE_SELL if s == 1 else m.ORDER_TYPE_BUY,
            magic=MAGIC,
            comment=b["id"] + "-exit",
            deviation=20,
        )
        if i.filling_mode & 2:
            req["type_filling"] = m.ORDER_FILLING_IOC
        elif i.filling_mode & 1:
            req["type_filling"] = m.ORDER_FILLING_FOK
        else:
            raise Block("No verified exit fill mode")
        if i.trade_exemode != m.SYMBOL_TRADE_EXECUTION_MARKET:
            req["price"] = z.mark(s)

        def check():
            # Rejoin position identifier to actual opening order/deal, not its comment.
            z2 = self.fresh(name)
            v = self.view(b, self.clock(), z2)
            if k not in v.positions or abs(v.positions[k] - vol) > 1e-8:
                raise Block("Position changed before exit")

        check()
        return self._mutation(req, "close", check)
