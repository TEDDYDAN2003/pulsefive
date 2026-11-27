import unittest
from types import SimpleNamespace as NS

from pulsefive.engine import Block, Review
from pulsefive.policy import GOLD, MAGIC
from pulsefive.terminal import MT5Demo

from .test_policy import Q, T


class Terminal:
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_MARGIN_MODE_RETAIL_HEDGING = 2
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    DEAL_TYPE_BUY = 0
    DEAL_TYPE_SELL = 1
    DEAL_ENTRY_IN = 0
    DEAL_ENTRY_OUT = 1
    DEAL_ENTRY_OUT_BY = 3
    ORDER_STATE_CANCELED = 2
    ORDER_STATE_FILLED = 4
    ORDER_STATE_REJECTED = 5
    ORDER_STATE_EXPIRED = 6

    def __init__(self):
        self.orders = []
        self.positions = []
        self.hist = []
        self.deals = []
        self.mode = 0

    def account_info(self):
        return NS(
            login=101,
            server="demo",
            trade_mode=self.mode,
            margin_mode=2,
            currency="USD",
            trade_allowed=True,
            trade_expert=True,
            balance=1000.0,
            equity=1000.0,
            margin_free=950.0,
        )

    def terminal_info(self):
        return NS(connected=True, trade_allowed=True, tradeapi_disabled=False)

    def orders_get(self, **q):
        return self.orders

    def positions_get(self, **q):
        return self.positions

    def history_orders_get(self, *a):
        return self.hist

    def history_deals_get(self, *a):
        return self.deals

    def order_send(self, *a):
        raise AssertionError("disabled")


def opening():
    return NS(
        ticket=44,
        state=2,
        magic=MAGIC,
        symbol=GOLD.name,
        type=2,
        volume_initial=0.01,
        volume_current=0.004,
        price_open=2000.0,
        sl=1999.5,
    )


def deal(ticket, order, entry, vol, profit):
    return NS(
        ticket=ticket,
        order=order,
        entry=entry,
        type=0 if entry == 0 else 1,
        volume=vol,
        profit=profit,
        fee=0.0,
        swap=0.0,
        commission=-0.01,
        magic=MAGIC,
        symbol=GOLD.name,
        position_id=77,
        time_msc=T + 20,
    )


class ReceiptsTests(unittest.TestCase):
    def setUp(self):
        self.m = Terminal()
        self.a = MT5Demo(self.m, [GOLD], 101, "demo")
        self.b = dict(
            born=T,
            ids=[44],
            name=GOLD.name,
            side=1,
            legs=[dict(price=2000.0, sl=1999.5)],
        )

    def view(self):
        return self.a.view(self.b, T + 100, Q)

    def test_history_lag_is_not_flat(self):
        self.m.hist = [opening()]
        r = self.view()
        self.assertFalse(r.proven)
        self.assertFalse(r.can_stage)
        self.m.hist = None
        self.assertRaises(Review, self.view)

    def test_partial_entry_cancel_late_history_and_partial_exits(self):
        self.m.hist = [opening()]
        d = deal(1, 44, 0, 0.006, 0.0)
        self.m.deals = [d, d, deal(2, 98, 1, 0.002, -0.10)]
        self.m.positions = [
            NS(
                ticket=88,
                identifier=77,
                volume=0.004,
                type=0,
                sl=1999.5,
                magic=MAGIC,
                symbol=GOLD.name,
                profit=-0.30,
                swap=0.0,
            )
        ]
        r = self.view()
        self.assertFalse(r.proven)
        self.assertEqual(r.positions, {88: 0.004})
        self.assertAlmostEqual(r.entered, 0.006)
        self.assertEqual(r.filled_legs, 1)
        self.assertAlmostEqual(r.booked, -0.12)
        self.m.positions = []
        self.assertFalse(self.view().proven)
        self.m.deals.append(deal(3, 99, 1, 0.004, -0.30))
        r = self.view()
        self.assertTrue(r.proven)
        self.assertAlmostEqual(r.booked, -0.43)

    def test_wrong_ownership_or_modified_stop(self):
        o = opening()
        self.m.orders = [o]
        self.assertTrue(self.view().can_stage)
        o.sl = 0.0
        self.assertTrue(self.view().unsafe)
        o.magic = 999
        self.assertRaises(Review, self.view)

    def test_wrong_account_rejected_and_native_writes_permanently_disabled(self):
        self.m.mode = 2
        self.assertRaises(Review, self.a.account)
        self.m.mode = 0
        self.assertRaises(Block, self.a._mutation, {}, "place", lambda: None)
