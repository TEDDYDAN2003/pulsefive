# Reproduce the session study

Use the repository root and an activated research environment (or substitute the full venv Python path). The only network step is the optional public-archive download. Raw quotes are not redistributed; 16 pinned ZIPs are ~299 MB total, expanded time/book arrays ~507 MB. Allow ~3 GB free including dependencies/temp files. The study used CPython 3.13.18 on Linux, NumPy 2.3.5, pandas 2.2.3, Numba 0.66.0 and llvmlite 0.48.0. The tested inputs/results/hashes are included; numerical libraries and OS can change the last digits of refitted floating-point weights. The frozen model is the release authority, not a refit.

```powershell
# Run from the source root. PowerShell: no environment activation required.
$py = '.\.venv\Scripts\python.exe'
& $py -m pip install -e ".[research]"
& $py -m unittest discover -v
# Quick refit of the 10 ridge models + all 30 August grid scores from packaged labels:
& $py -m research.run fit --cache .runtime/cache --work research/labels --output .runtime/quick
```

**Full reproduction** (writes new outputs, not the frozen files):

```powershell
$env:OPENBLAS_NUM_THREADS = '1'
$env:OMP_NUM_THREADS = '1'
& $py -m pulsefive prepare --archives .runtime/archives --cache .runtime/cache --download
& $py -m research.run labels  --cache .runtime/cache --work .runtime/labels --output .runtime/study --jobs 2
& $py -m research.run fit     --cache .runtime/cache --work .runtime/labels --output .runtime/study
& $py -m research.run confirm --cache .runtime/cache --work .runtime/labels --output .runtime/study --jobs 2
Get-Content .runtime/study/freeze.sha256
& $py -m research.run test    --cache .runtime/cache --work .runtime/labels --output .runtime/study --jobs 2
& $py -m research.independent --cache .runtime/cache --study .runtime/study --output .runtime/full-stream-audit.json
```

The CLI verifies ZIP SHA-256, reads the CSV to the end (ZIP CRC), validates symbols/dates/monotone UTC ms/price grid, and verifies the prepared byte arrays against `cache-hashes.json`. Existing matching input caches can be reused. If your provider no longer serves a pinned archive, don't substitute silently. An archive has to be re-reviewed to change the study. Use `--jobs 1` on a low-memory system (the optional full-stream verifier uses two workers). On Windows keep `tzdata` installed for IANA timezone rules.

| Split | What happens |
|---|---|
| June–July | Fit standardized ridge on independent admitted attempts, two depths per session; entry features only; target = realized USD (zero if unfilled). |
| August | Compare 2 depths × 3 fitted-score quantiles per session on separate base/stress labels. Include zero-trade weekdays. No future quote is an input. |
| September | Replay best candidates on a shared account in each scenario. A failure can veto, never promote; freeze model and session flags. |
| October | Excluded from new selection and holdout because October had already been inspected. Original losing results are retained. |
| Nov 2–17 | No more fitting. Replay original shallow any-session signal, best learned but unapproved challenger, and release. Each policy/scenario has one independent continuous $1,000 account. |

`research/sources.json` has the exact 4 monthly + 12 daily paths/SHA-256s. There are no November 18 or later rows. `tick-manifest.json` has counts/endpoints; `label-counts.json` includes even edges vetoed by the preflight (trial rows count admitted attempts across both depths). November final includes 3,847,181 raw updates and 2,747,332 occupied 50 ms observation bins per cost case. Flat-state feature scans omit no eligible edges. The second scalar full-stream path runs `Tape.update/Engine.observe` from scratch across all holdout dates and compares each settlement field, server counter, terminal cash and mark-to-market drawdown. Same underlying decision and broker code, independent replay path; not an independent broker validation.

**Audit trail:** initial session protocol `2b755d8`; simulated no-quote exit correction `181b33e`; trained session freeze `38c5bc7`; recorded November output `03dfd3a`. SHA-256 of the frozen JSON: `14ee65478a3987cec09e12680f4d09beb2fac1a82fcb2791f2ba0e91772c53f9`. Final testing reads the freeze file and verifies its companion digest. Never choose a different rule using November and call November blind again.

## Original October baseline (optional, separate)

Download the [pinned October archive](https://ticks.ex2archive.com/ticks/XAUUSDm/2026/10/Exness_XAUUSDm_2026_10.zip) to `.runtime/archives/oct.zip` (SHA-256 in `pulsefive/replay.py`). Example:

```powershell
& $py -m pulsefive legacy --zip .runtime/archives/oct.zip --cache .runtime/legacy-cache --start 2026-10-19 --end 2026-10-30 --output .runtime/oct-base
```

Repeat with `--stress` and a different output directory; use Oct 1–16 for the engineering segment. The legacy admission window is 12:00–16:00 **UTC**, not all-day trading. It is a different experiment (see `research/PROTOCOL.md`).
