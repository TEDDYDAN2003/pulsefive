# PulseFive · Session Lab

A standalone gold limit-basket scalper **for research and paper execution**. It is not part of the Hull project. Open **`Session-Report.html`** for the results. Works offline without Excel, a charting package or an MT5 login.

**Decision: abstain in all five sessions.** On November 2–17, 2026, the frozen research challenger lost $4.07 under recorded spreads and $11.89 under stress on separate simulated $1,000 accounts. Only 8 / 72 and 7 / 80 attempts filled at least one ticket. Neither run realized a $2 basket. The release made no attempts: $0 is not evidence of an edge. Do not fund this strategy on these results.

## Start on a Windows PC

1. Extract the **whole** download to a new folder. Use the included `PulseFive` source directory **or**, for a full independent Git repository, open PowerShell next to `PulseFive.bundle` and run `git clone -b main .\PulseFive.bundle PulseFive-local`. No remote or credentials are needed. Open PowerShell in the extracted source or cloned folder (it contains `pyproject.toml`).
2. Install 64-bit CPython 3.11 if needed. Create a virtual environment. No PowerShell script execution-policy change is needed:
   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -e .
   .\.venv\Scripts\python.exe -m pulsefive status
   .\.venv\Scripts\python.exe -m pulsefive example
   ```
   `status` reads the frozen gates locally. `example` demonstrates a five-fill target, a one-ticket partial basket, a nonfill, fills during cancellation, a reversal and a target intent that realizes a loss. **Injected signal, invented prices; not performance evidence.** View an event trace with `example --case late-fill --events`.
3. For the full numerical checks: ` .\.venv\Scripts\python.exe -m pip install -e ".[research]" ` then ` .\.venv\Scripts\python.exe -m unittest discover -v `. Without the research extra the numerical tests are skipped. For Linux/macOS replay, use `python3.11 -m venv .venv` and `./.venv/bin/python` instead. Dependencies may need internet on first install; status/example do not need market data.

## Genuine forward paper, on your local demo feed

This was **not** run against a live terminal in the research environment. Historical replay is complete; prospective observation is a separate next step. The installed release contains **no native `order_send` call, no live mode and no account login command**. It simulates pending limits, stops, cancels and exits in memory. No funds or broker tickets are created, even in challenger mode.

* Open a connected Windows MT5 **demo** terminal yourself. Show exactly `XAUUSDm` in Market Watch. The feed must be XAU/USD, $0.001 tick size, 100 oz/lot. Do not enable Algo Trading for this tool. Use a synchronized PC clock; market data access is needed, trading permissions are not.
* Install the quote reader and run the default selected policy:
  ```powershell
  .\.venv\Scripts\python.exe -m pip install -e ".[mt5]"
  .\.venv\Scripts\python.exe -m pulsefive forward --minutes 120 --record
  ```
  All release gates currently abstain. To collect **simulated** receipts for the rejected research challenger on genuinely new quotes instead:
  ```powershell
  .\.venv\Scripts\python.exe -m pulsefive forward --profile challenger --minutes 240 --record
  ```
  `--terminal-path "C:\Program Files\MetaTrader 5\terminal64.exe"` is optional. There is no writer switch. The process is finite; nothing installs as a service. Opening a run during a quiet period, a warm-up, or a zero-edge session can legitimately yield no fills. Allow at least 21 uninterrupted observed minutes after start/gaps before expecting an edge. Entries are blocked for the first 25 UTC minutes of each day.
* Each invocation is a new $1,000 *simulation*. Its directory is printed under `%LOCALAPPDATA%\PulseFive\forward`; optionally override `--logs`. `events.jsonl` records intents/settlements when any exist; `state.json` records progress; `summary.json` carries cash, realized settled net, unresolved inventory, the model hash, per-session coverage / fills / net, and end status. `--record` writes source times, bid, ask and local observation times to `ticks.csv` (can grow large). No account password or login is written to the log. Keep losing, inactive and censored runs.
* Ctrl+C stops new entries and tries to drain paper inventory. A slow/disconnected feed, clock interruption or unresolved drain is **censored**, not a successful zero-return run. An interrupted process can leave `in_progress` without a summary; do not splice it into a fresh run or claim its position closed. For forward analysis check `late_quotes_while_exposed` and observation delays; local polling is not a guaranteed 50 ms clock. This mode was exercised with mock terminal data, **not certified on Windows or a broker terminal**.

## Strategy and risk in one minute

* One basket at a time. Use the current, unfinished M1 midpoint candle against the previous 20 complete observed minute ranges. Require directional body, a 2.5 s move/efficiency and elevated quote-change activity. That activity is **not traded volume**. Treat gaps and partial warm-up minutes as invalid. In the session study, only the first qualifying edge per UTC minute is considered.
* Prepare five distinct pullback limits of 0.01 lot each (1 oz per ticket here). Stage sequentially on 50 ms polls, no more than 650 ms; abandon unplaced legs if the signal/inventory changes. Do not chase with a market entry. Withdraw working legs on reversal, poor reach, widening spread, quiet tape or 6 s timeout. An order can fill during the cancel delay. True queue position and partial lots are unknown; research fills each eligible individual ticket in full, **not** necessarily the whole basket.
* Check aggregate liquidation value, including booked costs and an estimated exit allowance. $2 is a **basket exit intent**, not $2 per ticket, not a TP promise. It can realize less or a loss. Other exits include directional reversal, giving back gains, stalled trade, 25 s holding limit and individual stops. Mark longs at bid, shorts at ask. Confirm settled inventory before cooldown and require a fresh edge, not automatic rapid reentry.
* Baseline hypotheses: spread ≤$0.30/oz, nominal basket stop budget ≤min($5, 0.5% equity), ≥20% free-equity reserve, daily loss gate min($20, 2% day-start equity), 20 attempts/day and 4 per session/day for session profiles. Gaps, latency and slips can exceed budgets. $1,000, 1:100 leverage and 5 × 0.01 lot are modeling choices, **not sizing advice**. No tests on another instrument support trading it.

## Where to look

| File | Contents |
|---|---|
| `Session-Report.html` | Offline results with daily realized balances and session-by-session counts |
| `research/SESSION_PROTOCOL.md` | Frozen split, fit, cost models and rejection gates |
| `docs/REPRODUCE.md` | Training, evaluation, second scalar pass and old October reproduction commands |
| `docs/RESEARCH-NOTES.md` | Data sources, what fills mean, exclusions and prospective evidence plan |
| `pulsefive/model_v1.json` | Frozen coefficients, cutoffs, August/September evidence and release flags |
| `research/study/` | All 30 August candidate scores, frozen artifact, September/November CSVs and summaries |
| `research/labels/` | Derived feature/target rows (compressed); quick refit without re-downloading raw ticks |
| `research/{engineering,evaluation}-{base,stress}/` | Original losing October ledgers, unchanged |
| `pulsefive/{policy,engine,paper,forward}.py` | Causal signal, five-ticket state machine, simulated execution and local quote reader |
| `tests/` | Timing, nonfills, reversals, partial/cancel races, ordinary/censored state and receipt checks |

The Git history includes the research protocol, the model freeze **before** the November test, and the eventual report. Nothing was published, no old repository was changed, and this package is not a connection to a trading account.
