import unittest
from dataclasses import replace

from pulsefive.engine import Engine, Memory, View
from pulsefive.example import run
from pulsefive.model import Rules
from pulsefive.paper import Paper
from pulsefive.policy import GOLD, Leg, Quote

from .test_policy import F, Q, T, ms

N = GOLD.name


def start(p=None, r=None, j=None):
    p = p or Paper([GOLD])
    j = j or Memory()
    e = Engine([GOLD], p, j, r or Rules("baseline"))
    p.last[N] = Q
    e.tapes[N].q = Q
    e.tapes[N].f = F
    return e, p, j


def fire(e, w=T):
    e.pump(w, [(1.0, N)])


class EngineTests(unittest.TestCase):
    def test_whole_batch_sides_and_examples(self):
        r = run("target")["settled"]
        self.assertEqual(
            (r["legs_sent"], r["legs_filled"], r["reason"]), (5, 5, "basket_target")
        )
        self.assertGreaterEqual(r["net"], 2)
        r = run("partial")["settled"]
        self.assertEqual(r["legs_filled"], 1)
        r = run("nonfill")["settled"]
        self.assertEqual((r["net"], r["legs_filled"]), (0, 0))

    def test_cancel_fill_race_and_protective_stops(self):
        z = run("late-fill")
        self.assertEqual(z["server_events"]["fill_during_cancel"], 5)
        self.assertEqual(z["settled"]["legs_filled"], 5)
        self.assertLess(z["settled"]["net"], 0)
        self.assertGreater(run("reversal")["server_events"]["native_stops"], 0)

    def test_target_intent_does_not_book_at_threshold(self):
        r = run("target-missed")["settled"]
        self.assertEqual(r["reason"], "basket_target")
        self.assertLess(r["net"], 0)

    def test_latency_penetration_and_sell_valuation(self):
        p = Paper([GOLD])
        b = dict(name=N, id="b", side=-1)
        k = p.place(b, 0, Leg(2000.30, 2000.80), T, Q)
        p.arrive(N, Quote(T + 149, 2000.32, 2000.56), T + 149)
        self.assertIn(k, p.orders)
        p.arrive(N, Quote(T + 150, 2000.30, 2000.54), T + 150)
        self.assertIn(k, p.orders)
        p.arrive(N, Quote(T + 151, 2000.302, 2000.542), T + 151)
        self.assertIn(k, p.pos)
        q = Quote(T + 200, 1999.50, 1999.74)
        p.close(b, k, 0.01, q.ms, q)
        p.arrive(N, q, q.ms)
        self.assertIn(k, p.pos)
        p.arrive(N, replace(q, ms=T + 350), T + 350)
        self.assertNotIn(k, p.pos)
        self.assertAlmostEqual(p.balance - 1000, 2000.30 - (1999.74 + 0.02) - 0.07)

    def test_cancel_or_expire_without_a_price_update(self):
        p = Paper([GOLD])
        b = dict(name=N, id="b", side=1)
        l = Leg(2000.0, 1999.5)
        k = p.place(b, 0, l, T, Q)
        p.cancel(b, k, T + 50)
        p.advance(T + 199)
        self.assertIn(k, p.orders)
        p.advance(T + 200)
        self.assertNotIn(k, p.orders)
        k = p.place(b, 0, l, T, Q)
        p.advance(T + 90000)
        self.assertNotIn(k, p.orders)

    def test_margin_spread_staleness_and_session_gate(self):
        e, p, j = start(Paper([GOLD], 1000.0, 2.0))
        fire(e)
        self.assertIsNone(e.s.get("batch"))
        self.assertEqual(j.events[-1]["event"], "admission_veto")
        e, p, j = start()
        fire(e, T + 501)
        self.assertIsNone(e.s.get("batch"))
        e.tapes[N].q = Quote(T, 2000.0, 2000.9)
        fire(e)
        self.assertIsNone(e.s.get("batch"))
        e.tapes[N].q = replace(Q, ms=ms("2026-11-07T13:10"))
        fire(e, e.tapes[N].q.ms)
        self.assertIsNone(e.s.get("batch"))

    def test_day_loss_attempts_and_cooldown(self):
        for st in [
            dict(day_locked=True),
            dict(day_pnl=-20.0),
            dict(attempts=20),
            dict(per_session={"London": 4}),
            dict(after=T + 1),
        ]:
            e, p, j = start()
            e.s.update(
                day=T // 86400000,
                start_eq=1000,
                attempts=0,
                day_pnl=0,
                day_locked=False,
                per_session={},
            )
            e.s.update(st)
            fire(e)
            self.assertIsNone(e.s.get("batch"))

    def test_one_global_basket_and_stop_if_quote_moves_before_all_five(self):
        e, p, j = start()
        fire(e)
        b = e.s["batch"]
        self.assertEqual(len(b["ids"]), 1)
        e.tapes[N].f = replace(F, side=0)
        fire(e, T + 50)
        self.assertFalse(b["stage"])
        self.assertEqual(len(b["ids"]), 1)
        self.assertEqual(e.s["attempts"], 1)

    def test_session_handoff_drains(self):
        t = ms("2026-11-03T12:59:59.800")
        e, p, j = start()
        e.tapes[N].q = replace(Q, ms=t)
        p.last[N] = e.tapes[N].q
        fire(e, t)
        b = e.s["batch"]
        self.assertEqual(b["session"], "London")
        e.tapes[N].q = replace(Q, ms=t + 200)
        e.pump(t + 200)
        self.assertEqual(b["exit"], "session_end")
        self.assertTrue(b["cancels"])

    def test_uncertain_ack_is_not_resent_on_restart(self):
        class Lost(Paper):
            def place(self, *args):
                super().place(*args)
                raise OSError("transport timeout")

        e, p, j = start(Lost([GOLD]))
        fire(e)
        self.assertTrue(e.s["hold"])
        self.assertIn("intent", e.s)
        self.assertEqual(len(p.orders), 1)
        e2 = Engine([GOLD], p, j, Rules("baseline"))
        fire(e2, T + 50)
        self.assertIn("unfinished", e2.s["hold"])
        self.assertEqual(len(p.orders), 1)

    def test_clean_restart_drains_instead_of_restaging(self):
        e, p, j = start()
        fire(e)
        b = e.s["batch"]
        e2 = Engine([GOLD], p, j, Rules("baseline"))
        self.assertEqual(b["exit"], "restart_drain")
        self.assertFalse(b["stage"])
        e2.pump(T + 50)
        self.assertTrue(b["cancels"])

    def test_ambiguous_flatness_and_partial_close(self):
        class Receipts(Paper):
            def __init__(self):
                super().__init__([GOLD])
                self.snap = None
                self.sent = []

            def view(self, *args):
                return self.snap if self.snap is not None else super().view(*args)

            def close(self, b, k, vol, now, q):
                self.sent.append((k, vol))
                return True

        e, p, j = start(Receipts())
        fire(e)
        e.stopping = True
        p.snap = View(
            positions={502: 0.01}, entered=0.01, first_fill=T + 1, filled_legs=1
        )
        e.pump(T + 50)
        e.pump(T + 100)
        self.assertEqual(p.sent, [(502, 0.01)])
        p.snap.positions[502] = 0.004
        p.snap.exited = 0.006
        e.pump(T + 150)
        self.assertEqual(p.sent[-1], (502, 0.004))
        p.snap.positions.clear()
        p.snap.exited = 0.01
        p.snap.booked = -0.10
        e.pump(T + 200)
        self.assertIsNotNone(e.s["batch"])
        p.snap.proven = True
        e.pump(T + 250)
        self.assertIsNone(e.s["batch"])
        self.assertAlmostEqual(e.s["day_pnl"], -0.10)

    def test_queued_exit_waits_for_real_next_quote(self):
        e, p, j = start()
        fire(e)
        b = e.s["batch"]
        k = b["ids"][0]
        q = Quote(T + 200, 1999.86, 2000.10)
        p.arrive(N, q, q.ms)
        e.tapes[N].q = q
        e.stopping = True
        e.pump(q.ms)
        for dt in [1200, 6500, 7600]:
            p.advance(T + dt)
            e.pump(T + dt)
        self.assertFalse(e.s.get("hold"))
        self.assertIn(k, p.pos)
        self.assertEqual(p.counts["exit_waited_over_5s_for_quote"], 1)
        q = Quote(T + 8500, 1998.0, 1998.24)
        p.arrive(N, q, q.ms)
        e.tapes[N].q = q
        e.pump(q.ms)
        self.assertFalse(e.s.get("batch"))
        self.assertLess(e.s["day_pnl"], -2.0)

    def test_unverified_action_timeout_still_freezes(self):
        e, p, j = start()
        fire(e)
        b = e.s["batch"]
        k = b["ids"][0]
        b["actions"]["c" + str(k)] = [T, 0]
        e.pump(T + 6000)
        self.assertIn("not reconciled", e.s["hold"])
