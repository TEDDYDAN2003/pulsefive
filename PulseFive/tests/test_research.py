import csv
import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime

from pulsefive.data import ROOT, manifest
from pulsefive.model import NAMES

try:
    import numpy as np
    import pandas as pd

    from research.fit import calendar_stats, fit, passed
    from research.ticklab import Day

    has_research = True
except ImportError:
    has_research = False


class ShippedEvidence(unittest.TestCase):
    def test_source_dates_disjoint_and_cutoff(self):
        x = manifest()
        r = json.loads((ROOT / "research" / "tick-manifest.json").read_text())
        self.assertEqual(set(r), set(x))
        last = -1
        for k, v in sorted(r.items()):
            self.assertEqual(x[k]["sha256"], v["sha256"])
            self.assertGreater(v["first_ms"], last)
            last = v["last_ms"]
            self.assertEqual(
                datetime.fromtimestamp(last / 1000, UTC).isoformat()[: len(k)], k
            )
        self.assertEqual(
            datetime.fromtimestamp(last / 1000, UTC).date().isoformat(), "2026-11-17"
        )
        self.assertNotIn("2026-10", r)

    def test_frozen_hash_and_no_retroactive_promotion(self):
        folder = ROOT / "research" / "study"
        src = (folder / "frozen.json").read_bytes()
        self.assertEqual(
            hashlib.sha256(src).hexdigest(),
            (folder / "freeze.sha256").read_text().split()[0],
        )
        self.assertEqual(src, (ROOT / "pulsefive" / "model_v1.json").read_bytes())
        model = json.loads(src)
        for s, m in model["sessions"].items():
            self.assertEqual(
                m["approved"], m["validation_pass"] and m["confirmation_pass"]
            )
            self.assertFalse(m["approved"])

    def test_csv_cash_for_every_session_and_policy(self):
        for kind in ("confirm", "test"):
            for cost in ("base", "stress"):
                d = ROOT / "research" / "study" / (kind + "-" + cost)
                rep = json.loads((d / "summary.json").read_text())
                for name, a in rep["policies"].items():
                    with (d / (name + ".csv")).open() as f:
                        rows = list(csv.DictReader(f))
                    self.assertEqual(len({r["id"] for r in rows}), len(rows))
                    self.assertAlmostEqual(
                        a["ending_balance"],
                        1000 + sum(float(r["net"]) for r in rows),
                        places=4,
                    )
                    for s in NAMES:
                        rs = [r for r in rows if r["session"] == s]
                        z = a["sessions"][s]
                        self.assertAlmostEqual(
                            z["net"], sum(float(r["net"]) for r in rs), places=4
                        )
                        self.assertEqual(z["attempts"], len(rs))
                        self.assertEqual(
                            z["filled"], sum(int(r["legs_filled"]) > 0 for r in rs)
                        )
                        self.assertEqual(
                            z["realized_target"], sum(float(r["net"]) >= 2 for r in rs)
                        )


@unittest.skipUnless(has_research, "install research extra for numerical tests")
class NumericTests(unittest.TestCase):
    def test_no_lookahead_on_features_or_candidate_edges(self):
        start = 1780272000000
        ms = start + np.arange(23 * 60000 // 200) * 200
        bid = (2000000 + np.resize(np.repeat([0, 20, 80, 30], 5), len(ms))).astype(
            "int32"
        )
        bid[-240:] += np.arange(240, dtype="int32") * 50
        book = np.column_stack([bid, bid + 240])
        cut = len(ms) - 120
        day = Day(ms, book, False)
        v = book.copy()
        v[cut:] += 2000000
        div = Day(ms, v, False)
        pre = Day(ms[:cut], book[:cut], False)
        np.testing.assert_array_equal(day.f[:cut], div.f[:cut])
        np.testing.assert_array_equal(day.f[:cut], pre.f)
        np.testing.assert_array_equal(day.edges[day.edges < cut], pre.edges)
        self.assertGreater(len(day.edges), 0)
        self.assertEqual(day.parity(), len(ms))

    def test_acceptance_hurdles_include_zero_trade_dates(self):
        g = pd.DataFrame(
            [
                dict(ms=3000, date="a", filled=1, closed_ms=4000, net=5.0),
                dict(ms=4500, date="a", filled=1, closed_ms=4520, net=10.0),
                dict(ms=6500, date="a", filled=0, closed_ms=6550, net=0.0),
            ]
        )
        a = calendar_stats(g, ["a", "b", "c"])
        self.assertEqual((a["attempts"], a["net"], a["filled"]), (2, 5.0, 1))
        self.assertAlmostEqual(a["mean"], 5 / 3)
        self.assertFalse(passed(a))

    def test_refit_from_shipped_labels_without_november(self):
        folder = ROOT / "research"
        expected = json.loads((folder / "study" / "provisional.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            actual = fit(folder / "labels", tmp)
        for s in NAMES:
            a = actual["sessions"][s]
            e = expected["sessions"][s]
            self.assertEqual(a["key"], e["key"])
            self.assertEqual(a["validation_pass"], e["validation_pass"])
            self.assertAlmostEqual(a["threshold"], e["threshold"], places=9)
            np.testing.assert_allclose(a["fit"]["coef"], e["fit"]["coef"], atol=1e-9)
