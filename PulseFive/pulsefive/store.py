"""Per-account process lock and crash-safe intent log, outside the repository."""

import hashlib
import json
import os
import time
from pathlib import Path


class Store:
    def __init__(self, root, identity):
        key = hashlib.sha256(identity.encode()).hexdigest()[:20]
        self.dir = Path(root) / key
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "state.json"
        self.file = (self.dir / "writer.lock").open("a+b")
        self.file.seek(0, 2)
        if not self.file.tell():
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            self.file.close()
            raise RuntimeError("Another writer is using this account") from e
        try:
            self.s = (
                json.loads(self.path.read_text())
                if self.path.exists()
                else {"identity": identity, "schema": 1}
            )
            if self.s.get("identity") != identity or self.s.get("schema") != 1:
                raise RuntimeError("State identity/schema mismatch; manual review")
            self.save()
        except BaseException:
            self.file.close()
            raise

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        data = json.dumps(self.s, allow_nan=False)
        with tmp.open("w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def log(self, event, **kw):
        obj = dict(event=event, logged_ms=time.time_ns() // 1000000, **kw)
        with (self.dir / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, allow_nan=False) + "\n")
        if event in (
            "settled",
            "manual_review",
            "exit_intent",
            "reject",
            "admission_veto",
        ):
            print(json.dumps(obj), flush=True)

    def close(self):
        self.file.close()
