"""
core/paper_api.py — Paper trading. Real market data, simulated orders.

Changes in this version:
  - Startup never crashes even if market is closed or network is down
  - get_spot_ltp() uses the same market-aware fallback as dhan_api.py
  - unrealised_pnl() confirmed present (was missing in earlier version)
  - Clear warning printed when running outside market hours
"""
from __future__ import annotations
import json, logging, os, time
from datetime import date
from dhanhq import DhanContext

import config
from core.states import Position, AlgoState
from core.dhan_api import (
    make_dhan, get_spot_ltp, get_option_ltp,
    get_option_security_id, get_expiry_list,
    _is_market_hours,
)

log = logging.getLogger("algo.paper")


class PaperClient:

    def __init__(self, ctx: DhanContext):
        self._dhan        = make_dhan(ctx)
        self._counter     = 0
        self.current_spot = 0.0
        self.trades: list[dict] = []

        # Warm up spot price — graceful if market is closed
        try:
            price = get_spot_ltp(self._dhan)
            self.current_spot = price
            if price > 0:
                log.info(f"PaperClient warm spot={price:.2f}")
            else:
                log.warning(
                    "PaperClient: spot price = 0 (market is closed or network issue). "
                    "This is normal outside 9:15-15:30 IST. "
                    "Real price will come from WebSocket when market opens."
                )
        except Exception as e:
            log.warning(f"PaperClient spot warm-up failed: {e}. Starting with spot=0.")

        os.makedirs("data", exist_ok=True)
        self._load()

        if not _is_market_hours():
            log.info(
                "NOTE: Market is currently CLOSED.\n"
                "  - Paper trading will work but fills use fallback price (0.5% of spot)\n"
                "  - Live ticks start when market opens at 9:15 AM IST\n"
                "  - All logic (S/R, FSM, candle filters) works correctly"
            )

        log.info("PaperClient ready — ALL ORDERS SIMULATED")

    # ── Market data ───────────────────────────────────────────────────────────

    def get_spot_ltp(self) -> float:
        p = get_spot_ltp(self._dhan)
        if p > 0:
            self.current_spot = p
        return p

    def get_option_ltp(self, security_id: str) -> float:
        opt_seg = config.INDEX_CONFIG[config.ACTIVE_INDEX]["opt_seg"]
        return get_option_ltp(self._dhan, security_id, opt_seg)

    def get_position_ltp(self, pos: Position) -> float:
        return self.get_option_ltp(pos.security_id)

    def get_option_security_id(self, strike: float, opt_type: str, expiry: str) -> str:
        return get_option_security_id(self._dhan, strike, opt_type, expiry)

    def get_expiry_list(self) -> list[str]:
        return get_expiry_list(self._dhan)

    def check_funds_before_buy(self, security_id: str, strike: float,
                               side: str) -> tuple[bool, float, float]:
        """Paper mode: always allow (no real capital at risk)."""
        return True, 0.0, 0.0

    # ── Simulated orders ──────────────────────────────────────────────────────

    def buy_option(self, security_id: str, strike: float,
                   side: str, entry_spot: float) -> Position:
        idx  = config.INDEX_CONFIG[config.ACTIVE_INDEX]
        qty  = config.NUM_LOTS * idx["lot_size"]
        fill = self._fill(security_id, entry_spot)
        oid  = self._oid()
        self._rec("BUY", side, security_id, strike, qty, fill, entry_spot)
        log.info(f"[PAPER BUY] {side} k={strike} fill=Rs{fill:.2f} spot={entry_spot:.2f} qty={qty}")
        return Position(side=side, strike=strike, security_id=security_id,
                        entry_price=fill, entry_spot=entry_spot,
                        order_id=oid, quantity=qty)

    def sell_option(self, pos: Position, reason: str = "") -> float:
        fill = self._fill(pos.security_id, self.current_spot)
        pnl  = (fill - pos.entry_price) * pos.quantity
        self._rec("SELL", pos.side, pos.security_id, pos.strike,
                  pos.quantity, fill, self.current_spot, reason=reason)
        log.info(f"[PAPER SELL] {pos.side} k={pos.strike} fill=Rs{fill:.2f} pnl=Rs{pnl:,.2f}")
        return fill

    def force_sell_all(self, state: AlgoState) -> float:
        total = 0.0
        for pos in [state.call_pos, state.put_pos]:
            if pos is not None:
                ep = self.sell_option(pos, "FORCE_SELL")
                total += (ep - pos.entry_price) * pos.quantity
        state.call_pos = state.put_pos = None
        return total

    def unrealised_pnl(self, state: AlgoState) -> float:
        """Mark-to-market PnL for all open positions using real LTPs."""
        total = 0.0
        for pos in [state.call_pos, state.put_pos]:
            if pos is None:
                continue
            try:
                ltp    = self._fill(pos.security_id, self.current_spot)
                total += (ltp - pos.entry_price) * pos.quantity
            except Exception:
                pass
        return total

    # ── Internal ──────────────────────────────────────────────────────────────

    def _fill(self, security_id: str, ref_spot: float) -> float:
        """
        Get real option LTP via ohlc_data.
        During market hours: returns live LTP.
        Outside market hours: returns previous close (options keep last_price).
        Fallback: 0.5% of current spot (if API fails entirely).
        """
        try:
            p = self.get_option_ltp(security_id)
            if p > 0:
                return round(p, 2)
        except Exception as e:
            log.warning(f"Option LTP failed ({security_id}): {e}")

        # Fallback: 0.5% of spot
        spot = ref_spot if ref_spot > 0 else self.current_spot
        fb = round(max(spot, 1) * 0.005, 2)
        log.warning(f"Using fallback fill Rs{fb:.2f} (0.5% of spot {spot:.2f})")
        return fb

    def _oid(self) -> str:
        self._counter += 1
        return f"PAPER{self._counter:05d}"

    def _rec(self, tx, side, sid, strike, qty, fill, spot, reason=""):
        self.trades.append({
            "date": str(date.today()), "time": time.strftime("%H:%M:%S"),
            "type": tx, "side": side, "security_id": sid,
            "strike": strike, "quantity": qty,
            "fill_price": fill, "spot": spot, "reason": reason,
            "index": config.ACTIVE_INDEX,
        })
        self._save()

    def _save(self):
        try:
            with open(config.PAPER_TRADE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.trades, f, indent=2)
        except Exception as e:
            log.error(f"Paper log save: {e}")

    def _load(self):
        if not os.path.exists(config.PAPER_TRADE_FILE):
            return
        try:
            with open(config.PAPER_TRADE_FILE, encoding="utf-8") as f:
                self.trades = json.load(f)
            log.info(f"Loaded {len(self.trades)} existing paper trades")
        except Exception:
            self.trades = []
