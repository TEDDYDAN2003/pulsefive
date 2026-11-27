"""Checksummed Exness ZIP loader. No account credentials. Archive files remain external."""

import hashlib
import json
import os
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def manifest():
    return json.loads((ROOT / "research" / "sources.json").read_text())


def check_cache(cache, keys):
    checks = json.loads((ROOT / "research" / "cache-hashes.json").read_text())
    for k in keys:
        for n, h in checks[k].items():
            with (Path(cache) / k / n).open("rb") as f:
                got = hashlib.file_digest(f, "sha256").hexdigest()
            if got != h:
                raise ValueError(
                    "Prepared data hash mismatch: "
                    + k
                    + "/"
                    + n
                    + "; rebuild into a new cache"
                )


def build(archive_dir, cache, *, download=False):
    import shutil
    from urllib.request import urlopen

    import numpy as np
    import pandas as pd

    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    src = Path(archive_dir)
    src.mkdir(parents=True, exist_ok=True)
    records = {}
    for k, m in manifest().items():
        z = src / (k + ".zip")
        folder = cache / k
        folder.mkdir(exist_ok=True)
        ok = folder / "complete.json"
        if not z.exists() and download:
            part = z.with_suffix(".partial")
            with urlopen(m["url"], timeout=45) as f, part.open("wb") as w:
                shutil.copyfileobj(f, w)
            with part.open("rb") as f:
                digest = hashlib.file_digest(f, "sha256").hexdigest()
            if digest != m["sha256"]:
                raise ValueError("Unexpected downloaded data " + k)
            os.replace(part, z)
        with z.open("rb") as f:
            digest = hashlib.file_digest(f, "sha256").hexdigest()
        if digest != m["sha256"]:
            raise ValueError("ZIP hash mismatch: " + k)
        if ok.exists() and json.loads(ok.read_text()).get("sha256") == digest:
            rec = json.loads(ok.read_text())
            if tuple((folder / x).stat().st_size for x in ["time.i8", "book.i4"]) == (
                rec["ticks"] * 8,
                rec["ticks"] * 8,
            ):
                records[k] = rec
                continue
        low = datetime.fromisoformat(k + ("-01" if len(k) == 7 else "")).replace(
            tzinfo=UTC
        )
        high = (
            (low + timedelta(days=32)).replace(day=1)
            if len(k) == 7
            else low + timedelta(days=1)
        )
        count = 0
        prev = -1
        first = None
        ok.unlink(missing_ok=True)
        with (
            zipfile.ZipFile(z) as zz,
            zz.open(m["member"]) as f,
            (folder / "time.i8").open("wb") as tm,
            (folder / "book.i4").open("wb") as bk,
        ):
            for dt in pd.read_csv(f, chunksize=220000):
                times = (
                    pd.to_datetime(dt.Timestamp, utc=True)
                    .to_numpy(dtype="datetime64[ms]")
                    .astype("<i8")
                )
                px = dt[["Bid", "Ask"]].to_numpy()
                ints = np.rint(px * 1000).astype("<i4")
                if not (
                    (dt.Symbol == "XAUUSDm").all()
                    and times[0] >= prev
                    and (np.diff(times) >= 0).all()
                    and np.isfinite(px).all()
                    and (ints[:, 0] > 0).all()
                    and (ints[:, 1] >= ints[:, 0]).all()
                    and np.allclose(px, ints / 1000, atol=1e-9, rtol=0)
                ):
                    raise ValueError("Invalid archive row: " + k)
                first = int(times[0]) if first is None else first
                prev = int(times[-1])
                count += len(times)
                times.tofile(tm)
                ints.tofile(bk)
        if (
            not count
            or first < 1000 * low.timestamp()
            or prev >= 1000 * high.timestamp()
        ):
            raise ValueError("Wrong dates: " + k)
        rec = {"sha256": digest, "ticks": count, "first_ms": first, "last_ms": prev}
        ok.write_text(json.dumps(rec))
        records[k] = rec
        print(k, count, flush=True)
    check_cache(cache, records)
    return records


def arrays(cache, k):
    import numpy as np

    p = Path(cache) / k
    return np.memmap(p / "time.i8", dtype="<i8", mode="r"), np.memmap(
        p / "book.i4", dtype="<i4", mode="r"
    ).reshape(-1, 2)
