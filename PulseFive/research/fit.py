"""Fit on Jun--Jul only, select in Aug. No September/November input to this stage."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from pulsefive.model import FEATURES, STYLES
from pulsefive.policy import COOLDOWN
from pulsefive.sessions import NAMES


def ridge(g):
    x = g[list(FEATURES)].to_numpy(float)
    y = g.net.to_numpy(float)
    m = x.mean(axis=0)
    s = x.std(axis=0)
    s[s < 1.0e-9] = 1.0
    xx = (x - m) / s
    b = float(y.mean())
    w = np.linalg.solve(xx.T @ xx + 20.0 * np.eye(x.shape[1]), xx.T @ (y - b))
    return dict(
        mean=list(m), scale=list(s), coef=list(w), intercept=b, n=len(y)
    ), b + xx @ w


def estimates(g, r):
    return (
        r["intercept"]
        + ((g[list(FEATURES)].to_numpy() - r["mean"]) / r["scale"]) @ r["coef"]
    )


def calendar_stats(g, all_dates):
    daily = dict.fromkeys(all_dates, 0.0)
    filled_days = set()
    net = win = loss = 0.0
    fills = n = 0
    after = -1
    counts = {}
    for row in g.sort_values("ms").itertuples(index=False):
        if row.ms < after or counts.get(row.date, 0) >= 4:
            continue
        counts[row.date] = 1 + counts.get(row.date, 0)
        after = int(row.closed_ms) + COOLDOWN
        fills += row.filled > 0
        n += 1
        if row.filled:
            filled_days.add(row.date)
        daily[row.date] += row.net
        net += row.net
        win += max(0.0, row.net)
        loss -= min(0.0, row.net)
    a = np.array(list(daily.values()))
    pf = win / loss if loss else None
    return dict(
        attempts=n,
        filled=int(fills),
        fill_days=len(filled_days),
        net=round(net, 6),
        pf=round(pf, 6) if pf is not None else None,
        mean=float(a.mean()),
        lcb=float(a.mean() - 2.65 * a.std(ddof=1) / np.sqrt(len(a))),
        gross_gain=win,
        gross_loss=loss,
    )


def passed(a):
    return (
        a["filled"] >= 20
        and a["fill_days"] >= 8
        and a["net"] > 0
        and (a["gross_gain"] >= 1.12 * a["gross_loss"])
        and a["lcb"] > 0
    )


def fit(loc, out):
    loc = Path(loc)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)

    def get(k, c):
        return pd.read_csv(loc / (k + "-" + c + ".csv.gz"))

    tr = pd.concat([get("2026-" + m, "base") for m in ("06", "07")], ignore_index=True)
    vs = {c: get("2026-08", c) for c in ("base", "stress")}
    ds = json.loads((loc / "2026-08-base.json").read_text())["days"]
    trials = {}
    chosen = {}
    for session in NAMES:
        results = []
        for style in STYLES:
            group = tr[(tr.session == session) & (tr["style"] == style)]
            if len(group) < 20:
                continue
            mod, y = ridge(group)
            pred = {
                c: estimates(v[(v.session == session) & (v["style"] == style)], mod)
                for c, v in vs.items()
            }
            for quant in (0.5, 0.8, 0.95):
                cut = float(np.quantile(y, quant))
                key = f"{style}-p{int(quant * 100):02d}"
                stats = {}
                for c, v in vs.items():
                    v = v[(v.session == session) & (v["style"] == style)]
                    stats[c] = calendar_stats(v[pred[c] >= cut], ds)
                ok = all(passed(x) for x in stats.values())
                score = min(x["mean"] for x in stats.values())
                results.append(
                    dict(
                        key=key,
                        fit=mod,
                        style=style,
                        threshold=cut,
                        quantile=quant,
                        validation=stats,
                        validation_pass=ok,
                        score=score,
                    )
                )
        trials[session] = [{k: v for k, v in x.items() if k != "fit"} for x in results]
        if not results:
            chosen[session] = dict(
                fit=None,
                approved=False,
                validation_pass=False,
                reason="insufficient training",
            )
            continue
        best = sorted(results, key=lambda a: (-a["score"], a["key"]))[0]
        chosen[session] = dict(
            best,
            approved=best["validation_pass"],
            reason="provisional; requires September confirmation"
            if best["validation_pass"]
            else "failed August evidence and cost hurdles",
        )
    result = dict(
        schema="pulsefive-session-ridge-1",
        features=list(FEATURES),
        training="June–July 2026; base execution, zero for admitted nonfills",
        validation="August 2026",
        sessions=chosen,
    )
    (out / "provisional.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n"
    )
    (out / "validation-grid.json").write_text(
        json.dumps(trials, indent=2, allow_nan=False) + "\n"
    )
    return result


def confirm(prov, base, stress, out):
    import copy

    result = copy.deepcopy(prov)
    for s, m in result["sessions"].items():
        ss = {}
        passed_confirm = True
        for nm, rep in [("base", base), ("stress", stress)]:
            p = rep["policies"]["challenger"]
            a = p["sessions"][s]
            a = {**a, "fill_days": p["fill_days"][s]}
            ss[nm] = a
            passed_confirm &= (
                a["net"] > 0
                and (a["pf"] is None or a["pf"] >= 1.0)
                and a["filled"] >= 10
                and a["fill_days"] >= 5
            )
        m["confirmation"] = ss
        m["confirmation_pass"] = bool(passed_confirm)
        m["approved"] = bool(m["validation_pass"] and passed_confirm)
        m["reason"] = (
            "passed both chronological gates"
            if m["approved"]
            else "failed "
            + (
                "August and September"
                if not m["validation_pass"] and not passed_confirm
                else ("August" if not m["validation_pass"] else "September")
            )
            + "; paper abstains"
        )
    result["confirmation"] = (
        "September 2026; challenger on a continuous $1,000 account, veto only"
    )
    Path(out).write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result
