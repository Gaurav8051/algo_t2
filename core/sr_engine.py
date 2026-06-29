"""core/sr_engine.py — All S/R math. Pure Python, zero API dependency."""
from __future__ import annotations
from typing import Optional
import config
from core.states import SRLevel, Candle


def nearest_support(spot: float, levels: list[SRLevel]) -> Optional[SRLevel]:
    b = [s for s in levels if s.level < spot]
    return max(b, key=lambda x: x.level) if b else None

def nearest_resistance(spot: float, levels: list[SRLevel]) -> Optional[SRLevel]:
    a = [s for s in levels if s.level > spot]
    return min(a, key=lambda x: x.level) if a else None

def nearest_resistance_above_floor(spot: float, floor: float,
                                 levels: list[SRLevel]) -> Optional[SRLevel]:
    """Nearest R above spot that is also strictly above floor (CALL entry spot)."""
    a = [s for s in levels if s.level > spot and s.level > floor]
    return min(a, key=lambda x: x.level) if a else None

def nearest_support_below_ceiling(spot: float, ceiling: float,
                                  levels: list[SRLevel]) -> Optional[SRLevel]:
    """Nearest S below spot that is also strictly below ceiling (PUT entry spot)."""
    b = [s for s in levels if s.level < spot and s.level < ceiling]
    return max(b, key=lambda x: x.level) if b else None

def next_res_above(cur: SRLevel, spot: float, levels: list[SRLevel]) -> Optional[SRLevel]:
    a = [s for s in levels if s.level > spot and s != cur]
    return min(a, key=lambda x: x.level) if a else None

def next_res_above_entry(cur: SRLevel, spot: float, entry_spot: float,
                         levels: list[SRLevel]) -> Optional[SRLevel]:
    """Next R above spot for CALL SL ladder — never below entry spot."""
    a = [s for s in levels if s.level > spot and s.level > entry_spot and s != cur]
    return min(a, key=lambda x: x.level) if a else None

def next_sup_below(cur: SRLevel, spot: float, levels: list[SRLevel]) -> Optional[SRLevel]:
    b = [s for s in levels if s.level < spot and s != cur]
    return max(b, key=lambda x: x.level) if b else None

def next_sup_below_entry(cur: SRLevel, spot: float, entry_spot: float,
                         levels: list[SRLevel]) -> Optional[SRLevel]:
    """Next S below spot for PUT SL ladder — never above entry spot."""
    b = [s for s in levels if s.level < spot and s.level < entry_spot and s != cur]
    return max(b, key=lambda x: x.level) if b else None

def in_near_res_zone(spot: float, res: SRLevel) -> bool:
    lo = res.level - config.NEAR_SR_MULT * res.tol(config.TOLERANCE)
    return lo < spot < res.level

def in_near_sup_zone(spot: float, sup: SRLevel) -> bool:
    hi = sup.level + config.NEAR_SR_MULT * sup.tol(config.TOLERANCE)
    return sup.level < spot < hi

def assign_call_res(entry_spot: float, levels: list[SRLevel]) -> Optional[SRLevel]:
    res = nearest_resistance(entry_spot, levels)
    if res and in_near_res_zone(entry_spot, res):
        res = next_res_above(res, entry_spot, levels)
    return res

def call_sl_resistance_ref(entry_spot: float, levels: list[SRLevel]) -> Optional[SRLevel]:
    """CALL SL ladder anchor — always from entry spot, not drifting with price."""
    return assign_call_res(entry_spot, levels)

def assign_put_sup(entry_spot: float, levels: list[SRLevel]) -> Optional[SRLevel]:
    sup = nearest_support(entry_spot, levels)
    if sup and in_near_sup_zone(entry_spot, sup):
        sup = next_sup_below(sup, entry_spot, levels)
    return sup

def put_sl_support_ref(entry_spot: float, levels: list[SRLevel]) -> Optional[SRLevel]:
    """PUT SL ladder anchor — always from entry spot, not drifting with price."""
    return assign_put_sup(entry_spot, levels)

def call_sl_resistance_valid(res: SRLevel, entry_spot: float) -> bool:
    """CALL auto-SL may only arm/trail on resistance strictly above entry."""
    return res.level > entry_spot

def put_sl_support_valid(sup: SRLevel, entry_spot: float) -> bool:
    """PUT auto-SL may only arm/trail on support strictly below entry."""
    return sup.level < entry_spot

def resistance_broken(close: float, res: SRLevel) -> bool: return close > res.level
def support_broken(close: float, sup: SRLevel)    -> bool: return close < sup.level

def call_sl(res: SRLevel) -> float: return res.level - res.tol(config.TOLERANCE)
def put_sl(sup: SRLevel)  -> float: return sup.level + sup.tol(config.TOLERANCE)

def call_sl_hit(close: float, sl: float) -> bool: return close < sl
def put_sl_hit(close: float, sl: float)  -> bool: return close > sl

def put_entry_after_call_exit(close: float, last_res: SRLevel) -> bool:
    return close <= last_res.level - config.ENTRY_FILTER_MULT * last_res.tol(config.TOLERANCE)

def call_entry_after_put_exit(close: float, resistance: SRLevel) -> bool:
    """CALL after PUT SL or bounce: close >= resistance + 1.2x tolerance."""
    return call_reentry_after_sl(close, resistance)

def put_reentry_after_sl(close: float, support: SRLevel) -> bool:
    """PUT re-entry after PUT SL: close < support - 1.2x tolerance."""
    return close < support.level - config.ENTRY_FILTER_MULT * support.tol(config.TOLERANCE)

def call_reentry_after_sl(close: float, resistance: SRLevel) -> bool:
    """CALL re-entry after CALL SL: close >= resistance + 1.2x tolerance."""
    return close >= resistance.level + config.ENTRY_FILTER_MULT * resistance.tol(config.TOLERANCE)

def advance_put_reentry_ladder(close: float, ref: SRLevel,
                               levels: list[SRLevel]) -> SRLevel:
    """
    While waiting for PUT re-entry, each broken resistance above the current
    support reference becomes the new support ref (same ladder rule as an
    open PUT leg trailing upward).  E.g. after SL at S=23335, close > 23372
    advances ref to 23372; re-entry gate becomes 23372 - 1.2x tol.
    """
    while True:
        above = [r for r in levels if r.level > ref.level]
        if not above:
            break
        nxt = min(above, key=lambda x: x.level)
        if close > nxt.level:
            ref = nxt
        else:
            break
    return ref

def advance_call_reentry_ladder(close: float, ref: SRLevel,
                                levels: list[SRLevel]) -> SRLevel:
    """
    Mirror of advance_put_reentry_ladder: while waiting for CALL re-entry,
    each broken support below the current resistance reference becomes the
    new resistance ref.  Re-entry gate = ref + 1.2x tol.
    """
    while True:
        below = [r for r in levels if r.level < ref.level]
        if not below:
            break
        nxt = max(below, key=lambda x: x.level)
        if close < nxt.level:
            ref = nxt
        else:
            break
    return ref

def put_opposite_trigger(close: float, call_resistance: SRLevel,
                         sr_levels: list[SRLevel]) -> bool:
    """
    PUT entry while CALL_ONLY (Case-II): support below CALL's tracked
    resistance broken at 1.2x tolerance (e.g. 23900 - 1.2x11 = 23886.8).
    R-1.2x after CALL SL uses put_entry_after_call_exit() separately.
    """
    cap = call_resistance.level
    mult = config.ENTRY_FILTER_MULT
    for sup in sr_levels:
        if sup.level >= cap:
            continue
        tol = mult * sup.tol(config.TOLERANCE)
        if sup.level > close and close < sup.level - tol:
            return True
    return False

def call_opposite_trigger(close: float, put_resistance: SRLevel,
                          put_support: SRLevel,
                          sr_levels: list[SRLevel]) -> bool:
    """
    CALL entry while PUT_ONLY: resistance confirmed at 1.2x tolerance —
    PUT's own R + 1.2x (e.g. 23956 + 13.2 = 23969.2), or any resistance
    above PUT support broken at the same 1.2x threshold (Case-II mirror).
    """
    mult = config.ENTRY_FILTER_MULT
    if close >= (put_resistance.level
                 + mult * put_resistance.tol(config.TOLERANCE)):
        return True
    floor = put_support.level
    for res in sr_levels:
        if res.level <= floor:
            continue
        tol = mult * res.tol(config.TOLERANCE)
        if res.level < close and close > res.level + tol:
            return True
    return False

def put_trigger_while_call_alive(close: float, sup: SRLevel) -> bool:
    """
    DEPRECATED — no longer called from algo_engine.py.
    Bug: used 1.0x tolerance instead of the spec's 1.2x (ENTRY_FILTER_MULT),
    AND was previously called with a stale, frozen-at-entry support level
    rather than one recomputed against current price. See
    CHANGES_FROM_ORIGINAL.md for the full explanation. Kept here only for
    reference; algo_engine.py now uses put_entry_after_call_exit() with a
    LIVE nearest_support() lookup instead.
    """
    return close < sup.level - sup.tol(config.TOLERANCE)

def call_trigger_while_put_alive(close: float, res: SRLevel) -> bool:
    """DEPRECATED — see put_trigger_while_call_alive() docstring above; same bug, mirrored."""
    return close > res.level + res.tol(config.TOLERANCE)

def is_inverted_green_hammer(c: Candle) -> bool:
    rng = c.body_range()
    return rng > config.TOLERANCE and c.is_green() and (c.close - c.low) / rng < 0.20

def is_red_hanging_man(c: Candle) -> bool:
    rng = c.body_range()
    return rng > config.TOLERANCE and c.is_red() and (c.high - c.close) / rng < 0.20

def select_call_strike(spot: float, step: int) -> float:
    return float((int(spot // step) + 1 + config.OTM_SKIP) * step)

def select_put_strike(spot: float, step: int) -> float:
    return float((int(spot // step) - config.OTM_SKIP) * step)
