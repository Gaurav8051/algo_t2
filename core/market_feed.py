"""
core/market_feed.py — Dhan MarketFeed WebSocket (v2.2.0).

Correct pattern:
    feed = MarketFeed(ctx, instruments, "v2")
    feed.run_forever()   # ONCE before the loop
    while True:
        data = feed.get_data()

MarketFeed segment constants:
    MarketFeed.IDX     = NSE/BSE indices (Nifty 50, Sensex)
    MarketFeed.NSE_FNO = NSE F&O
    MarketFeed.BSE_FNO = BSE F&O
    MarketFeed.MCX     = MCX commodities (Crude Oil)

No static IP required for WebSocket data feeds.
"""
from __future__ import annotations
import logging, threading, time
from typing import Callable, Optional
from dhanhq import DhanContext, MarketFeed

import config
from core.candle_builder import CandleBuilder
from core.algo_engine import AlgoEngine

log = logging.getLogger("algo.feed")


def _get_feed_segment(index_key: str):
    """Return the correct MarketFeed segment constant for the index."""
    seg_name = config.INDEX_CONFIG[index_key]["feed_seg"]
    return getattr(MarketFeed, seg_name)


def start_feed(ctx: DhanContext, engine: AlgoEngine,
               spot_setter: Optional[Callable[[float], None]] = None) -> threading.Thread:
    t = threading.Thread(target=_run, args=(ctx, engine, spot_setter),
                         daemon=True, name="MarketFeed")
    t.start()
    log.info("MarketFeed thread started.")
    return t


def _run(ctx, engine, spot_setter):
    idx      = config.INDEX_CONFIG[config.ACTIVE_INDEX]
    seg      = _get_feed_segment(config.ACTIVE_INDEX)
    builder  = CandleBuilder(config.CANDLE_INTERVAL_SEC, engine.on_candle_close)
    instruments = [(seg, idx["security_id"], MarketFeed.Ticker)]

    while True:
        feed = None
        try:
            log.info(f"Connecting MarketFeed: {config.ACTIVE_INDEX} "
                     f"seg={idx['feed_seg']} sid={idx['security_id']}")
            feed = MarketFeed(ctx, instruments, "v2")
            feed.run_forever()
            log.info("MarketFeed connected.")
            while True:
                data = feed.get_data()
                if data:
                    ltp = data.get("LTP") or data.get("last_price") or data.get("ltp")
                    if ltp:
                        price = float(ltp)
                        if price > 0:
                            if spot_setter: spot_setter(price)
                            builder.on_tick(price)
                time.sleep(0.01)
        except Exception as e:
            log.error(f"MarketFeed error: {e} — reconnect in 5s", exc_info=True)
            if feed:
                try: feed.close_connection()
                except Exception: pass
            time.sleep(5)
