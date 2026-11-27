"""Reproducible session study. Run stages separately to freeze the model before final testing."""

import argparse
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from pulsefive.data import check_cache, manifest

from .fit import confirm, fit
from .ticklab import labels, portfolio

JOBS = (("2026-06", False), ("2026-07", False), ("2026-08", False), ("2026-08", True))


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("stage", choices=["labels", "fit", "confirm", "test"])
    p.add_argument("--cache", type=Path, required=True)
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--jobs", type=int, default=2)
    a = p.parse_args(argv)
    a.output.mkdir(parents=True, exist_ok=True)
    if a.stage != "fit":
        check_cache(
            a.cache,
            {
                "labels": ["2026-06", "2026-07", "2026-08"],
                "confirm": ["2026-09"],
                "test": [k for k in manifest() if k.startswith("2026-11-")],
            }[a.stage],
        )
    if a.stage == "labels":
        with ProcessPoolExecutor(max_workers=max(1, min(4, a.jobs))) as pool:
            fs = [pool.submit(labels, a.cache, k, v, a.work) for k, v in JOBS]
            metas = [f.result() for f in fs]
        (a.output / "label-counts.json").write_text(
            json.dumps(
                dict(zip([x + ("-stress" if y else "-base") for x, y in JOBS], metas)),
                indent=2,
            )
            + "\n"
        )
        return
    if a.stage == "fit":
        fit(a.work, a.output)
        return
    frozen = a.output / "frozen.json"
    digest = a.output / "freeze.sha256"
    if a.stage == "confirm":
        art = json.loads((a.output / "provisional.json").read_text())
        keys = ["2026-09"]
        modes = ["challenger"]
    else:
        if (
            hashlib.sha256(frozen.read_bytes()).hexdigest()
            != digest.read_text().split()[0]
        ):
            raise ValueError("Freeze hash mismatch")
        art = json.loads(frozen.read_text())
        keys = sorted(k for k in manifest() if "2026-11-" in k)
        modes = ["baseline", "challenger", "release"]
    with ProcessPoolExecutor(max_workers=max(1, min(2, a.jobs))) as pool:
        fs = [
            pool.submit(
                portfolio, a.cache, keys, v, art, modes, a.output / (a.stage + "-" + c)
            )
            for v, c in [(False, "base"), (True, "stress")]
        ]
        reports = [f.result() for f in fs]
    if a.stage == "confirm":
        confirm(art, *reports, frozen)
        digest.write_text(
            hashlib.sha256(frozen.read_bytes()).hexdigest() + "  frozen.json\n"
        )
        print("FROZEN", digest.read_text().strip())
    else:
        print("TEST of", digest.read_text().strip())
        print({m: [r["policies"][m]["net"] for r in reports] for m in modes})


if __name__ == "__main__":
    main()
