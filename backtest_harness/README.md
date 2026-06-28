# Backtest Harness

A standalone script that drives the **real** trading engine
(`core/algo_engine.py` + `core/sr_engine.py`, completely unmodified — the
same code `main.py` uses for paper and live trading) over a 1-minute OHLC
CSV. Use this to sanity-check your S/R levels, tolerance, and signal logic
against historical data before risking anything in paper or live mode.

## Quick start

1. Put your 1-minute CSV in this folder (a sample `nifty50_1min.csv` is
   included). Required columns, in this exact order:
   ```
   datetime,open,high,low,close,volume
   2026-06-01 09:15:00,23654.5,23733.7,23625.3,23631.45,7840691.0
   ```
   `datetime` format must be `YYYY-MM-DD HH:MM:SS`. The `volume` column is
   read but not used by the strategy.

2. Open `run_bt.py` and edit the **USER SETTINGS** block near the top:
   - `CSV_PATH` — defaults to `nifty50_1min.csv` in this folder
   - `TOLERANCE` — global tolerance in index points
   - `LEVELS` — your list of manually-defined S/R levels
   - `ACTIVE_INDEX` — must match a key in `config.INDEX_CONFIG`
     (`NIFTY50`, `BANKNIFTY`, `SENSEX`, `CRUDE` — see `config.py`)
   - `OPTION_EXPIRY` — informational only; the strategy itself never uses
     time-to-expiry in its signal logic (see caveat below)
   - `FIRST_TRADE_SIDE` / `FIRST_TRADE_AT_FIRST_CANDLE` / `FIRST_TRADE_SPOT`
     — your manual first trade. **Required** — the engine does nothing
     while flat, by design (per the strategy spec: the very first trade of
     any run must be placed manually).

3. Run it:
   ```bash
   python3 run_bt.py
   ```

4. Read the console output (trade-by-trade log, state transitions,
   day-by-day summary) and/or open `bt_output.json` for the full
   structured log if you want to load it into a spreadsheet or another
   script.

## What this DOES tell you

- Whether entries, exits, stop-losses, the candle filters (Inverted Green
  Hammer / Red Hanging Man), Case-1/Case-2 reversals, and the near-S/R
  "skip to next level" rule all fire at the **correct candle and price**
  according to your exact strategy rules.
- Whether positions correctly carry across day boundaries (delivery /
  overnight carry, per the spec) without getting stuck or lost.
- The exact entry/exit timestamps and spot prices for every trade, so you
  can manually cross-check any trade you're unsure about against a chart.

## What this DOES NOT tell you

**The reported P&L is in INDEX POINTS, not real Rupees.** There is no real
option-pricing model in this harness (no strike distance from spot, no
time decay, no implied volatility). A "+917 points" result tells you the
signal logic was net directionally correct over the test period — it does
NOT tell you what you would have actually made or lost trading real
options. Treat the trade list and timing as the reliable part of this
report, and the point-total as a rough sanity check only, not a profit
projection.

If you want real Rupee P&L from backtesting, that requires a proper option
pricing model (e.g. Black-Scholes with a realistic implied-volatility
assumption per strike/expiry) — a separate piece of work from the signal
engine itself.

## Notes on backtest-specific engine behavior

- In `BACKTEST` mode specifically, the daily P&L force-exit limit is
  disabled entirely (by design — see `CHANGES_FROM_ORIGINAL.md` in the
  parent folder). A backtest position runs through its full natural
  signal-driven lifecycle, even past what would be a live daily loss
  limit, since there's no way to "manually re-enter" mid-backtest the way
  a live trader could the next morning.
- `daily_pnl` still resets to 0 at each new trading day's 09:15 boundary
  in backtest mode, purely for bookkeeping clarity in the day-by-day
  report — it has no effect on trading decisions in this mode.
