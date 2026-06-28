"""
core/dhan_api.py  —  All Dhan API v2.2.0 calls.

CONFIRMED BEHAVIOUR (from live testing + official docs):

  ohlc_data for IDX_I (index):
    - During market hours (9:15-15:30 IST):
        returns {"IDX_I": {"13": {"last_price": 24200.0, "ohlc": {...}}}}
    - Outside market hours:
        returns {"IDX_I": {}}   <-- EMPTY, no last_price available
    FIX: when empty, fall back to daily_candle() which always has previous close.

  ohlc_data for NSE_FNO / MCX_FNO (options):
    - Outside market hours: returns last_price = previous close (not empty)
    - Confirmed from docs: {"NSE_FNO": {"49081": {"last_price": 368.15, "ohlc": {...}}}}

  DNS / network failures:
    - Transient errors (midnight blips, brief outages)
    - FIX: retry with backoff, allow startup with price=0 and warn user

  STATIC IP: only required for place_order (BUY/SELL).
    All data APIs (ohlc_data, daily_candle, option_chain, MarketFeed)
    work from any IP address.
"""

from __future__ import annotations
import datetime
import logging
import time
from dhanhq import DhanContext, dhanhq

import config
from core.states import Position, AlgoState

log = logging.getLogger("algo.api")


def make_context() -> DhanContext:
    return DhanContext(config.CLIENT_ID, config.ACCESS_TOKEN)

def make_dhan(ctx: DhanContext) -> dhanhq:
    return dhanhq(ctx)


# ─── Response unwrapper ───────────────────────────────────────────────────────

def _unwrap(resp: dict):
    """Handle single and double-nested Dhan response shapes."""
    outer = resp.get("data", {})
    if isinstance(outer, dict) and "data" in outer and "status" in outer:
        return outer["data"]
    return outer


# ─── Market hours check ───────────────────────────────────────────────────────

def _is_market_hours(index_key: str = None) -> bool:
    """
    Returns True if current IST time is within market trading hours.
    NSE/BSE: 9:15 - 15:30 IST
    MCX: 9:00 - 23:30 IST
    """
    if index_key is None:
        index_key = config.ACTIVE_INDEX

    # Convert UTC to IST (UTC + 5:30)
    now_utc = datetime.datetime.utcnow()
    now_ist = now_utc + datetime.timedelta(hours=5, minutes=30)
    t = now_ist.time()

    if index_key == "CRUDE_MCX":
        return datetime.time(9, 0) <= t <= datetime.time(23, 30)
    else:  # NIFTY50, BANKNIFTY, SENSEX
        return datetime.time(9, 15) <= t <= datetime.time(15, 30)


# ─── Spot price — with market-hours-aware fallback ───────────────────────────

def get_spot_ltp(dhan: dhanhq, index_key: str = None) -> float:
    """
    Get index spot price.

    Strategy:
      1. Try ohlc_data() — works during market hours, returns live price
      2. If empty (market closed) or error → fall back to daily_candle()
         which always returns the previous session's close price
      3. If that also fails → return 0.0 and warn (allows startup to proceed)
    """
    if index_key is None:
        index_key = config.ACTIVE_INDEX
    idx = config.INDEX_CONFIG[index_key]

    # --- Attempt 1: ohlc_data (live price, works during market hours) ---
    try:
        price = _ohlc_spot(dhan, idx)
        if price > 0:
            return price
        log.debug(f"ohlc_data returned 0 for {index_key} — trying daily fallback")
    except Exception as e:
        log.debug(f"ohlc_data failed ({index_key}): {e} — trying daily fallback")

    # --- Attempt 2: daily_candle (previous close, works 24/7) ---
    try:
        price = _daily_candle_close(dhan, idx)
        if price > 0:
            log.info(f"Using previous-close from daily_candle: {price:.2f} "
                     f"(market {'open — ohlc failed' if _is_market_hours(index_key) else 'closed'})")
            return price
    except Exception as e:
        log.warning(f"daily_candle fallback failed ({index_key}): {e}")

    # --- Fallback: return 0 with clear warning ---
    log.warning(
        f"Could not fetch spot price for {index_key}. "
        f"Market {'is open — check connectivity' if _is_market_hours(index_key) else 'is closed (normal outside hours)'}. "
        f"Using 0.0 — first-trade fills may be inaccurate."
    )
    return 0.0


def _ohlc_spot(dhan: dhanhq, idx: dict) -> float:
    """Try to get spot from ohlc_data. Returns 0 if empty (market closed)."""
    seg  = idx["exchange_seg"]
    sid  = int(idx["security_id"])
    resp = dhan.ohlc_data(securities={seg: [sid]})

    if resp.get("status") != "success":
        raise RuntimeError(f"ohlc_data status failure: {resp}")

    inner    = _unwrap(resp)
    seg_data = inner.get(seg, {})

    # MCX uses "MCX" key in response, not "MCX_COMM"
    if not seg_data:
        seg_data = inner.get("MCX", {})
    if not seg_data:
        # Try any non-empty value
        for v in inner.values():
            if isinstance(v, dict) and v:
                seg_data = v; break

    if not seg_data:
        return 0.0  # Empty — market is closed

    row = (seg_data.get(idx["security_id"])
           or seg_data.get(sid)
           or next(iter(seg_data.values()), None))

    if row is None:
        return 0.0

    price = float(row.get("last_price", 0))
    return price


def _daily_candle_close(dhan: dhanhq, idx: dict) -> float:
    """
    Get last available close from daily candle data.
    This works 24/7 — always has the previous session's close.
    Uses Dhan's daily_candle endpoint:
        dhan.daily_candle_data(security_id, exchange_segment, instrument)
    """
    # Use last 5 days to ensure we get at least one trading day
    to_dt   = datetime.date.today()
    from_dt = to_dt - datetime.timedelta(days=7)

    resp = dhan.historical_daily_data(
        security_id      = idx["security_id"],
        exchange_segment = idx["exchange_seg"],
        instrument_type  = "INDEX",
        from_date        = str(from_dt),
        to_date          = str(to_dt),
    )

    if resp.get("status") != "success":
        raise RuntimeError(f"historical_daily_data failed: {resp}")

    closes = resp.get("data", {}).get("close", [])
    if closes:
        return float(closes[-1])   # most recent close

    raise RuntimeError("historical_daily_data returned no close prices")


# ─── Option LTP ───────────────────────────────────────────────────────────────

def get_option_ltp(dhan: dhanhq, security_id: str, opt_seg: str) -> float:
    """
    Get option LTP via ohlc_data.
    For options (NSE_FNO/MCX_FNO), Dhan returns previous close even when
    market is closed — unlike IDX_I which returns empty.
    """
    resp = dhan.ohlc_data(securities={opt_seg: [int(security_id)]})
    if resp.get("status") != "success":
        raise RuntimeError(f"ohlc_data (option) failed: {resp}")

    inner    = _unwrap(resp)
    seg_data = inner.get(opt_seg, {})
    if not seg_data:
        for v in inner.values():
            if isinstance(v, dict) and v:
                seg_data = v; break

    row = (seg_data.get(security_id)
           or seg_data.get(int(security_id))
           or next(iter(seg_data.values()), None))

    if row is None:
        raise RuntimeError(f"No data for option {security_id} in {opt_seg}")

    return float(row["last_price"])


# ─── Option chain ─────────────────────────────────────────────────────────────

def get_option_security_id(dhan: dhanhq, strike: float,
                            opt_type: str, expiry: str,
                            index_key: str = None) -> str:
    if index_key is None:
        index_key = config.ACTIVE_INDEX
    idx = config.INDEX_CONFIG[index_key]
    log.info(f"option_chain  {index_key}  strike={strike}  {opt_type}  {expiry}")

    resp = dhan.option_chain(
        under_security_id      = idx["under_sec_id"],
        under_exchange_segment = idx["exchange_seg"],
        expiry                 = expiry,
    )
    if resp.get("status") != "success":
        raise RuntimeError(f"option_chain failed: {resp}")

    inner = _unwrap(resp)
    oc    = inner.get("oc")
    if not oc:
        raise RuntimeError(f"'oc' missing. inner keys={list(inner.keys())}")

    target = f"{float(strike):.6f}"
    if target not in oc:
        for k in oc:
            if abs(float(k) - strike) < 0.5:
                target = k; break
        else:
            avail = sorted(float(k) for k in oc)
            raise ValueError(f"Strike {strike} not found. Sample: {avail[:5]}...{avail[-5:]}")

    sub = "ce" if opt_type == "CE" else "pe"
    sid = str(oc[target][sub]["security_id"])
    log.info(f"  -> security_id={sid}")
    return sid


def get_expiry_list(dhan: dhanhq, index_key: str = None) -> list[str]:
    if index_key is None:
        index_key = config.ACTIVE_INDEX
    idx  = config.INDEX_CONFIG[index_key]
    resp = dhan.expiry_list(
        under_security_id      = idx["under_sec_id"],
        under_exchange_segment = idx["exchange_seg"],
    )
    if resp.get("status") != "success":
        raise RuntimeError(f"expiry_list failed: {resp}")
    inner = _unwrap(resp)
    if isinstance(inner, list):
        expiries = inner
    else:
        expiries = next((v for v in inner.values() if isinstance(v, list)), [])
    return sorted(e for e in expiries if isinstance(e, str) and len(e) == 10 and e[4] == "-")


# ─── Open positions ───────────────────────────────────────────────────────────

def get_open_positions(dhan: dhanhq, index_key: str = None) -> list[dict]:
    if index_key is None:
        index_key = config.ACTIVE_INDEX
    opt_seg = config.INDEX_CONFIG[index_key]["opt_seg"]
    resp    = dhan.get_positions()
    if resp.get("status") != "success":
        return []
    return [
        p for p in resp.get("data", [])
        if p.get("exchangeSegment") == opt_seg and int(p.get("netQty", 0)) != 0
    ]


# ─── Fund / margin check ──────────────────────────────────────────────────────

def get_available_balance(dhan: dhanhq) -> float:
    """Return available trading balance from Dhan fund-limit API."""
    resp = dhan.get_fund_limits()
    if resp.get("status") != "success":
        raise RuntimeError(f"get_fund_limits failed: {resp}")
    data = _unwrap(resp)
    if not isinstance(data, dict):
        data = resp.get("data", {})
    # API field is misspelled "availabelBalance" in official docs
    for key in ("availabelBalance", "availableBalance", "available_balance"):
        if key in data and data[key] is not None:
            return float(data[key])
    raise RuntimeError(f"No balance field in fund limits: {list(data.keys())}")


def estimate_buy_margin(dhan: dhanhq, security_id: str, quantity: int,
                        index_key: str = None) -> float:
    """Estimate margin required for a BUY order via margin_calculator."""
    if index_key is None:
        index_key = config.ACTIVE_INDEX
    idx = config.INDEX_CONFIG[index_key]
    resp = dhan.margin_calculator(
        security_id      = security_id,
        exchange_segment = idx["opt_seg"],
        transaction_type = dhan.BUY,
        quantity         = quantity,
        product_type     = _product(dhan),
        price            = 0,
        trigger_price    = 0,
    )
    if resp.get("status") != "success":
        raise RuntimeError(f"margin_calculator failed: {resp}")
    data = _unwrap(resp)
    if not isinstance(data, dict):
        data = resp.get("data", {})
    for key in ("totalMargin", "total_margin", "spanMargin"):
        if key in data and data[key] is not None:
            return float(data[key])
    # Sum known margin components if total not present
    parts = [data.get(k, 0) or 0 for k in
             ("spanMargin", "exposureMargin", "variableMargin", "brokerage")]
    total = sum(float(p) for p in parts)
    if total > 0:
        return total
    raise RuntimeError(f"Could not parse margin from: {list(data.keys())}")


def check_funds_before_buy(dhan: dhanhq, security_id: str, strike: float,
                           side: str, index_key: str = None) -> tuple[bool, float, float]:
    """
    Returns (ok, available_balance, required_margin).
    Skips order when available balance is below required margin.
    """
    if index_key is None:
        index_key = config.ACTIVE_INDEX
    qty = config.NUM_LOTS * config.INDEX_CONFIG[index_key]["lot_size"]
    try:
        avail = get_available_balance(dhan)
    except Exception as e:
        log.error(f"Fund check failed (balance): {e}")
        return False, 0.0, 0.0
    try:
        need = estimate_buy_margin(dhan, security_id, qty, index_key)
    except Exception as e:
        log.warning(f"margin_calculator failed ({e}) — using 5% spot fallback")
        spot = get_spot_ltp(dhan, index_key)
        need = max(spot, 1) * 0.05 * qty
    ok = avail >= need
    if not ok:
        log.warning(f"Insufficient funds for {side} k={strike}: "
                    f"avail=Rs{avail:,.2f} need=Rs{need:,.2f}")
    return ok, avail, need


# ─── Orders ───────────────────────────────────────────────────────────────────

def _product(dhan: dhanhq) -> str:
    pt = config.PRODUCT_TYPE.upper()
    try:
        return dhan.MARGIN if pt == "DELIVERY" else dhan.INTRA
    except AttributeError as e:
        raise RuntimeError(
            f"dhanhq SDK is missing the '{'MARGIN' if pt == 'DELIVERY' else 'INTRA'}' "
            f"product-type constant ({e}). This usually means your installed dhanhq "
            f"version differs from what this code expects (v2.2.0). Run "
            f"verify_sdk_constants(dhan) at startup, or check "
            f"`pip show dhanhq` against requirements.txt."
        ) from e


def verify_sdk_constants(dhan: dhanhq) -> None:
    """
    Call once at startup (before any order placement) to fail loudly and
    early if the installed dhanhq SDK is missing constants this code relies
    on — rather than discovering it mid-trade via a buried AttributeError.
    """
    required = ["BUY", "SELL", "MARKET", "INTRA", "MARGIN"]
    missing = [name for name in required if not hasattr(dhan, name)]
    if missing:
        raise RuntimeError(
            f"dhanhq SDK is missing required constant(s): {missing}. "
            f"Check `pip show dhanhq` — this code expects v2.2.0 "
            f"(see requirements.txt). If MARGIN is the only one missing, "
            f"your SDK may use a different name (e.g. dhan.CARRYFORWARD or "
            f"dhan.NORMAL) — check the installed package's source or "
            f"`dir(dhan)` and update _product() in dhan_api.py accordingly."
        )
    log.info("SDK constant check passed: " + ", ".join(required))


def buy_option(dhan: dhanhq, security_id: str, strike: float,
               side: str, entry_spot: float, index_key: str = None) -> Position:
    if index_key is None:
        index_key = config.ACTIVE_INDEX
    idx = config.INDEX_CONFIG[index_key]
    qty = config.NUM_LOTS * idx["lot_size"]
    ok, avail, need = check_funds_before_buy(dhan, security_id, strike, side, index_key)
    if not ok:
        raise RuntimeError(
            f"Insufficient funds for BUY {side} k={strike}: "
            f"avail=Rs{avail:,.2f} need=Rs{need:,.2f}"
        )
    log.info(f"BUY {side} k={strike} sec={security_id} qty={qty} prod={config.PRODUCT_TYPE}")

    resp = dhan.place_order(
        security_id      = security_id,
        exchange_segment = idx["opt_seg"],
        transaction_type = dhan.BUY,
        quantity         = qty,
        order_type       = dhan.MARKET,
        product_type     = _product(dhan),
        price            = 0,
    )
    if resp.get("status") != "success":
        raise RuntimeError(f"place_order BUY failed: {resp}")

    order_id    = str(resp["data"]["orderId"])
    entry_price = _wait_fill(dhan, order_id, security_id, idx["opt_seg"])
    log.info(f"  BUY FILLED oid={order_id} prem=Rs{entry_price:.2f}")
    return Position(side=side, strike=strike, security_id=security_id,
                    entry_price=entry_price, entry_spot=entry_spot,
                    order_id=order_id, quantity=qty)


def sell_option(dhan: dhanhq, pos: Position, reason: str = "",
                index_key: str = None) -> float:
    if index_key is None:
        index_key = config.ACTIVE_INDEX
    idx = config.INDEX_CONFIG[index_key]
    log.info(f"SELL {pos.side} k={pos.strike} sec={pos.security_id} qty={pos.quantity} reason={reason}")

    resp = dhan.place_order(
        security_id      = pos.security_id,
        exchange_segment = idx["opt_seg"],
        transaction_type = dhan.SELL,
        quantity         = pos.quantity,
        order_type       = dhan.MARKET,
        product_type     = _product(dhan),
        price            = 0,
    )
    if resp.get("status") != "success":
        raise RuntimeError(f"place_order SELL failed: {resp}")

    order_id   = str(resp["data"]["orderId"])
    exit_price = _wait_fill(dhan, order_id, pos.security_id, idx["opt_seg"])
    pnl        = (exit_price - pos.entry_price) * pos.quantity
    log.info(f"  SELL FILLED prem=Rs{exit_price:.2f} pnl=Rs{pnl:,.2f}")
    return exit_price


def force_sell_all(dhan: dhanhq, state: AlgoState, index_key: str = None) -> float:
    total = 0.0
    for pos in [state.call_pos, state.put_pos]:
        if pos is not None:
            try:
                ep = sell_option(dhan, pos, "FORCE_SELL", index_key)
                total += (ep - pos.entry_price) * pos.quantity
            except Exception as e:
                log.error(f"force_sell error: {e}")
    state.call_pos = state.put_pos = None
    return total


def download_minute_data(dhan: dhanhq, from_date: str, to_date: str,
                          index_key: str = None) -> dict:
    if index_key is None:
        index_key = config.ACTIVE_INDEX
    idx = config.INDEX_CONFIG[index_key]
    return dhan.intraday_minute_data(
        security_id      = idx["security_id"],
        exchange_segment = idx["exchange_seg"],
        instrument_type  = "INDEX",
        from_date        = from_date,
        to_date          = to_date,
    )


# ─── Internal ─────────────────────────────────────────────────────────────────

def _wait_fill(dhan: dhanhq, order_id: str, security_id: str,
               opt_seg: str, retries: int = 20) -> float:
    for _ in range(retries):
        try:
            resp = dhan.get_order_by_id(order_id)
            if resp.get("status") == "success":
                d = resp.get("data", {})
                if d.get("orderStatus") in ("TRADED", "COMPLETE"):
                    p = float(d.get("averageTradedPrice", 0))
                    if p > 0: return p
        except Exception as e:
            log.debug(f"_wait_fill: {e}")
        time.sleep(0.4)
    log.warning(f"Fill not confirmed for {order_id} — using LTP")
    try:
        return get_option_ltp(dhan, security_id, opt_seg)
    except Exception:
        return 0.0
