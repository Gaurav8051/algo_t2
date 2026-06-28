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

def next_res_above(cur: SRLevel, spot: float, levels: list[SRLevel]) -> Optional[SRLevel]:
    a = [s for s in levels if s.level > spot and s != cur]
    return min(a, key=lambda x: x.level) if a else None

def next_sup_below(cur: SRLevel, spot: float, levels: list[SRLevel]) -> Optional[SRLevel]:
    b = [s for s in levels if s.level < spot and s != cur]
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

def assign_put_sup(entry_spot: float, levels: list[SRLevel]) -> Optional[SRLevel]:
    sup = nearest_support(entry_spot, levels)
    if sup and in_near_sup_zone(entry_spot, sup):
        sup = next_sup_below(sup, entry_spot, levels)
    return sup

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
    return close >= resistance.level + config.ENTRY_FILTER_MULT * resistance.tol(config.TOLERANCE)

def put_opposite_trigger(close: float, call_resistance: SRLevel,
                         sr_levels: list[SRLevel]) -> bool:
    """
    PUT entry while CALL_ONLY (Case-II): price falls through a support level
    that sits below the open CALL's tracked resistance, by 1.0x tolerance.
    Does NOT use R-1.2x (that rule applies only after CALL SL via
    put_entry_after_call_exit). Ignores levels at/above CALL resistance so
    e.g. 24000-11 cannot fire PUT while spot is still below R 23956.
    """
    cap = call_resistance.level
    for sup in sr_levels:
        if sup.level >= cap:
            continue
        if sup.level > close and close < sup.level - sup.tol(config.TOLERANCE):
            return True
    return False

def call_opposite_trigger(close: float, put_resistance: SRLevel,
                          put_support: SRLevel,
                          sr_levels: list[SRLevel]) -> bool:
    """
    CALL entry while PUT_ONLY:
      1) Bounce: close >= PUT's own resistance + 1.2x tolerance
      2) Case-II mirror: resistance above PUT support broken at 1.0x tol
    """
    if close >= (put_resistance.level
                 + config.ENTRY_FILTER_MULT * put_resistance.tol(config.TOLERANCE)):
        return True
    floor = put_support.level
    for res in sr_levels:
        if res.level <= floor:
            continue
        if res.level < close and close > res.level + res.tol(config.TOLERANCE):
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
