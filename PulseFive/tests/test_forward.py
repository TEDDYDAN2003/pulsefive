import ast
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from pulsefive.cli import main
from pulsefive.data import ROOT
from pulsefive.forward import Cursor, FeedError, QuoteFeed, observe, pump_quotes
from pulsefive.policy import Quote
from pulsefive.store import Store

from .test_engine import fire, start
from .test_policy import T


def row(t, b=2000.0):
    return dict(time_msc=t, bid=b, ask=b + 0.24)


class DemoFeed:
    ACCOUNT_TRADE_MODE_DEMO = 0
    COPY_TICKS_INFO = 1

    def __init__(self):
        self.mode = 0
        self.server = "demo"
        self.connected = True
        self.book = []

    def account_info(self):
        return NS(login=101, server=self.server, trade_mode=self.mode)

    def terminal_info(self):
        return NS(connected=self.connected)

    def symbol_info(self, n):
        return NS(
            visible=True,
            currency_base="XAU",
            currency_profit="USD",
            trade_contract_size=100,
            trade_tick_size=0.001,
        )

    def copy_ticks_range(self, *args):
        return self.book

    def order_send(self, *args):
        raise AssertionError("No native requests permitted")


class ForwardTests(unittest.TestCase):
    def test_inclusive_prefix_duplicate_and_future_checks(self):
        c = Cursor(T)
        a = row(T)
        b = row(T, 2000.01)
        d = row(T + 1)
        self.assertEqual(len(c.consume([a, a, b, d], T)), 3)
        self.assertEqual(c.consume([a, a, b], T), [])
        self.assertEqual(
            [x.ms for x in c.consume([a, a, b, row(T, 2000.02), d], T + 1)], [T, T + 1]
        )
        self.assertEqual(c.consume([d], T + 2), [])

    def test_changed_prefix_missing_tick_or_bad_data_censors(self):
        for x in [
            [row(T, 1000.0)],
            [],
            [row(T + 1), row(T)],
            [row(T), dict(time_msc=T + 1, bid=2.0, ask=1.0)],
        ]:
            c = Cursor(T)
            c.consume([row(T)], T)
            self.assertRaises(FeedError, c.consume, x, T + 5)

    def test_reject_wrong_or_switched_account_and_missing_history(self):
        mt = DemoFeed()
        mt.mode = 2
        self.assertRaises(FeedError, QuoteFeed, mt, T)
        mt.mode = 0
        f = QuoteFeed(mt, T)
        mt.book = [row(T)]
        self.assertEqual(len(f.read(T)), 1)
        mt.server = "other"
        self.assertRaises(FeedError, f.read, T + 5500)
        mt.server = "demo"
        mt.book = None
        self.assertRaises(FeedError, f.read, T + 5500)

    def test_forward_does_not_fill_on_an_earlier_tick_in_same_poll(self):
        e, p, j = start()
        fire(e)
        k = e.s["batch"]["ids"][0]
        qs = [Quote(T + 50, 1900, 1900.24), Quote(T + 151, 2000.8, 2001.04)]
        pump_quotes(e, qs, T + 155)
        self.assertIn(k, p.orders)
        self.assertNotIn(k, p.pos)

    def test_forward_writes_receipt_and_zero_native_requests(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("pulsefive.forward.clock", side_effect=[T, T, T + 1, T + 75, T + 80]),
            patch("pulsefive.forward.time.sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            mt = DemoFeed()
            mt.book = [row(T)]
            s = observe(mt, 0.001, tmp, record=True)
            self.assertEqual(
                (s["status"], s["raw_quotes"], s["broker_orders_sent"]),
                ("complete", 1, 0),
            )
            d = next(Path(tmp).iterdir())
            self.assertTrue((d / "ticks.csv").is_file())
            self.assertEqual(json.loads((d / "summary.json").read_text()), s)

    def test_feed_failure_not_a_zero_return_completed_observation(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("pulsefive.forward.clock", side_effect=[T, T, T + 100]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            mt = DemoFeed()
            mt.book = None
            s = observe(mt, 1, tmp)
            self.assertEqual(s["status"], "censored")
            self.assertIn("unavailable", s["reason"])

    def test_store_locks_persists_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Store(tmp, "alpha")
            a.s["counter"] = 4
            a.save()
            self.assertRaises(RuntimeError, Store, tmp, "alpha")
            a.close()
            b = Store(tmp, "alpha")
            self.assertEqual(b.s["counter"], 4)
            path = b.path
            b.close()
            z = json.loads(path.read_text())
            z["identity"] = "beta"
            path.write_text(json.dumps(z))
            self.assertRaises(RuntimeError, Store, tmp, "alpha")

    def test_cli_default_offline_and_no_native_transport_in_package(self):
        with contextlib.redirect_stdout(io.StringIO()) as f:
            self.assertEqual(main([]), 0)
        self.assertIn("PAPER ONLY", f.getvalue())
        self.assertIn("ABSTAIN", f.getvalue())
        for p in (ROOT / "pulsefive").glob("*.py"):
            send = [
                n
                for n in ast.walk(ast.parse(p.read_text()))
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("order_send", "login")
            ]
            self.assertFalse(send, p.name)
