"""Build a fully offline, static HTML report from the archived ledgers."""

import csv
import json
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from pulsefive.data import ROOT
from pulsefive.sessions import NAMES

D = ROOT / "research" / "study"
fmt = lambda x: f"${x:,.2f}"


def money(v):
    return ("−" if v < 0 else ("+" if v > 0 else "")) + fmt(abs(v))


def get(path):
    return json.loads(Path(path).read_bytes())


def cellnum(v):
    return (
        f'<span class="{"neg" if v < 0 else "pos" if v > 0 else ""}">{money(v)}</span>'
    )


def table(head, rows):
    return (
        '<div class="scroll"><table><thead><tr>'
        + "".join('<th scope="col">' + x + "</th>" for x in head)
        + "</tr></thead><tbody>"
        + "".join(
            "<tr>"
            + "".join(
                ('<th scope="row">' + x + "</th>") if i == 0 else "<td>" + x + "</td>"
                for i, x in enumerate(row)
            )
            + "</tr>"
            for row in rows
        )
        + "</tbody></table></div>"
    )


def chart(summ, cost):
    dates = summ["dates"]
    values = {}
    for mode in ["baseline", "challenger", "release"]:
        cash = 1000
        v = [cash]
        daily = dict.fromkeys(dates, 0.0)
        with (D / ("test-" + cost) / (mode + ".csv")).open() as f:
            for r in csv.DictReader(f):
                daily[
                    datetime.fromtimestamp(int(r["ms"]) / 1000, UTC).date().isoformat()
                ] += float(r["net"])
        for s in dates:
            cash += daily[s]
            v.append(cash)
        values[mode] = v
    xmin, xmax = 46, 456
    ymin, ymax = 19, 167
    low, high = 750, 1015

    def yy(v):
        return ymax - (v - low) / (high - low) * (ymax - ymin)

    def xx(i):
        return xmin + (xmax - xmin) * i / len(dates)

    grid = "".join(
        f'<line x1="{xmin}" y1="{yy(v):.1f}" x2="{xmax}" y2="{yy(v):.1f}" class="grid"/><text x="{xmin - 7}" y="{yy(v) + 4:.1f}" text-anchor="end">{v:,}</text>'
        for v in (750, 800, 850, 900, 950, 1000)
    )
    paths = ""
    for name, color in [
        ("release", "#6a7278"),
        ("baseline", "#b85a32"),
        ("challenger", "#126b70"),
    ]:
        vals = values[name]
        points = " ".join(f"{xx(i):.1f},{yy(v):.1f}" for i, v in enumerate(vals))
        dash = ' stroke-dasharray="4 5"' if name == "release" else ""
        paths += f'<polyline fill="none" stroke="{color}" stroke-width="2.7" points="{points}"{dash}/><circle cx="{xx(len(dates))}" cy="{yy(vals[-1]):.1f}" r="3" fill="{color}"/>'
    xlabels = "".join(
        f'<text x="{xx(i):.1f}" y="190" text-anchor="middle">{x}</text>'
        for i, x in [(0, "Start"), (5, "Nov 6"), (10, "Nov 13"), (12, "Nov 17")]
    )
    return f'<svg role="img" aria-label="Daily settled balances under {cost} costs. Starting from 1,000 USD; final values in table." viewBox="0 0 478 205">{grid}{paths}{xlabels}</svg>'


def create(out):
    sets = {c: get(D / ("test-" + c) / "summary.json") for c in ["base", "stress"]}
    model = get(D / "frozen.json")
    labels = get(D / "label-counts.json")
    ticks = get(ROOT / "research" / "tick-manifest.json")
    audit = get(D / "full-stream-audit.json")
    trials = get(D / "validation-grid.json")
    total = sum(x["ticks"] for x in ticks.values())
    ntrain = sum(labels["2026-" + k + "-base"]["trial_rows"] for k in ["06", "07"])
    actual = sets["base"]["policies"]["challenger"]
    severe = sets["stress"]["policies"]["challenger"]
    sums = []
    for cost, r in sets.items():
        for mode, name in [
            ("baseline", "Unfiltered baseline"),
            ("challenger", "Trained challenger"),
            ("release", "Approved-only release"),
        ]:
            a = r["policies"][mode]
            sums.append(
                [
                    name,
                    "Recorded" if cost == "base" else "Stress",
                    str(a["attempts"]),
                    str(a["filled"]),
                    str(a["full"]),
                    str(a["realized_target"]),
                    cellnum(a["net"]),
                    fmt(a["max_drawdown"]),
                ]
            )
    sessions = []
    qual = []
    for s in NAMES:
        label = {
            "Asia": "Tokyo / Asia",
            "London": "London only",
            "London-NY": "London + NY",
            "New York": "New York only",
            "Sydney": "Sydney / Pacific",
        }[s]
        x = model["sessions"][s]
        a = actual["sessions"][s]
        b = severe["sessions"][s]
        sessions.append(
            [
                label,
                f"{a['filled']} / {a['attempts']}",
                cellnum(a["net"]),
                f"{b['filled']} / {b['attempts']}",
                cellnum(b["net"]),
                '<b class="veto">ABSTAIN</b>',
            ]
        )
        v = x["validation"]
        con = x["confirmation"]
        qual.append(
            [
                label,
                escape(x["key"]),
                f"{v['base']['filled']} / {v['stress']['filled']}",
                f"{con['base']['filled']} / {con['stress']['filled']}",
                f"{money(con['base']['net'])} / {money(con['stress']['net'])}",
            ]
        )
    charts = "".join(
        "<figure><figcaption>"
        + ("Recorded spreads" if c == "base" else "Stressed execution")
        + "</figcaption>"
        + chart(r, c)
        + "</figure>"
        for c, r in sets.items()
    )
    old = []
    for k, date in [("engineering", "Oct 1–16"), ("evaluation", "Oct 19–30")]:
        a = get(ROOT / "research" / (k + "-base") / "summary.json")
        b = get(ROOT / "research" / (k + "-stress") / "summary.json")
        old.append(
            [
                date,
                str(a["attempts"]),
                str(a["filled_batches"]),
                cellnum(a["net"]),
                str(b["attempts"]),
                str(b["filled_batches"]),
                cellnum(b["net"]),
            ]
        )
    css = """*{box-sizing:border-box}html{color-scheme:light}body{margin:0;background:#f5f4ef;color:#192f34;font:16px/1.55 system-ui,-apple-system,Segoe UI,sans-serif}main{max-width:1100px;margin:0 auto;padding:36px 36px 60px}header{background:#173c41;color:#f7f5e9;padding:32px 38px;border-radius:16px 16px 5px 5px}p{max-width:82ch;margin:8px 0 14px}.strap{font-size:12px;font-weight:730;letter-spacing:.16em;text-transform:uppercase;color:#b3ddcf}.meta{font-size:13px;color:#d2e5df}.rule{border-top:1px solid #457073;margin:22px 0 16px}.headline{font:600 clamp(34px,5.2vw,60px)/1.04 Georgia,serif;letter-spacing:-.035em;margin:22px 0 14px}.stand{font-size:18px;color:#dfebe3;max-width:71ch}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:16px 0 24px}.card,section{background:#fffefa;border:1px solid #d1d9d1;border-radius:8px}.card{padding:16px 19px}.card b{font-size:28px;color:#164e52;display:block;line-height:1.25}.card small{color:#43575b;font-size:13px}section{padding:24px 28px;margin:16px 0}h2{font:600 30px/1.13 Georgia,serif;letter-spacing:-.025em;margin:3px 0 12px}h3{font-size:17px;margin:22px 0 4px}.kicker{font-size:11px;font-weight:750;color:#42706e;letter-spacing:.15em;text-transform:uppercase;display:block;margin-bottom:6px}.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px;margin:12px 0 5px;font-variant-numeric:tabular-nums}th,td{text-align:right;white-space:nowrap;padding:9px 10px;border-bottom:1px solid #dce2dc}th:first-child,td:first-child{text-align:left}thead th{background:#e9efea;color:#385654;font-size:12px;font-weight:670}tbody th{font-weight:610}tbody tr:nth-child(even){background:#f7f8f2}.neg{color:#923b27}.pos{color:#146158}.veto{color:#5b543a;background:#f1ecd8;font-size:11px;letter-spacing:.05em;padding:4px 8px;border-radius:4px}.small,.foot{color:#526164;font-size:13px}.plots{display:grid;grid-template-columns:1fr 1fr;gap:16px}figure{margin:8px 0;padding:10px;background:#f7f8f3;border-radius:6px}figcaption{font-size:14px;font-weight:bold;margin-left:8px}svg{width:100%;display:block}svg text{font:11px system-ui,sans-serif;fill:#405453}.grid{stroke:#d6dfd9;stroke-width:1}.key{font-size:13px;color:#405453;display:flex;gap:18px;flex-wrap:wrap}.key i{width:17px;height:3px;display:inline-block;vertical-align:middle;margin-right:5px}.notes{padding:12px 16px;border-left:3px solid #527d73;background:#eef3eb}.split{display:grid;grid-template-columns:1fr 1fr;gap:18px}.split p{font-size:14px}.chips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}.chips span{font-size:13px;background:#e8efea;border-radius:4px;padding:5px 9px}code{font:12px/1.4 ui-monospace,Consolas,monospace;overflow-wrap:anywhere}footer{padding:4px 5px;color:#536567;font-size:12px}a{color:#155c64}details summary{cursor:pointer;font-weight:650}li{margin:5px 0}ol,ul{padding-left:22px}@media(max-width:700px){main{padding:12px}header,section{padding:18px}.card{padding:10px}.card b{font-size:21px}.metrics{gap:5px}.plots,.split{grid-template-columns:1fr}.headline{margin-top:14px}th,td{padding:8px}.card small{font-size:11px}}@media(max-width:520px){.metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}.metrics .card:last-child{grid-column:1/-1;display:flex;align-items:center;justify-content:space-between}.metrics .card:last-child b{margin-left:8px}}@media print{body{background:white}main{max-width:none;padding:0}section,figure{break-inside:avoid}header{-webkit-print-color-adjust:exact;print-color-adjust:exact}details{display:block}.scroll{overflow:visible}table{font-size:11px}th,td{padding:4px}}"""
    title = "PulseFive | Session validation report"
    part = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title><style>{css}</style></head><body><main><header><div class="strap">PulseFive &nbsp; / &nbsp; Research note 02</div><div class="headline">No session earned approval.</div><p class="stand">Training made the gold scalper more selective, not profitable. Its best research configuration still lost money on new dates under both execution models.</p><div class="rule"></div><div class="meta">XAUUSDm &nbsp;·&nbsp; 5 × 0.01 lot &nbsp;·&nbsp; $2 basket target &nbsp;·&nbsp; Historical bid/ask replay &nbsp;·&nbsp; Evidence through 17 Nov 2026</div></header>
<div class="metrics"><div class="card"><small>Distinct quotes in the new study</small><b>{total:,}</b></div><div class="card"><small>November quotes held out</small><b>{sets["base"]["raw_ticks"]:,}</b></div><div class="card"><small>Session gates approved</small><b>0 of {len(NAMES)}</b></div></div>
<section><span class="kicker">01 &nbsp; / &nbsp; Untouched November comparison</span><h2>What happened to $1,000?</h2><p>November 2–17, 2026 · 12 trading days. Each row is an independent continuous account; no reset between days. “Filled” means at least one ticket, “Full” all five. The $2 column counts actual simulated realizations, not exit requests.</p>{table(["Policy", "Costs", "Attempts", "Filled", "Full", "Net ≥ $2", "Net USD", "Max DD¹"], sums)}<p class="small">¹ Maximum peak-to-trough modeled equity drawdown, including open positions. Fees and exit slippage are included in realized net. Individual session amounts may not sum after cent rounding.</p><h3>Daily closing cash, USD</h3><div class="key"><span><i style="background:#b85a32"></i>Unfiltered baseline</span><span><i style="background:#126b70"></i>Trained challenger</span><span><i style="background:#6a7278"></i>Approved-only / no trades</span></div><div class="plots">{charts}</div><p class="small">Daily settled balances, not intraday equity. The challenger and release nearly overlap because few challenger tickets filled.</p></section>
<section><span class="kicker">02 &nbsp; / &nbsp; Challenger by session</span><h2>A small gain is not a session edge.</h2>{table(["Session", "Filled / attempts · base", "Base net", "Filled / attempts · stress", "Stress net", "Release"], sessions)}<p class="notes">Asia’s recorded-spread gain came from just two filled baskets; London’s came from one. Neither persisted under stress. Sydney had no recorded-spread attempts and no stress fills. Do not promote these windows on November results.</p></section>
<section><span class="kicker">03 &nbsp; / &nbsp; How the model was selected</span><h2>Fit early. Decide before the test.</h2><div class="chips"><span>Jun–Jul · fit</span><span>Aug · select</span><span>Sep · confirm / veto</span><span>Oct · already viewed, excluded</span><span>Nov 2–17 · final test</span></div><p>{ntrain:,} admitted June–July trial labels across two entry depths; the same edge can yield two labels. Ten session/depth ridge fits, eight entry-time features, {sum(map(len, trials.values()))} August candidates. Unfilled attempts contribute zero. Features never see post-entry quotes.</p>{table(["Session", "Best August candidate", "Aug fills · B / S", "Sep fills · B / S", "Sep net · B / S"], qual)}<p class="small">B / S = recorded / stressed execution. Candidate names give limit depth and fitted score percentile. “Best” means highest weaker-scenario mean daily net, not deployable. In both cost cases August needed ≥20 filled baskets on ≥8 days, positive net, profit factor ≥1.12 and a positive conservative mean-daily-net bound. September needed ≥10 fills on ≥5 days, positive net and profit factor ≥1. No session passed both gates.</p></section>
<section><span class="kicker">04 &nbsp; / &nbsp; Execution and limits</span><h2>Quote motion is observable. Queue position is not.</h2><div class="split"><div><h3>Recorded-spread model</h3><p>150 ms action delay; 1 tick penetration; $3.50/lot per side; $0.02/oz exit slip. A buy marks at bid, a sell at ask. All eligible working tickets can fill on the same quote.</p></div><div><h3>Stress model</h3><p>Both sides widened outward by 10% of spread; 450 ms delay; 50 ticks penetration; $5/lot per side; $0.06/oz exit slip. Not a worst-case bound.</p></div></div><p>Five limits are intended, not guaranteed to be sent or filled. Quote-change activity is not traded volume. Ordering, reversal, spread and latency can turn a $2 exit intent into less or even a loss. No exchange queue, real fills or native broker orders were observed.</p><p class="small">Sessions use local DST clocks, exclude rollover, weekends and warm-up; this is not a 24/7 or multi-instrument result. One basket, up to 20 attempts/day and four per session/day. Initial modeled risk ≤$5, daily gate ≤$20 on $1,000; stops can gap.</p></section>
<section><span class="kicker">05 &nbsp; / &nbsp; Audit trail</span><h2>The October losses remain on record.</h2>{table(["Earlier 12–16 UTC run", "Attempts · B", "Filled · B", "Net · B", "Attempts · S", "Filled · S", "Net · S"], old)}<p class="small">The initial October experiment used a narrower time window and different admission caps. Do not compare it directly to the new all-session benchmark. It was not reused as a blind test.</p><h3>Reconciliation</h3><p>A second scalar full-stream pass checked every one of the {sum(sum(v["matching_ledgers"].values()) for v in audit.values()):,} November scenario/basket records against the accelerated path, plus cash, drawdowns and server-event counts. Agreement tests the replay software, not whether a broker would have filled these orders.</p><p class="small">Protocol <code>2b755d8</code> · frozen rules <code>38c5bc7</code> · test record <code>03dfd3a</code>. Archive hashes, all 30 validation scores, ledgers and reproduction commands are included in the companion repository.</p></section>
<section><span class="kicker">06 &nbsp; / &nbsp; Next observation</span><h2>Keep the default flat.</h2><p>On a local Windows MT5 demo feed, the optional forward-paper reader can record the rejected challenger on genuinely new quotes. All orders remain simulated. Keep the rules fixed, log nonfills and outages, and do not call zero trades a profit.</p><p class="small">Start in the repository with <code>python -m pulsefive status</code>. Windows installation, quote capture and paper-only commands are in <code>README.md</code>. No live connection was made for this study.</p></section><footer>PulseFive &nbsp; / &nbsp; Independent gold research repository &nbsp; · &nbsp; Read-only report &nbsp; · &nbsp; USD throughout</footer></main></body></html>"""
    Path(out).write_text(part, encoding="utf-8")


if __name__ == "__main__":
    create(ROOT / "Session-Report.html")
