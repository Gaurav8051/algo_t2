"""core/candle_builder.py — 1-min OHLC from WebSocket ticks."""
from __future__ import annotations
import time, logging, datetime
from typing import Optional, Callable
from core.states import Candle

log = logging.getLogger("algo.candle")


class CandleBuilder:
    def __init__(self, interval_sec: int = 60,
                 on_candle_close: Optional[Callable[[Candle], None]] = None):
        self.interval = interval_sec
        self.on_candle_close = on_candle_close
        self._o = self._h = self._l = self._c = None
        self._min = None

    def on_tick(self, price: float):
        now = time.time()
        m   = int(now // self.interval)
        if self._min is None:
            self._start(price, m); return
        if m != self._min:
            self._emit(); self._start(price, m)
        else:
            if price > self._h: self._h = price
            if price < self._l: self._l = price
            self._c = price

    def _start(self, p, m):
        self._o = self._h = self._l = self._c = p
        self._min = m

    def _emit(self):
        if self._o is None: return
        ts = datetime.datetime.fromtimestamp(self._min * self.interval)
        c = Candle(open=self._o, high=self._h, low=self._l, close=self._c, timestamp=ts)
        log.info(f"CANDLE  O={c.open:.2f} H={c.high:.2f} L={c.low:.2f} C={c.close:.2f}")
        if self.on_candle_close:
            try: self.on_candle_close(c)
            except Exception as e: log.error(f"candle_close error: {e}", exc_info=True)
