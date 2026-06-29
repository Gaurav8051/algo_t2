"""Shared command dispatcher for file-based and TCP dashboard control."""
from __future__ import annotations
import logging
from typing import Any

import config

log = logging.getLogger("algo.commands")


def execute_command(engine, state, client, mode: str, cmd: dict,
                    shutdown_cb=None) -> dict:
    """
    Run one dashboard command. Returns {"message": "..."} or {"error": "..."}.
    shutdown_cb: callable(reason) when q_paper requested.
    """
    action = cmd.get("action", "")
    spot = state.last_candle.close if state.last_candle else client.get_spot_ltp()

    try:
        if action == "buy_call":
            if state.has_call():
                return {"error": "CALL position already open — duplicate blocked"}
            if state.call_disabled:
                return {"error": "CALL side is DISABLED — enable first"}
            engine.manual_buy_call(spot)
            return {"message": "buy_call sent"}

        if action == "buy_put":
            if state.has_put():
                return {"error": "PUT position already open — duplicate blocked"}
            if state.put_disabled:
                return {"error": "PUT side is DISABLED — enable first"}
            engine.manual_buy_put(spot)
            return {"message": "buy_put sent"}

        if action == "force_sell":
            engine.manual_force_sell()
            return {"message": "force_sell all executed"}

        if action == "sell_call":
            if not state.has_call():
                return {"error": "No CALL position open"}
            engine.manual_sell_call()
            return {"message": "CALL sold"}

        if action == "sell_put":
            if not state.has_put():
                return {"error": "No PUT position open"}
            engine.manual_sell_put()
            return {"message": "PUT sold"}

        if action == "set_pnl":
            engine.manual_set_pnl(float(cmd["value"]))
            return {"message": f"PnL set to {cmd['value']}"}

        if action == "add_sr":
            engine.add_sr(float(cmd["level"]), cmd.get("tolerance"))
            return {"message": f"S/R {cmd['level']} added"}

        if action == "del_sr":
            engine.delete_sr(float(cmd["level"]))
            return {"message": f"S/R {cmd['level']} deleted"}

        if action == "set_tol":
            engine.modify_sr_tol(float(cmd["level"]), float(cmd["tolerance"]))
            return {"message": f"tol updated for {cmd['level']}"}

        if action == "set_global_tol":
            config.TOLERANCE = float(cmd["value"])
            return {"message": f"global tolerance = {config.TOLERANCE}"}

        if action == "set_profit":
            config.DAILY_PROFIT_TARGET = float(cmd["value"])
            return {"message": f"profit target = {config.DAILY_PROFIT_TARGET}"}

        if action == "set_loss":
            config.DAILY_LOSS_LIMIT = float(cmd["value"])
            return {"message": f"loss limit = {config.DAILY_LOSS_LIMIT}"}

        if action == "set_pnl_mode":
            m = cmd["value"].lower()
            if m not in ("none", "profit", "loss", "both"):
                return {"error": "mode must be none|profit|loss|both"}
            config.PNL_LIMIT_MODE = m
            return {"message": f"PnL limit mode = {m}"}

        if action == "set_expiry":
            config.OPTION_EXPIRY = cmd["value"]
            return {"message": f"expiry = {cmd['value']}"}

        if action == "set_lots":
            config.NUM_LOTS = int(cmd["value"])
            return {"message": f"lots = {config.NUM_LOTS}"}

        if action == "disable_call":
            engine.set_side_disabled(call=True)
            return {"message": "CALL side DISABLED"}

        if action == "disable_put":
            engine.set_side_disabled(put=True)
            return {"message": "PUT side DISABLED"}

        if action == "disable_both":
            engine.set_side_disabled(call=True, put=True)
            return {"message": "CALL and PUT DISABLED"}

        if action == "enable_call":
            engine.set_side_disabled(call=False)
            return {"message": "CALL side ENABLED"}

        if action == "enable_put":
            engine.set_side_disabled(put=False)
            return {"message": "PUT side ENABLED"}

        if action == "enable_all":
            engine.set_side_disabled(call=False, put=False)
            return {"message": "CALL and PUT ENABLED"}

        if action == "q_paper":
            if mode != "paper":
                return {"error": "Q-paper only valid in PAPER mode"}
            if shutdown_cb:
                shutdown_cb("Q_PAPER")
            return {"message": "Q-paper shutdown queued"}

        if action == "stop_algo":
            if state.has_call() or state.has_put():
                return {"error": "Close all positions first (sell all / sell call / sell put)"}
            if shutdown_cb:
                shutdown_cb("STOP")
            return {"message": "Algo stop queued — process will exit when flat"}

        return {"error": f"unknown action: {action}"}

    except Exception as e:
        log.error(f"Command error ({action}): {e}", exc_info=True)
        return {"error": str(e)}
