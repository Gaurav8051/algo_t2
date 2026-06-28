# Changes from algo_v5_fixed.zip (original upload)

Reviewed against the user's full strategy spec and tested with the spec's
own worked numerical examples (Case-1, Case-2, near-S/R zone rule, both
candle-filter types), plus several real multi-day backtests using the
user's own S/R levels and historical data, with the user directly
clarifying ambiguous rules along the way. Seven real bugs were found and
fixed below; #4, #5, and #7 were discovered via real backtests and the
user's own analysis of the results, not just code review — #7 in
particular corrected a wrong assumption made while fixing #5. No correct
strategy/S-R/candle-filter logic was changed.

## 1. Live-mode crash on first tick (main.py)

**Before:**
```python
start_feed(ctx, engine, spot_setter=lambda p: setattr(last_tick, 0, p))
```
`setattr()` sets an *attribute* by name — it cannot be used to assign to a
list index. This raised `TypeError` the first time a live tick arrived,
breaking the live "last tick" display (paper mode was unaffected — it used
a different code path).

**After:**
```python
def _set_last_tick(p):
    last_tick[0] = p
start_feed(ctx, engine, spot_setter=_set_last_tick)
```

## 2. Telegram alerts mislabeled "PAPER" even during LIVE trading

**Before:** `telegram_alert.alert_buy()` / `alert_sell()` hardcoded the
text `"PAPER BUY"` / `"PAPER SELL"` regardless of which mode actually
placed the trade. A real live trade would show up in Telegram labeled as
paper — confusing, though not a capital-risk bug.

**After:**
- `AlgoState` gained a `mode: str = "PAPER"` field, set explicitly to
  `"PAPER"`, `"LIVE"`, or `"BACKTEST"` wherever each run mode constructs
  its `AlgoState` in `main.py`.
- `alert_buy()` / `alert_sell()` now take a `mode` parameter and use it in
  the message text. Default is `"LIVE"` (not `"PAPER"`) — if a future
  caller ever forgets to pass `mode`, you'll see an over-cautious "LIVE"
  label rather than a falsely reassuring "PAPER" one.
- `algo_engine.py`'s four call sites (`_enter_call`, `_enter_put`,
  `_exit_call`, `_exit_put`) now pass `mode=s.mode` through.

## 3. Unverified `dhan.MARGIN` constant — now checked at startup, not mid-trade

The code uses `dhan.MARGIN` as the product-type for DELIVERY (carry
forward) F&O orders. This is confirmed correct against Dhan's API (other
official Dhan SDKs expose the same underlying `"MARGIN"` product-type
string for F&O carry-forward), but nothing in the original code verified
the *installed* SDK actually exposes this attribute before relying on it —
if it were ever missing, you'd discover it via a buried `AttributeError`
in the middle of placing a real order.

**Added:**
- `dhan_api.verify_sdk_constants(dhan)` — checks `BUY`, `SELL`, `MARKET`,
  `INTRA`, `MARGIN` all exist on the SDK object. Called once at LIVE
  startup, right after the Dhan connection succeeds and before any order
  can be placed. If anything is missing, the program logs a clear,
  actionable error and exits — it will not proceed to place an order with
  a guessed/missing constant.
- `_product()` now also wraps the `AttributeError` case with a clearer
  message (defense in depth, in case verification is ever skipped).

## 4. FORCE_EXIT permanently stuck after a daily P&L limit breach (core/algo_engine.py)

**Discovered via:** a 15-day backtest using the user's real S/R levels and
1-minute Nifty data (June 1-19 2026), which surfaced this on day 1 and then
silently produced ZERO further trades for the remaining 14 days.

**Root cause:**
- `daily_pnl` was a single running total with no concept of "trading day"
  anywhere in the codebase — it never reset.
- Once a daily P&L limit was breached, `_force_exit()` set
  `state.fsm = FSMState.FORCE_EXIT`, and `_sync_fsm()` explicitly refused
  to ever leave that state (`if s.fsm == FORCE_EXIT: return`).
- Every subsequent candle re-checked the same stale `daily_pnl`, saw it
  was still over the limit, and re-ran the force-exit branch forever —
  across all future trading days, not just the day of the breach.
- Worse: even manually resetting P&L (`manual_set_pnl`) and placing a new
  manual trade afterward created a real position that the FSM dispatcher
  then permanently ignored (no SL monitoring at all), because
  `manual_buy_call`/`manual_buy_put` never called `_sync_fsm()` either.

**Fix — three coordinated changes, all in `core/algo_engine.py`:**

1. **New trading-day boundary detection** (`_session_date_for`,
   `_maybe_roll_session_day`), based on the user's explicit rule: a
   trading day runs from 09:15 market-open to the next day's 09:15, not
   midnight. Requires `Candle.timestamp` to be set (already supported by
   `Candle`; `core/candle_builder.py` already sets it for live ticks, and
   `main.py`'s `run_backtest()` already parses it from the CSV's
   `datetime` column).

2. **Mode-specific reset behavior**, per the user's explicit choices:
   - **BACKTEST**: `daily_pnl` resets to 0 at each day boundary. The
     daily-limit force-exit check is now skipped entirely in BACKTEST
     mode (`if s.mode != "BACKTEST" and self._pnl_limit_hit():`) — a
     backtest position now runs through its full signal-driven lifecycle
     instead of getting permanently stuck, since there's no way to
     "manually re-enter the next morning" inside an unattended backtest
     run.
   - **LIVE / PAPER**: at the 09:15 boundary, `daily_pnl` is **seeded
     with the current unrealized P&L** of any still-open position(s)
     (marked-to-market via `client.get_position_ltp()`), not reset to
     zero — per the user's explicit choice that a carried-over overnight
     position's exposure should count against today's limit from the
     start of the day. Any stuck `FSMState.FORCE_EXIT` from a prior day
     is also cleared back to a normal state at this point, via a new
     `allow_unstick=True` parameter on `_sync_fsm()`.

3. **Immediate FSM sync on every entry/exit**, not just at the end of
   `on_candle_close()`. Previously, `manual_buy_call`/`manual_buy_put`
   (and the Case-2 auto-entries, and SL-triggered auto-reversals) created
   a real position but left `state.fsm` stale until the *next* candle
   closed — a real, if narrow (≤60s), window where a manually-entered
   position had no SL monitoring at all. `_enter_call`, `_enter_put`,
   `_exit_call`, and `_exit_put` now all call `self._sync_fsm()`
   immediately after changing `state.call_pos`/`state.put_pos`.

**New Telegram alert** (`core/telegram_alert.py`): `alert_new_session_day()`
fires whenever a day boundary is crossed in LIVE/PAPER mode, showing the
carried-over unrealized P&L (or confirming none was carried) — distinct
from `alert_session_start()`, which only fires when the program itself
launches.

**Verified with:** unit tests reproducing the exact stuck-FORCE_EXIT
scenario (confirmed fixed — engine recovers cleanly at the next day's
9:15 candle and accepts new manual trades immediately), a carry-forward
unrealized-P&L-seeding test, a backtest-mode-never-force-exits test, and
a full re-run of the original 15-day/5,628-candle backtest end-to-end
with zero crashes and continuous trading across all 12 actual trading
days in the dataset (some days have no candle data — weekends/holidays —
and are correctly skipped since the day-boundary check only fires on
candles that actually exist).

## 5. Case-II auto-reversal used a stale, wrong reference level (core/algo_engine.py, core/sr_engine.py)

**Discovered via:** a real 7-day backtest (June 12-19) using the user's own
S/R levels, which produced a spurious PUT entry one candle after a PUT had
correctly exited via SL, while a CALL was still open with no SL trigger
anywhere near the actual price.

**Root cause:** `_check_put_trigger()` / `_check_call_trigger()` (the
"Case-II" rule — enter the opposite side if price cleanly breaks the
*other* side's nearest level while the current position hasn't hit its
own SL yet) read the trigger threshold from `pos.own_support` /
`pos.own_resistance` on the position that's still open. That field is the
position's own SL-trailing reference — it only updates when price moves
in *that position's own* favorable direction (e.g. a call's
`own_support` only advances when its *resistance* gets broken upward). If
price instead drifts the other way without ever breaking that position's
resistance, the field stays frozen at whatever was nearest when the
position was entered — potentially far from where price actually is many
candles later. In the discovered case, a call bought near 23449 kept
`own_support=23433` frozen for the entire session; when price later
drifted down to 23385, the Case-II threshold was wrongly computed against
the stale 23433 instead of the support level actually nearest the current
price (23370), making the trigger far too easy to satisfy.

A second, smaller bug in the same functions: they used plain (1.0x)
tolerance instead of the spec's 1.2x (`ENTRY_FILTER_MULT`) — inconsistent
with the SL-hit auto-reversal functions, which already used 1.2x
correctly.

**Fix:** `_check_put_trigger()` / `_check_call_trigger()` now recompute
the nearest support/resistance live against the *current* candle's close
each time (`nearest_support()` / `nearest_resistance()` from
`sr_engine.py`), and reuse the already-correct `put_entry_after_call_exit()`
/ `call_entry_after_put_exit()` functions (1.2x tolerance) instead of the
old 1.0x-tolerance `put_trigger_while_call_alive()` /
`call_trigger_while_put_alive()`, which are now marked deprecated in
`sr_engine.py` (kept for reference, no longer called).

Note: the symmetric SL-hit auto-reversal logic (`_call_sl_hit()` /
`_put_sl_hit()`, using `pos.own_support` / `pos.own_resistance` as the
reference for *that* check) was verified correct and left unchanged — it
matches the user's own original worked example precisely (a broken
resistance becomes the reference support for the very next auto-reversal
check, by design). Only the separate Case-II "no-SL-yet" trigger had the
stale-reference bug.

**Verified with:** a unit test reproducing the exact spurious-PUT scenario
(confirmed fixed — no PUT fires against a stale frozen reference), plus a
full re-run of the user's exact reported 7-day backtest, which now
produces a clean, fully explainable 2-trade sequence instead of the
previous runaway 15-buy/13-sell sequence with same-side back-to-back
re-entries.

## 6. `main.py` backtest mode crashed on a single comma typo in S/R input

**Before:** `_confirm_sr()`'s manual-edit path did
`float(x.strip())` on every comma-separated token with no validation. A
single double-comma (`23850,,23940`), trailing comma, or stray typo
anywhere in a long list crashed the entire program with a raw
`ValueError` traceback, forcing a full restart and complete re-entry of
every level.

**After:** blank tokens (from double/trailing commas) are silently
dropped. Any token that still doesn't parse as a number is reported by
name (e.g. `Could not parse: ['23zz0']`) so the user knows exactly what
to fix, and the user is asked whether to proceed with just the valid
levels or re-enter the whole list — instead of crashing.

## New: `backtest_harness/` folder

A cleaned-up, reusable version of the ad-hoc script used to find and
verify the bug above. Drop in your own CSV and S/R levels and run it
directly — see `backtest_harness/README.md` for usage and an important
caveat about what the reported P&L number does and does not mean
(index points, not real option-premium Rupees).

## Recommended one-time manual check before going LIVE

Run this once with your real credentials to be fully certain on your
exact installed SDK build:
```bash
python3 -c "
from dhanhq import DhanContext, dhanhq
import config
ctx = DhanContext(config.CLIENT_ID, config.ACCESS_TOKEN)
d = dhanhq(ctx)
print('MARGIN:', d.MARGIN)
print('INTRA :', d.INTRA)
"
```
If this prints two strings without error, you're good — `verify_sdk_constants`
will also catch this automatically on every LIVE startup from now on.

## 7. Opposite-side entry rule was wrong even after fix #5 — corrected to the user's actual rule

**Discovered via:** the user's own analysis of a clean 19-day backtest run
(after fix #5), which surfaced an 11-day gap between a CALL entering and
any PUT consideration, even though price almost certainly broke other
support levels downward many times in that window. The user clarified the
actual intended rule directly, which turned out to be simpler than — and
meaningfully different from — what fix #5 implemented.

**What fix #5 got wrong:** it correctly fixed the *stale reference* bug
(see item 5) but kept the original design's gating condition: the
opposite-side check only ran when the existing position's own SL was
**not yet active** (`if s.has_call() and not s.call_pos.sl_active:
self._check_put_trigger(...)`). That meant once a CALL's own SL activated
(which happens routinely as price moves favorably and resistance gets
broken upward), the opposite-side PUT check stopped running entirely for
as long as that CALL stayed open with an active SL — even if price later
reversed hard and broke several support levels downward. That's exactly
what produced the 11-day silent gap: the CALL's SL was active near-
continuously after 09:38 on day 1, so `_check_put_trigger` never ran
again until the CALL finally exited on June 12.

**The user's actual rule, confirmed directly:** while exactly one side is
open, check on **every candle** — independent of that position's own SL
state entirely — whether the close has broken **any** S/R level (not
just the nearest one, and not the open position's own tracked
support/resistance) in the opposite direction, by plain tolerance (the
same 1.0x threshold used for SL-activation, not the 1.2x entry-filter
multiplier used elsewhere). If so, and no duplicate same-side position
exists, enter the opposite side immediately. This does not apply while
fully flat (both sides closed) — only a manual trade restarts trading
from flat, unchanged from before.

**Fix:**
- `on_candle_close()`: removed the `and not pos.sl_active` gating
  entirely. `_check_put_trigger()` / `_check_call_trigger()` now run on
  every candle whenever exactly one side is open, full stop.
- `_check_put_trigger()` / `_check_call_trigger()`: rewritten to scan
  *every* level in `state.sr_levels` for a break in the relevant
  direction (plain 1.0x tolerance), rather than computing against a
  single nearest/tracked level. The duplicate-same-side guard
  (`if s.has_put(): return` / `if s.has_call(): return`) is what
  prevents this from re-firing every subsequent candle once the
  opposite side is entered — no additional state needed.
- The now-fully-unused `put_entry_after_call_exit()` /
  `call_entry_after_put_exit()` calls inside these two functions were
  removed; those functions are still correctly used elsewhere, in
  `_call_sl_hit()` / `_put_sl_hit()` for the (different, verified
  correct) SL-hit auto-reversal rule.
- Also fixed in the same change: a cosmetic-only bug in the log
  message's "nearest broken level" selection (`min`/`max` were swapped
  for both the CALL and PUT cases) — this never affected any trading
  decision, only made the log text temporarily confusing
  (e.g. logging "broke resistance 23056" when the level that actually
  mattered for the decision was 23582).

**Behavioral consequence:** the strategy is now meaningfully more active
than under fix #5's (incorrect) gating — re-running the user's original
19-day scenario now produces 33 entries/31 exits instead of 3, with the
very first opposite-side entry firing one minute after the manual first
trade (matching the user's own expectation precisely, since many S/R
levels near any reasonable starting spot are already broken by more than
tolerance). This is expected and correct per the user's explicit rule,
not over-trading — every single entry was traced and verified against
which exact S/R level it broke.

**Verified with:** a unit test confirming the rule does NOT fire
prematurely (no level broken yet) and DOES fire immediately on a genuine
break regardless of the existing position's own SL state, plus a full
re-run of the user's exact reported 19-day scenario showing the very
first CALL entry now firing 1 minute after the manual PUT, with every
subsequent entry's triggering level confirmed by direct calculation.

## Not changed (deferred per user's decision)

- **Multi-index concurrency**: the architecture supports one active index
  per running process (switch between Nifty50/BankNifty/Sensex/Crude MCX
  between sessions, not simultaneously within one process). User confirmed
  this can be deferred — trading one index at a time for now is fine.
- `Candle.body_range()` returns `high - low` (true range), which is a
  slightly misleading name but not used incorrectly anywhere — left as-is
  to avoid unnecessary churn.
