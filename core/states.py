"""core/states.py — All dataclasses. No external dependencies."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Any


class FSMState(Enum):
    NO_POSITION = auto()
    CALL_ONLY   = auto()
    PUT_ONLY    = auto()
    BOTH        = auto()
    FORCE_EXIT  = auto()


@dataclass
class SRLevel:
    level:     float
    tolerance: Optional[float] = None

    def tol(self, default: float) -> float:
        return self.tolerance if self.tolerance is not None else default

    def __lt__(self, o: "SRLevel") -> bool: return self.level < o.level
    def __eq__(self, o: object) -> bool:
        return isinstance(o, SRLevel) and self.level == o.level
    def __hash__(self) -> int: return hash(self.level)
    def __repr__(self) -> str:
        t = f"T={self.tolerance}" if self.tolerance is not None else "T=global"
        return f"SR({self.level},{t})"


@dataclass
class Candle:
    open: float; high: float; low: float; close: float
    timestamp: Optional[Any] = None  # datetime of candle close; None in older callers (safe default)

    def body_range(self) -> float: return self.high - self.low
    def is_green(self) -> bool:    return self.close >= self.open
    def is_red(self)   -> bool:    return self.close < self.open


@dataclass
class CandleFilterState:
    active:        bool  = False
    filter_type:   str   = ""
    trigger_close: float = 0.0


@dataclass
class Position:
    side:         str
    strike:       float
    security_id:  str
    entry_price:  float
    entry_spot:   float
    order_id:     str
    quantity:     int
    own_support:    Optional[SRLevel]   = None
    own_resistance: Optional[SRLevel]  = None
    entry_support:    Optional[SRLevel] = None  # frozen at entry for SL auto-reversal
    entry_resistance: Optional[SRLevel] = None
    sl_level:       Optional[float]     = None
    sl_active:      bool                = False
    candle_filter:  CandleFilterState   = field(default_factory=CandleFilterState)

    @property
    def is_call(self) -> bool: return self.side == "CALL"
    @property
    def is_put(self)  -> bool: return self.side == "PUT"

    def sl_summary(self) -> str:
        if not self.sl_active: return "SL=WAITING"
        return f"SL=ACTIVE@{self.sl_level:.1f}"

    def to_dict(self) -> dict:
        """Serialise for session persistence."""
        return {
            "side": self.side, "strike": self.strike,
            "security_id": self.security_id,
            "entry_price": self.entry_price, "entry_spot": self.entry_spot,
            "order_id": self.order_id, "quantity": self.quantity,
            "own_support":    [self.own_support.level, self.own_support.tolerance]
                              if self.own_support else None,
            "own_resistance": [self.own_resistance.level, self.own_resistance.tolerance]
                              if self.own_resistance else None,
            "entry_support":    [self.entry_support.level, self.entry_support.tolerance]
                                if self.entry_support else None,
            "entry_resistance": [self.entry_resistance.level, self.entry_resistance.tolerance]
                                if self.entry_resistance else None,
            "sl_level": self.sl_level, "sl_active": self.sl_active,
        }

    @staticmethod
    def from_dict(d: dict) -> "Position":
        pos = Position(
            side=d["side"], strike=d["strike"], security_id=d["security_id"],
            entry_price=d["entry_price"], entry_spot=d["entry_spot"],
            order_id=d["order_id"], quantity=d["quantity"],
        )
        if d.get("own_support"):
            pos.own_support = SRLevel(*d["own_support"])
        if d.get("own_resistance"):
            pos.own_resistance = SRLevel(*d["own_resistance"])
        if d.get("entry_support"):
            pos.entry_support = SRLevel(*d["entry_support"])
        if d.get("entry_resistance"):
            pos.entry_resistance = SRLevel(*d["entry_resistance"])
        pos.sl_level  = d.get("sl_level")
        pos.sl_active = d.get("sl_active", False)
        return pos


@dataclass
class AlgoState:
    fsm:      FSMState           = FSMState.NO_POSITION
    call_pos: Optional[Position] = None
    put_pos:  Optional[Position] = None
    sr_levels: list[SRLevel]     = field(default_factory=list)
    daily_pnl: float             = 0.0
    last_candle: Optional[Candle] = None
    index_key: str               = "NIFTY50"
    mode:      str               = "PAPER"   # "PAPER" | "LIVE" | "BACKTEST" — used for alert labels only
    last_session_date: Optional[Any] = None  # date of the last candle processed; drives 9:15 day-reset
    # After SL exit: same-side re-entry gated at exit level +/- 1.2x tol (blocks Case-II spam)
    pending_put_reentry:  Optional[SRLevel] = None   # PUT re-entry needs S - 1.2x
    pending_call_reentry: Optional[SRLevel] = None   # CALL re-entry needs R + 1.2x
    call_disabled: bool = False   # strategy skips CALL auto logic + manual CALL blocked
    put_disabled:  bool = False   # strategy skips PUT auto logic + manual PUT blocked

    def has_call(self) -> bool:  return self.call_pos is not None
    def has_put(self)  -> bool:  return self.put_pos  is not None
    def has_both(self) -> bool:  return self.has_call() and self.has_put()
    def sorted_levels(self) -> list[float]:
        return sorted(sr.level for sr in self.sr_levels)

    def summary(self) -> str:
        close = f"{self.last_candle.close:.2f}" if self.last_candle else "?"
        lines = [
            "=" * 62,
            f"  Index       : {self.index_key}",
            f"  FSM         : {self.fsm.name}",
            f"  Last Candle : {close}",
            f"  Realised    : Rs {self.daily_pnl:,.2f}",
        ]
        for pos in [self.call_pos, self.put_pos]:
            if pos:
                lines += [
                    f"  {pos.side:<4}        : strike={pos.strike}"
                    f"  entry=Rs{pos.entry_price:.2f}  {pos.sl_summary()}",
                    f"               R={pos.own_resistance}  S={pos.own_support}",
                ]
                if pos.candle_filter.active:
                    lines.append(f"               [FILTER:{pos.candle_filter.filter_type}]")
        lines.append("=" * 62)
        return "\n".join(lines)
