"""
core/algo_engine.py — Complete FSM engine with Telegram alerts.
Mode-agnostic: receives a client object at __init__.
"""
from __future__ import annotations
import logging
import datetime as _dt
from typing import Any

import config
from core.states import AlgoState, FSMState, Position, SRLevel, CandleFilterState, Candle
from core.sr_engine import (
    nearest_support, nearest_resistance,
    next_res_above, next_sup_below,
    assign_call_res, assign_put_sup,
    resistance_broken, support_broken,
    call_sl, put_sl, call_sl_hit, put_sl_hit,
    put_entry_after_call_exit, call_entry_after_put_exit,
    put_opposite_trigger, call_opposite_trigger,
    is_inverted_green_hammer, is_red_hanging_man,
    select_call_strike, select_put_strike,
)
import core.telegram_alert as tg

log = logging.getLogger("algo.engine")


class AlgoEngine:

    def __init__(self, client: Any, state: AlgoState):
        self.client = client
        self.state  = state
        self._step  = config.INDEX_CONFIG[state.index_key]["strike_step"]
        self._last_session_date: _dt.date | None = None  # tracks trading-day boundary

    # ── Daily P&L / session-day boundary ────────────────────────────────────────

    def _session_date_for(self, ts: _dt.datetime) -> _dt.date:
        """
        Trading-day bucket for a candle timestamp. A trading day runs from
        market open (09:15) to market open the next calendar day, so a
        candle at e.g. 09:10 (pre-open, shouldn't normally occur for cash
        index options but kept defensive) is treated as still belonging to
        the PREVIOUS session day.
        """
        market_open = ts.replace(hour=9, minute=15, second=0, microsecond=0)
        return ts.date() if ts >= market_open else (ts.date() - _dt.timedelta(days=1))

    def _maybe_roll_session_day(self, candle: Candle):
        """
        Detect crossing a new trading day's 09:15 market-open boundary and,
        if so, reset the daily P&L bookkeeping:
          - BACKTEST: daily_pnl resets to 0 (no force-exit semantics apply
            to backtest at all — see _pnl_limit_hit()).
          - LIVE/PAPER: daily_pnl resets to the mark-to-market UNREALIZED
            P&L of any still-open position(s) as of this candle (carrying
            forward exposure from an overnight position, per spec), and any
            stuck FORCE_EXIT state from a prior day is cleared back to a
            normal flat/positioned state so trading can resume.
        Requires candle.timestamp to be set; if it's None (e.g. an older
        caller that doesn't supply timestamps) this is a silent no-op and
        legacy single-day behavior is preserved.
        """
        if candle.timestamp is None:
            return
        today = self._session_date_for(candle.timestamp)
        if self._last_session_date is None:
            self._last_session_date = today
            return
        if today == self._last_session_date:
            return

        # New trading day boundary crossed
        self._last_session_date = today
        s = self.state

        if s.mode == "BACKTEST":
            s.daily_pnl = 0.0
            log.info(f"=== NEW SESSION DAY {today} (BACKTEST) — daily_pnl reset to 0 ===")
            return

        # LIVE / PAPER
        unrealized = 0.0
        for pos in (s.call_pos, s.put_pos):
            if pos is None:
                continue
            try:
                ltp = self.client.get_position_ltp(pos)
            except Exception as e:
                log.error(f"get_position_ltp failed during day-roll for {pos.side}: {e}")
                ltp = pos.entry_price  # fail safe: assume no move rather than crash
            unrealized += (ltp - pos.entry_price) * pos.quantity

        s.daily_pnl = unrealized
        if s.fsm == FSMState.FORCE_EXIT:
            self._sync_fsm(allow_unstick=True)
        log.info(f"=== NEW SESSION DAY {today} ({s.mode}) — daily_pnl seeded to "
                 f"unrealized Rs{unrealized:,.2f} from overnight position(s); "
                 f"FSM={s.fsm.name} ===")
        tg.alert_new_session_day(s.index_key, today, unrealized)

    # ── Main candle close dispatch ────────────────────────────────────────────

    def on_candle_close(self, candle: Candle):
        s     = self.state
        self._maybe_roll_session_day(candle)
        s.last_candle = candle
        close = candle.close
        log.info(f"== CANDLE O={candle.open:.2f} H={candle.high:.2f} "
                 f"L={candle.low:.2f} C={close:.2f} [{s.fsm.name}] ==")

        if self._pnl_limit_hit():
            log.warning("DAY P&L LIMIT -> force-close all")
            pnl = self.state.daily_pnl
            limit = "PROFIT" if pnl >= config.DAILY_PROFIT_TARGET else "LOSS"
            tg.alert_pnl_limit(pnl, limit)
            self._force_exit("PNL_LIMIT")
            return

        fsm = s.fsm
        if   fsm == FSMState.NO_POSITION: pass
        elif fsm == FSMState.CALL_ONLY:
            self._proc_call(candle)
            self._check_put_trigger(candle)
        elif fsm == FSMState.PUT_ONLY:
            self._proc_put(candle)
            self._check_call_trigger(candle)
        elif fsm == FSMState.BOTH:
            self._proc_call(candle)
            self._proc_put(candle)

        self._sync_fsm()
        log.info(s.summary())

    # ── Manual commands ───────────────────────────────────────────────────────

    def manual_buy_call(self, spot: float):
        if self.state.has_call():
            log.warning("CALL exists — duplicate blocked"); return
        self._enter_call(spot, "MANUAL")

    def manual_buy_put(self, spot: float):
        if self.state.has_put():
            log.warning("PUT exists — duplicate blocked"); return
        self._enter_put(spot, "MANUAL")

    def manual_force_sell(self):
        log.warning("MANUAL FORCE SELL")
        self._force_exit("MANUAL")

    def manual_set_pnl(self, value: float):
        old = self.state.daily_pnl
        self.state.daily_pnl = value
        log.info(f"PnL overridden: Rs{old:,.0f} -> Rs{value:,.0f}")

    def add_sr(self, level: float, tol: float | None = None):
        sr = SRLevel(level, tol)
        if sr not in self.state.sr_levels:
            self.state.sr_levels.append(sr)
            self.state.sr_levels.sort()
            log.info(f"SR ADDED: {sr}")
            self._reeval()

    def delete_sr(self, level: float):
        before = len(self.state.sr_levels)
        self.state.sr_levels = [s for s in self.state.sr_levels if s.level != level]
        if len(self.state.sr_levels) < before:
            log.info(f"SR DELETED: {level}"); self._reeval()

    def modify_sr_tol(self, level: float, tol: float):
        for sr in self.state.sr_levels:
            if sr.level == level:
                sr.tolerance = tol
                log.info(f"SR {level} tol -> {tol}"); self._reeval(); return

    # ── CALL processing ───────────────────────────────────────────────────────

    def _proc_call(self, candle: Candle):
        s = self.state; pos = s.call_pos
        if pos is None: return
        close = candle.close

        if pos.candle_filter.active:
            self._resolve_call_filter(candle, pos); return

        if pos.sl_active and pos.sl_level is not None:
            if call_sl_hit(close, pos.sl_level):
                if is_red_hanging_man(candle):
                    log.info("RED HANGING MAN near CALL SL — wait 1 candle")
                    tg.alert_candle_filter("RED_HANGING_MAN", "CALL", close)
                    pos.candle_filter = CandleFilterState(True, "HANGMAN", close); return
                self._call_sl_hit(close, pos); return

        if not pos.sl_active and pos.own_resistance is not None:
            if resistance_broken(close, pos.own_resistance):
                pos.sl_level  = call_sl(pos.own_resistance)
                pos.sl_active = True
                log.info(f"CALL SL ACTIVATED R={pos.own_resistance.level} SL={pos.sl_level:.2f}")
                tg.alert_sl_activated("CALL", pos.sl_level, close, self.state.index_key)
                old = pos.own_resistance
                pos.own_support    = old
                pos.own_resistance = next_res_above(old, close, s.sr_levels)
            return

        if pos.sl_active and pos.own_resistance is not None:
            if resistance_broken(close, pos.own_resistance):
                new_sl = call_sl(pos.own_resistance)
                if new_sl > pos.sl_level:
                    log.info(f"CALL SL TRAIL UP {pos.sl_level:.2f} -> {new_sl:.2f}")
                    pos.sl_level = new_sl
                old = pos.own_resistance
                pos.own_support    = old
                pos.own_resistance = next_res_above(old, close, s.sr_levels)

    # ── PUT processing ────────────────────────────────────────────────────────

    def _proc_put(self, candle: Candle):
        s = self.state; pos = s.put_pos
        if pos is None: return
        close = candle.close

        if pos.candle_filter.active:
            self._resolve_put_filter(candle, pos); return

        if pos.sl_active and pos.sl_level is not None:
            if put_sl_hit(close, pos.sl_level):
                if is_inverted_green_hammer(candle):
                    log.info("INV GREEN HAMMER near PUT SL — wait 1 candle")
                    tg.alert_candle_filter("INV_GREEN_HAMMER", "PUT", close)
                    pos.candle_filter = CandleFilterState(True, "HAMMER", close); return
                self._put_sl_hit(close, pos); return

        if not pos.sl_active and pos.own_support is not None:
            if support_broken(close, pos.own_support):
                pos.sl_level  = put_sl(pos.own_support)
                pos.sl_active = True
                log.info(f"PUT SL ACTIVATED S={pos.own_support.level} SL={pos.sl_level:.2f}")
                tg.alert_sl_activated("PUT", pos.sl_level, close, self.state.index_key)
                old = pos.own_support
                pos.own_resistance = old
                pos.own_support    = next_sup_below(old, close, s.sr_levels)
            return

        if pos.sl_active and pos.own_support is not None:
            if support_broken(close, pos.own_support):
                new_sl = put_sl(pos.own_support)
                if new_sl < pos.sl_level:
                    log.info(f"PUT SL TRAIL DOWN {pos.sl_level:.2f} -> {new_sl:.2f}")
                    pos.sl_level = new_sl
                old = pos.own_support
                pos.own_resistance = old
                pos.own_support    = next_sup_below(old, close, s.sr_levels)

    # ── Candle filters ────────────────────────────────────────────────────────

    def _resolve_call_filter(self, candle: Candle, pos: Position):
        close = candle.close; trig = pos.candle_filter.trigger_close
        pos.candle_filter = CandleFilterState()
        if close < trig:
            log.info(f"HANGMAN confirmed ({close}<{trig}) -> CALL SL exec")
            self._call_sl_hit(close, pos)
        else:
            log.info(f"HANGMAN NOT confirmed ({close}>={trig}) -> SL holds")

    def _resolve_put_filter(self, candle: Candle, pos: Position):
        close = candle.close; trig = pos.candle_filter.trigger_close
        pos.candle_filter = CandleFilterState()
        if close > trig:
            log.info(f"HAMMER confirmed ({close}>{trig}) -> PUT SL exec")
            self._put_sl_hit(close, pos)
        else:
            log.info(f"HAMMER NOT confirmed ({close}<={trig}) -> SL holds")

    # ── Case-II opposite-side triggers (while exactly one leg open) ─────────
    #
    # PUT while CALL_ONLY (Case-II): support below CALL's own resistance
    # broken downward at 1.0x tolerance — NOT any level in the list, and NOT
    # R-1.2x (that applies only after CALL SL in _call_sl_hit).
    #
    # CALL while PUT_ONLY: PUT's own resistance + 1.2x tolerance (bounce), or
    # Case-II mirror — resistance above PUT support broken at 1.0x tolerance.
    #
    # Does NOT apply when flat or when both sides are open.

    def _check_put_trigger(self, candle: Candle):
        s = self.state
        if s.has_put():
            return
        pos = s.call_pos
        if pos is None or pos.own_resistance is None:
            return
        close = candle.close
        if not put_opposite_trigger(close, pos.own_resistance, s.sr_levels):
            return
        log.info(f"OPPOSITE-SIDE PUT (Case-II): close={close:.2f} broke support "
                 f"below CALL R={pos.own_resistance.level}")
        self._enter_put(close, "OPPOSITE_SIDE_BREAK")

    def _check_call_trigger(self, candle: Candle):
        s = self.state
        if s.has_call():
            return
        pos = s.put_pos
        if pos is None or pos.own_resistance is None or pos.own_support is None:
            return
        close = candle.close
        if not call_opposite_trigger(close, pos.own_resistance, pos.own_support,
                                     s.sr_levels):
            return
        thr = (pos.own_resistance.level
               + config.ENTRY_FILTER_MULT
               * pos.own_resistance.tol(config.TOLERANCE))
        kind = "bounce" if close >= thr else "resistance-break"
        log.info(f"OPPOSITE-SIDE CALL ({kind}): close={close:.2f} "
                 f"vs PUT R={pos.own_resistance.level}")
        self._enter_call(close, "OPPOSITE_SIDE_BREAK")

    # ── SL execution + auto reverse ───────────────────────────────────────────

    def _call_sl_hit(self, close: float, pos: Position):
        # own_support at exit is the resistance that was broken (e.g. 24000)
        ref = pos.own_support
        self._exit_call(close, "SL_HIT")
        if self.state.has_put(): return
        if ref and put_entry_after_call_exit(close, ref):
            self._enter_put(close, "AUTO_AFTER_CALL_SL")

    def _put_sl_hit(self, close: float, pos: Position):
        # Use PUT's tracked resistance at exit (e.g. 23956 after ladder trail).
        # CALL enters when close >= that R + 1.2x tol — same rule as opposite-side
        # CALL while PUT is open; NOT the frozen entry_resistance (24000).
        ref = pos.own_resistance
        self._exit_put(close, "SL_HIT")
        if self.state.has_call():
            return
        if ref and call_entry_after_put_exit(close, ref):
            self._enter_call(close, "AUTO_AFTER_PUT_SL")

    # ── Entry / exit ──────────────────────────────────────────────────────────

    def _enter_call(self, spot: float, reason: str = ""):
        s = self.state
        if s.has_call(): log.warning("Dup CALL blocked"); return
        strike = select_call_strike(spot, self._step)
        sec_id = self.client.get_option_security_id(strike, "CE", config.OPTION_EXPIRY)
        if not self._funds_ok(sec_id, strike, "CALL"):
            return
        pos    = self.client.buy_option(sec_id, strike, "CALL", spot)
        pos.own_resistance = assign_call_res(spot, s.sr_levels)
        pos.own_support    = nearest_support(spot, s.sr_levels)
        pos.entry_resistance = pos.own_resistance
        pos.entry_support    = pos.own_support
        s.call_pos = pos
        self._sync_fsm()
        tg.alert_buy("CALL", strike, pos.entry_price, spot,
                     s.index_key, pos.quantity, reason, mode=s.mode)
        log.info(f"CALL ENTERED k={strike} reason={reason} "
                 f"R={pos.own_resistance} S={pos.own_support}")

    def _enter_put(self, spot: float, reason: str = ""):
        s = self.state
        if s.has_put(): log.warning("Dup PUT blocked"); return
        strike = select_put_strike(spot, self._step)
        sec_id = self.client.get_option_security_id(strike, "PE", config.OPTION_EXPIRY)
        if not self._funds_ok(sec_id, strike, "PUT"):
            return
        pos    = self.client.buy_option(sec_id, strike, "PUT", spot)
        pos.own_support    = assign_put_sup(spot, s.sr_levels)
        pos.own_resistance = nearest_resistance(spot, s.sr_levels)
        pos.entry_support    = pos.own_support
        pos.entry_resistance = pos.own_resistance
        s.put_pos = pos
        self._sync_fsm()
        tg.alert_buy("PUT", strike, pos.entry_price, spot,
                     s.index_key, pos.quantity, reason, mode=s.mode)
        log.info(f"PUT ENTERED k={strike} reason={reason} "
                 f"S={pos.own_support} R={pos.own_resistance}")

    def _exit_call(self, spot: float, reason: str = ""):
        s = self.state; pos = s.call_pos
        if pos is None: return
        exit_px      = self.client.sell_option(pos, reason=reason)
        pnl          = (exit_px - pos.entry_price) * pos.quantity
        s.daily_pnl += pnl
        tg.alert_sell("CALL", pos.strike, exit_px, spot, pnl, s.index_key,
                      pos.quantity, reason, mode=s.mode)
        s.call_pos = None
        self._sync_fsm()
        log.info(f"CALL CLOSED pnl=Rs{pnl:,.2f} total=Rs{s.daily_pnl:,.2f}")

    def _exit_put(self, spot: float, reason: str = ""):
        s = self.state; pos = s.put_pos
        if pos is None: return
        exit_px      = self.client.sell_option(pos, reason=reason)
        pnl          = (exit_px - pos.entry_price) * pos.quantity
        s.daily_pnl += pnl
        tg.alert_sell("PUT", pos.strike, exit_px, spot, pnl, s.index_key,
                      pos.quantity, reason, mode=s.mode)
        s.put_pos = None
        self._sync_fsm()
        log.info(f"PUT CLOSED pnl=Rs{pnl:,.2f} total=Rs{s.daily_pnl:,.2f}")

    def _force_exit(self, reason: str = ""):
        extra = self.client.force_sell_all(self.state)
        self.state.daily_pnl += extra
        self.state.fsm        = FSMState.FORCE_EXIT
        from core.session import clear_session
        clear_session()
        log.warning(f"FORCE EXIT reason={reason} extra=Rs{extra:,.2f} "
                    f"total=Rs{self.state.daily_pnl:,.2f}")

    # ── FSM / S/R helpers ─────────────────────────────────────────────────────

    def _sync_fsm(self, allow_unstick: bool = False):
        s = self.state
        if s.fsm == FSMState.FORCE_EXIT and not allow_unstick:
            return
        if s.has_call() and s.has_put(): s.fsm = FSMState.BOTH
        elif s.has_call():               s.fsm = FSMState.CALL_ONLY
        elif s.has_put():                s.fsm = FSMState.PUT_ONLY
        else:                            s.fsm = FSMState.NO_POSITION

    def _reeval(self):
        s = self.state
        close = s.last_candle.close if s.last_candle else None
        if close is None: return
        if s.call_pos:
            p = s.call_pos
            if not p.sl_active:
                p.own_resistance = assign_call_res(close, s.sr_levels)
                p.own_support    = nearest_support(close, s.sr_levels)
            else:
                p.own_resistance = nearest_resistance(close, s.sr_levels)
        if s.put_pos:
            p = s.put_pos
            if not p.sl_active:
                p.own_support    = assign_put_sup(close, s.sr_levels)
                p.own_resistance = nearest_resistance(close, s.sr_levels)
            else:
                p.own_support = nearest_support(close, s.sr_levels)

    def _pnl_limit_hit(self) -> bool:
        if not config.PNL_LIMIT_ENABLED_FOR_MODE.get(self.state.mode, True):
            return False
        p = self.state.daily_pnl
        return p >= config.DAILY_PROFIT_TARGET or p <= config.DAILY_LOSS_LIMIT

    def _funds_ok(self, security_id: str, strike: float, side: str) -> bool:
        checker = getattr(self.client, "check_funds_before_buy", None)
        if checker is None:
            return True
        ok, avail, need = checker(security_id, strike, side)
        if ok:
            return True
        log.warning(f"SKIP {side} buy k={strike} — insufficient funds "
                    f"(avail=Rs{avail:,.2f}, need=Rs{need:,.2f})")
        return False
