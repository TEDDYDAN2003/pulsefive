"""User-facing commands. None can transmit a broker order."""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from .example import CASES
from .model import Rules


def status():
    rules = Rules()
    model = rules.artifact
    digest = hashlib.sha256(
        (Path(__file__).parent / "model_v1.json").read_bytes()
    ).hexdigest()
    print(
        "PulseFive | XAUUSDm | PAPER ONLY\nFive passive 0.01-lot tickets; $2 total target, not promised.\n"
    )
    for session, r in model["sessions"].items():
        print(
            f"{session:<11} {'ELIGIBLE' if r['approved'] else 'ABSTAIN':<10} {r['reason']}"
        )
    print("\nFrozen artifact SHA-256:", digest)
    print(
        "Run `pulsefive example` for synthetic order mechanics; `pulsefive forward --help` for real-quote local paper observation."
    )


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="pulsefive",
        description="Session-aware gold limit-basket research • paper only",
    )
    s = p.add_subparsers(dest="cmd")
    s.add_parser("status", help="Show frozen session gates; no network")
    e = s.add_parser(
        "example", help="Synthetic five-ticket mechanics; does not measure returns"
    )
    e.add_argument("--case", choices=CASES + ("all",), default="all")
    e.add_argument("--events", action="store_true")
    f = s.add_parser(
        "forward",
        help="Read current quotes from a local demo MT5; all trading is simulated",
    )
    f.add_argument("--minutes", type=float, default=90)
    f.add_argument("--profile", choices=("release", "challenger"), default="release")
    f.add_argument(
        "--record",
        action="store_true",
        help="Also log quote prices and receipt times on this PC",
    )
    f.add_argument("--terminal-path", type=Path, help="Optional path to terminal64.exe")
    f.add_argument(
        "--logs",
        type=Path,
        default=Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
        / "PulseFive"
        / "forward",
    )
    d = s.add_parser(
        "prepare", help="Download if requested, hash-check and index the study archives"
    )
    d.add_argument("--archives", type=Path, required=True)
    d.add_argument("--cache", type=Path, required=True)
    d.add_argument("--download", action="store_true")
    l = s.add_parser("legacy", help="Reproduce the original October 12–16 UTC baseline")
    l.add_argument("--zip", type=Path, required=True)
    l.add_argument("--cache", type=Path, required=True)
    l.add_argument("--start", required=True)
    l.add_argument("--end", required=True)
    l.add_argument("--stress", action="store_true")
    l.add_argument("--output", required=True, type=Path)
    a = p.parse_args(argv)
    if a.cmd in (None, "status"):
        status()
        return 0
    if a.cmd == "example":
        from .example import run

        for c in CASES if a.case == "all" else [a.case]:
            v = run(c)
            r = v["settled"]
            print(
                f"SYNTHETIC | {c:<10} {r['legs_sent']} sent, {r['legs_filled']} filled | {r['reason']:<16} {r['net']:+.3f} USD"
            )
            if a.events:
                print(json.dumps(v["events"], indent=2))
    elif a.cmd == "forward":
        if sys.platform != "win32":
            p.exit(
                2,
                "Forward paper requires Windows and an already connected local MT5 demo. No connection attempted.\n",
            )
        if not 0 < a.minutes <= 1440:
            p.error("--minutes must be in (0,1440]")
        try:
            import MetaTrader5 as mt
        except ImportError:
            p.exit(2, "Install the mt5 extra on 64-bit Windows; see README.\n")
        if not (
            mt.initialize(str(a.terminal_path)) if a.terminal_path else mt.initialize()
        ):
            p.exit(2, "Local terminal initialization failed; no orders sent.\n")
        from .forward import FeedError, observe

        try:
            return (
                2
                if observe(mt, a.minutes, a.logs, a.profile, a.record)["status"]
                == "censored"
                else 0
            )
        except FeedError as exc:
            p.exit(2, str(exc) + "\n")
        finally:
            mt.shutdown()
    elif a.cmd == "prepare":
        from .data import build

        r = build(a.archives, a.cache, download=a.download)
        print(
            "Verified",
            len(r),
            "archives,",
            sum(v["ticks"] for v in r.values()),
            "quotes",
        )
    elif a.cmd == "legacy":
        from .replay import prepare, replay

        print(
            json.dumps(
                replay(
                    prepare(a.zip, a.cache), a.start, a.end, a.stress, output=a.output
                ),
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
