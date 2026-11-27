import unittest
from dataclasses import replace
from datetime import UTC, datetime

from pulsefive.model import FEATURES, Rules, predict, vector
from pulsefive.policy import (
    EUR,
    GOLD,
    Feature,
    Quote,
    Tape,
    cancel_reason,
    daily_budget,
    early_exit,
    plan,
    risk_budget,
    window,
)
from pulsefive.sessions import session


def ms(s):
    return int(datetime.fromisoformat(s).replace(tzinfo=UTC).timestamp() * 1000)


T = ms("2026-11-03T12:32:10")
Q = Quote(T, 2000.0, 2000.24)
F = Feature(0.64, 1.8, 2.0, 0.70, 0.34, 0.2, 0.05, 1, 2.1)


class PolicyTests(unittest.TestCase):
    def test_dst_and_overlap(self):
        for utc, s in [
            ("2026-07-06T12:05", "London-NY"),
            ("2026-11-03T12:05", "London"),
            ("2026-11-03T13:05", "London-NY"),
            ("2026-10-28T12:05", "London-NY"),
            ("2026-11-03T18:30", "New York"),
            ("2026-11-03T01:00", "Asia"),
            ("2026-07-06T22:30", "Sydney"),
            ("2026-11-03T23:30", "Sydney"),
        ]:
            with self.subTest(utc=utc):
                self.assertEqual(session(ms(utc)), s)

    def test_closed_utc_block_and_rollover(self):
        for t in [
            "2026-11-03T00:24",
            "2026-11-03T23:58",
            "2026-11-03T22:00",
            "2026-07-06T21:00",
            "2026-11-07T13:00",
            "2026-11-08T23:45",
        ]:
            self.assertEqual(session(ms(t)), "")
        self.assertFalse(window(ms("2026-11-03T08:31")))
        self.assertEqual(session(ms("2026-11-03T08:31")), "London")

    def test_tape_order_and_current_bar_not_in_baseline(self):
        t = Tape(GOLD)
        start = T - T % 60000
        for m in range(22):
            for i in range(120):
                p = 2000.0 + 0.1 * (i % 2)
                self.assertTrue(
                    t.update(Quote(start + m * 60000 + i * 500, p, p + 0.24))
                )
        self.assertAlmostEqual(t.base, 0.1)
        self.assertIsNotNone(t.f)
        before = t.q
        self.assertFalse(t.update(before))
        self.assertFalse(t.update(Quote(before.ms - 1, before.bid, before.ask)))
        self.assertFalse(t.update(Quote(before.ms + 1, 2002, 2001)))
        t.update(Quote(before.ms + 50, 2020, 2020.24))
        self.assertAlmostEqual(t.base, 0.1)
        t.update(Quote(before.ms + 11000, 2020, 2020.24))
        self.assertIsNone(t.f)
        self.assertEqual(len(t.bars), 0)

    def test_cold_or_missing_minute_cannot_fire(self):
        t = Tape(GOLD)
        start = T - T % 60000
        for m in range(20):
            for i in range(100):
                t.update(
                    Quote(
                        start + m * 60000 + i * 50, 2000 + i * 0.02, 2000.20 + i * 0.02
                    )
                )
                self.assertIsNone(t.f)
        self.assertFalse(Quote(T, 2000.0, float("inf")).valid())
        self.assertFalse(Quote(T, -1.0, 1.0).valid())

    def test_ladder_grid_and_risk(self):
        for side in (1, -1):
            f = replace(F, side=side)
            l = plan(GOLD, Q, f, 1000)
            b = plan(GOLD, Q, f, 1000, "patient")
            self.assertEqual(len(l), 5)
            self.assertEqual(len(set(x.price for x in l)), 5)
            self.assertTrue(
                all(
                    side * (x.price - x.sl) > 0 and side * (Q.entry(side) - x.price) > 0
                    for x in l
                )
            )
            self.assertTrue(all(side * (s.price - u.price) > 0 for s, u in zip(l, b)))
            self.assertTrue(
                all(
                    abs(x.price / GOLD.tick - round(x.price / GOLD.tick)) < 1e-6
                    for x in l
                )
            )
            self.assertEqual(plan(GOLD, Q, f, 100), [])
        self.assertEqual(plan(GOLD, Quote(T, 2000.0, 2000.60), F, 1000), [])
        self.assertEqual(plan(GOLD, Q, F, 1000, "invalid"), [])
        self.assertEqual(risk_budget(1000), 5)
        self.assertEqual(daily_budget(600), 12)

    def test_cancel_logic(self):
        self.assertEqual(cancel_reason(GOLD, Q, F, 1, 1999, 6000), "ttl")
        self.assertEqual(cancel_reason(GOLD, Q, None, 1, 1999, 0), "unverified_flow")
        self.assertEqual(
            cancel_reason(GOLD, Q, replace(F, pace=0.1), 1, 2000, 700), "quiet_tape"
        )
        self.assertEqual(
            cancel_reason(GOLD, Q, replace(F, efficiency=-0.6, move=-0.3), 1, 2000, 50),
            "opposite_flow",
        )
        self.assertEqual(cancel_reason(GOLD, Q, F, 1, 1999, 3500), "unlikely_reach")

    def test_exit_hierarchy(self):
        cases = [
            (2.0, 2.0, 100, 0, "basket_target"),
            (-5.0, 0, 100, 0, "basket_loss"),
            (0.5, 1.3, 500, 0, "profit_giveback"),
            (-0.1, 0, 10000, 0, "stalled"),
            (0.5, 0.6, 25000, 0, "time_stop"),
        ]
        for n, p, a, rev, why in cases:
            self.assertEqual(early_exit(Q, F, 1, n, p, a, rev, 5), why)
        self.assertEqual(
            early_exit(
                Q, replace(F, efficiency=-0.6, move=-0.4), 1, -0.2, 0, 1200, 350, 5
            ),
            "flow_reversal",
        )

    def test_model_inputs_and_refusal(self):
        x = vector(GOLD, Q, F)
        self.assertEqual(len(x), len(FEATURES))
        self.assertEqual(
            predict(
                dict(intercept=1, mean=x, scale=[1] * len(x), coef=[2] * len(x)), x
            ),
            1,
        )
        r = Rules()
        self.assertIsNone(r.decide(GOLD, Q, F))
        self.assertIsNone(Rules("baseline").decide(EUR, Q, F))
        self.assertIsNone(Rules("baseline").decide(replace(GOLD, tick=0.01), Q, F))
        self.assertRaises(ValueError, Rules, "bogus")
        self.assertRaises(ValueError, Rules, "release", {})
