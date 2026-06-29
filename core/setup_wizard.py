"""
Interactive startup wizard — step-by-step configuration before trading begins.

Run from main.py on first start (or when session restore is declined).
"""
from __future__ import annotations
import logging
import re
from typing import Any

import config
from core.states import SRLevel

log = logging.getLogger("algo.wizard")


def pick_mode_interactive() -> str:
    print("\n+============================================================+")
    print("|   STEP 0 — TRADING MODE                                    |")
    print("+============================================================+")
    print("|  [1] PAPER  — Real market data, simulated orders (safe)    |")
    print("|  [2] LIVE   — Real orders on Dhan (requires IP whitelist)  |")
    print("+============================================================+")
    print("|  RULES:                                                    |")
    print("|  • Only ONE mode runs at a time (paper OR live)            |")
    print("|  • Paper → Live: Q-paper first, then start live            |")
    print("|  • Live → Paper: BLOCKED while live positions are open       |")
    print("+============================================================+")
    while True:
        c = input("Select mode (1=paper / 2=live): ").strip()
        if c in ("1", "paper", "p"):
            return "paper"
        if c in ("2", "live", "l"):
            return "live"
        print("  Enter 1 or 2.")


def pick_index() -> str:
    options = list(config.INDEX_CONFIG.keys())
    print("\n+-- STEP 1 — INDEX SELECTION --+")
    for i, k in enumerate(options):
        ic = config.INDEX_CONFIG[k]
        print(f"  [{i+1}] {k:<12} {ic['name']}  (lot={ic['lot_size']}, step={ic['strike_step']})")
    while True:
        inp = input(f"\nChoose index (1-{len(options)} or name): ").strip()
        if inp.isdigit() and 1 <= int(inp) <= len(options):
            return options[int(inp) - 1]
        if inp.upper() in options:
            return inp.upper()
        print("  Invalid choice — try again.")


def pick_global_tolerance() -> float:
    print("\n+-- STEP 2 — GLOBAL TOLERANCE --+")
    print(f"  Default tolerance = {config.TOLERANCE} index points")
    print("  Used for any S/R level where you do not set a specific tolerance.")
    inp = input(f"Global tolerance [{config.TOLERANCE}]: ").strip()
    if not inp:
        return float(config.TOLERANCE)
    try:
        return float(inp)
    except ValueError:
        print(f"  Invalid — using {config.TOLERANCE}")
        return float(config.TOLERANCE)


def _config_default_levels(global_tol: float) -> list[SRLevel]:
    levels = sorted([SRLevel(r[0], r[1]) for r in config.SR_LEVELS])
    for sr in levels:
        if sr.tolerance is None:
            sr.tolerance = global_tol
    return levels


def _parse_sr_tokens(raw: str, global_tol: float) -> list[SRLevel]:
    """
    Parse one line or comma-separated list:
      6670,6645,6620
      23956 9, 24000
      23805
    """
    levels: list[SRLevel] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split()
        try:
            lv = float(parts[0])
            tol = float(parts[1]) if len(parts) > 1 else global_tol
            levels.append(SRLevel(lv, tol))
        except ValueError:
            return []
    return levels


def _manual_sr_lines(global_tol: float) -> list[SRLevel]:
    levels: list[SRLevel] = []
    print("  Enter one level per line (commas OK on a line). Empty line when done.")
    while True:
        raw = input(f"  S/R #{len(levels)+1} (empty=done): ").strip()
        if not raw:
            break
        parsed = _parse_sr_tokens(raw, global_tol)
        if not parsed:
            print("  Invalid — use: 23805  or  23956 9  or  6670,6645,6620")
            continue
        levels.extend(parsed)
    return sorted(levels)


def pick_sr_levels(global_tol: float) -> list[SRLevel]:
    print("\n+-- STEP 3 — SUPPORT / RESISTANCE LEVELS --+")
    print("  Enter levels one per line, OR paste comma-separated on one line.")
    print("  Format:")
    print("    23805          → uses global tolerance")
    print("    23956 9        → level 23956 with tolerance 9")
    print("    6670,6645,6620 → multiple levels at once")
    print("  Empty line / Enter alone → load defaults from config.py:")
    for sr in sorted([SRLevel(r[0], r[1]) for r in config.SR_LEVELS]):
        t = f"  tol={sr.tolerance}" if sr.tolerance else ""
        print(f"    {sr.level}{t}")

    first = input(
        "\nPaste levels (comma-separated), type 'manual' for line-by-line, "
        "or Enter for config defaults: "
    ).strip()
    if not first:
        return _config_default_levels(global_tol)
    if first.lower() == "manual":
        levels = _manual_sr_lines(global_tol)
        if not levels:
            print("  No levels entered — using config defaults.")
            return _config_default_levels(global_tol)
        return levels

    parsed = _parse_sr_tokens(first, global_tol)
    if parsed:
        return sorted(parsed)

    print(f"  Could not parse {first!r} — using config defaults.")
    print("  Tip: paste 6670,6645,6620 or type 'manual' for one level per line.")
    return _config_default_levels(global_tol)


def pick_expiry(client: Any, index_key: str | None = None) -> str:
    idx = index_key or config.ACTIVE_INDEX
    print(f"\n+-- STEP 4 — OPTION EXPIRY ({idx}, validated with Dhan API) --+")
    api_list: list[str] = []
    try:
        getter = getattr(client, "get_expiry_list", None)
        if getter is not None:
            api_list = getter(index_key=idx)
        if api_list:
            print("  Available expiries from Dhan:")
            for i, e in enumerate(api_list[:12]):
                print(f"    [{i}] {e}")
        else:
            print("  Dhan returned no expiries — enter date manually (YYYY-MM-DD).")
    except Exception as ex:
        log.warning(f"Expiry list failed ({idx}): {ex}")
        print(f"  Could not fetch expiry list for {idx} — enter date manually.")

    while True:
        inp = input(f"\nEnter expiry YYYY-MM-DD [{config.OPTION_EXPIRY}]: ").strip()
        expiry = inp or config.OPTION_EXPIRY
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", expiry):
            print("  Format must be YYYY-MM-DD")
            continue
        if api_list and expiry not in api_list:
            print(f"  '{expiry}' NOT in Dhan expiry list — try again.")
            nearest = [e for e in api_list if e.startswith(expiry[:7])]
            if nearest:
                print(f"  Suggestions: {', '.join(nearest[:5])}")
            continue
        print(f"  Expiry confirmed: {expiry}")
        return expiry


def pick_lots() -> int:
    print("\n+-- STEP 5 — LOTS PER ORDER --+")
    idx = config.INDEX_CONFIG[config.ACTIVE_INDEX]
    lot_size = idx["lot_size"]
    while True:
        inp = input(f"Number of lots [{config.NUM_LOTS}] (1 lot = {lot_size} qty): ").strip()
        if not inp:
            return int(config.NUM_LOTS)
        try:
            n = int(inp)
            if n >= 1:
                return n
        except ValueError:
            pass
        print("  Enter a positive integer.")


def pick_first_trade() -> str:
    print("\n+-- STEP 6 — FIRST MANUAL TRADE --+")
    print("  Strategy requires one manual entry to start the ladder.")
    while True:
        inp = input("  Side: call / put : ").strip().lower()
        if inp in ("call", "c", "put", "p"):
            return "call" if inp in ("call", "c") else "put"
        print("  Enter 'call' or 'put'.")


def confirm_summary(mode: str, index: str, global_tol: float,
                    levels: list[SRLevel], expiry: str, lots: int,
                    first_side: str, spot: float) -> bool:
    print("\n+============================================================+")
    print("|   STEP 7 — CONFIRM & START                                   |")
    print("+============================================================+")
    print(f"  Mode       : {mode.upper()}")
    print(f"  Index      : {index}")
    print(f"  Expiry     : {expiry}")
    print(f"  Lots       : {lots}  (qty={lots * config.INDEX_CONFIG[index]['lot_size']})")
    print(f"  Global tol : {global_tol}")
    print(f"  S/R levels : {len(levels)}")
    for sr in levels:
        print(f"    {sr.level}  (tol={sr.tolerance})")
    print(f"  First trade: {first_side.upper()} @ spot ~{spot:.2f}")
    print(f"  PnL limits : {config.PNL_LIMIT_MODE} (profit={config.DAILY_PROFIT_TARGET}, "
          f"loss={config.DAILY_LOSS_LIMIT})")
    print("+============================================================+")
    while True:
        ans = input("Start trading? (yes/no): ").strip().lower()
        if ans in ("yes", "y"):
            return True
        if ans in ("no", "n"):
            return False
        print("  Enter yes or no.")


def run_full_wizard(client: Any, mode: str,
                    index_key: str | None = None) -> tuple[list[SRLevel], str, int, str] | None:
    """
    Returns (sr_levels, expiry, num_lots, first_side) or None if user cancelled.
    Sets config.ACTIVE_INDEX, TOLERANCE, NUM_LOTS, OPTION_EXPIRY.
    Pass index_key when index was already chosen (e.g. before session restore).
    """
    config.ACTIVE_INDEX = index_key or pick_index()
    global_tol = pick_global_tolerance()
    config.TOLERANCE = global_tol
    levels = pick_sr_levels(global_tol)
    expiry = pick_expiry(client, config.ACTIVE_INDEX)
    config.OPTION_EXPIRY = expiry
    lots = pick_lots()
    config.NUM_LOTS = lots
    first_side = pick_first_trade()
    try:
        spot = client.get_spot_ltp()
    except Exception:
        spot = 0.0
    if not confirm_summary(mode, config.ACTIVE_INDEX, global_tol, levels,
                           expiry, lots, first_side, spot):
        print("\n  Setup cancelled.")
        return None
    return levels, expiry, lots, first_side
