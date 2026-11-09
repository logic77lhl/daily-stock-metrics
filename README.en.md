[中文](README.md) | **English**

# Daily market metrics (A-shares / ETFs / HK Connect)

Every day it fetches **the top 100 A-shares by market cap**, **the top 100 on-exchange ETFs by size**, and **the top 100 HK Connect names by market cap**, then computes:

- **KDJ J value** on daily / weekly / monthly bars
- **MA20 / MA60 alignment** (bullish or bearish), distance from MA20 %, volume ratio
- Current **PE_TTM and PB_MRQ** plus **historical percentiles** (all history and a 5-year window)
- **Industry** (A-shares)

Results are written to `output\<date>\` and published as a GitHub Pages site.

> **The output comes in two kinds, with very different reliability.** Read
> [win rates and baselines](#win-rates-and-baselines) first:
> **descriptive** output (J values, moving-average state, volume ratio, valuation percentiles, industry)
> is field-verified and accurate;
> **predictive** output ("which strategy wins", any market-timing hint) was **disproved** by a
> 527-trading-day backtest, so the project no longer publishes any form of buy signal.

## Files

| File | Purpose |
|---|---|
| `runner.py` | **the only** daily pipeline; the three markets share one orchestration, differences live in `MarketSpec` |
| `run_daily.py` | thin A-share entry point (`= python runner.py a`); same for `run_etf_daily.py` / `run_hk_daily.py` |
| `reports.py` | report assembly and materialisation — **the only** place that decides what a daily report contains |
| `fetch_top100.py` | step 1: fetch the top 100 A-shares by market cap (East Money) |
| `fetch_metrics.py` | step 2: compute KDJ-J (Tencent K-line) and PE/PB percentiles (East Money valuations) |
| `trading_calendar.py` | **the only** trading-day authority: target-day derivation, holiday table, `plan` subcommand |
| `quality.py` | data-quality gate and DONE marker (single exit); checks row counts, success rate, fill rate, **data date** |
| `gate.py` | **the only** completion/red-green decision: target trading day plus hole detection over the lookback window |
| `http_util.py` | shared HTTP layer: session reuse, backoff retries, `HostPool` host retirement, wait budget |
| `signals.py` | single definition of signal classification (shared by `generate_report` / `market_insights`) |
| `backfill.py` | historical backfill: recompute missing trading days from historical K-lines and valuations |
| `tests/test_kline_agg.py` | aggregation fidelity: weekly/monthly J derived from daily bars must equal the API's values |
| `tests/test_calendar.py` | calendar structure checks plus target-day derivation (including a past-midnight guard) |
| `tests/test_workflows.py` | workflow YAML structure guard (a truncated `run: \|` block invalidates the whole file) |
| `tests/test_degradation.py` | end-to-end degradation: list API RST → observation pool fallback → gate still passes; reports rebuildable from CSV |
| `run.bat` | silent launcher for the scheduled task |

## Daily output

One directory per trading day: A-share, ETF, HK Connect, and value screens, plus a report page and the raw CSVs the site is built from. The Pages site is generated from these files — no backend, no database in production.

## Operations

- The pipeline is idempotent per day; re-running a completed day is a no-op.
- `quality.py` refuses to mark a day done if the data date doesn't match the target trading day.
- `gate.py` checks the lookback window for holes so a silent gap can't hide behind a green day.
- The scheduled trigger is a documented weakness: GitHub's `schedule` is delayed by minutes to hours in practice, so an external timer is the primary trigger and Actions is the fallback.

## Backtest

### Fill-price semantics (look-ahead bias fixed in 2026-10)

Signals are computed on day *t* and filled at day *t+1*'s price, never at the close that generated the signal. The earlier version filled at the same close and overstated every result.

### Known biases (disclosed item by item in the report footer)

Survivorship in the top-100 universe, missing delisted names, and a short window. They're stated rather than corrected, because correcting them would need data the free sources don't provide.

## Win rates and baselines

### Independent backtest result (527 trading days, 2024-07 to 2026-10)

Every strategy-level "win rate" was inside the noise band of its own baseline once the baseline was computed the same way. That is the finding.

### The deleted "today's buy reference"

An earlier version published a ranked buy list. The backtest showed the ranking had no edge after costs and baseline comparison, so the list was deleted rather than tuned. The report now states this explicitly.

## How long a run takes

Measured, not estimated: the A-share run is minutes, the three markets together roughly double that. The Pages build is seconds.

## Running manually

```bash
python runner.py a      # A-shares
python runner.py etf    # ETFs
python runner.py hk     # HK Connect
```

## Scheduled runs

```powershell
powershell -ExecutionPolicy Bypass -File setup_autostart.ps1
powershell -ExecutionPolicy Bypass -File remove_autostart.ps1
```

### Real `schedule` latency

GitHub Actions cron is best-effort: observed delays range from a few minutes to several hours. Hence the external timer as the primary trigger.

### Why the "Run failed" emails appear

Most are the scheduled workflow firing outside a trading day and exiting non-zero by design. The check order was changed so a non-trading day exits 0 with a clear message.

## Browsing online

<https://logic77lhl.github.io/daily-stock-metrics/> — static pages generated from the same CSVs; no JavaScript required to read a table.

## License

[MIT](LICENSE)
