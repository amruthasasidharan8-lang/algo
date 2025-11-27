#!/usr/bin/env python3
# bot.py - BankNifty 9:30 Breakout bot (Advanced)
# Run under PM2: pm2 start bot.py --interpreter python3 --name banknifty_bot

import json
import time
import traceback
from pathlib import Path
import os
import logging
from datetime import datetime, timedelta
import requests

# ------------- CONFIG (REPLACE SENSITIVE VALUES ON SERVER) -------------
CLIENT_ID = "CGDSV5GE7E-100"
ACCESS_TOKEN = "CGDSV5GE7E-100:YOUR_JWT_TOKEN"   # <-- REPLACE with real JWT on server
TELEGRAM_BOT_TOKEN = "8428714129:AAERaYcX9fgLcQPWUwPP7z1C56EnvEf5jhQ"
TELEGRAM_CHAT_ID = "1597187434"

CONTROL_FILE = Path("control.json")
STATE_FILE = Path("state.json")
LOG_FILE = Path("bot.log")

# Defaults - can be overridden by control.json
DEFAULT_CONTROL = {
    "mode": "idle",
    "start_at": None,
    "stop_at": None,
    "params": {
        "premium_min": 35,
        "premium_max": 45,
        "qty": 1,
        "test_mode": True,
        "candle_high": 56000,
        "candle_low": 55800
    },
    "last_command_time": None,
    "last_command_by": None
}

# ------------- Logging Setup -------------
logging.basicConfig(filename=str(LOG_FILE), level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("banknifty_bot")

# ------------- Helpers -------------
def send_msg(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text})
    except Exception:
        logger.exception("Telegram send failed")

def load_json_safe(p: Path, default):
    try:
        if not p.exists():
            return default
        with p.open() as f:
            return json.load(f)
    except Exception:
        logger.exception("load_json_safe failed for %s", p)
        return default

def save_json_safe(p: Path, data):
    try:
        with p.open("w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        logger.exception("save_json_safe failed for %s", p)

def ensure_files():
    if not CONTROL_FILE.exists():
        save_json_safe(CONTROL_FILE, DEFAULT_CONTROL)
    if not STATE_FILE.exists():
        save_json_safe(STATE_FILE, {"trade_taken_date": None, "open_trade": None})

# ------------- FYERS minimal wrapper (keeps same interface as your earlier code) -------------
try:
    from fyers_api import fyersModel
    fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN, log_path=None, is_async=False)
except Exception:
    fyers = None
    logger.warning("fyers_api not available; running in limited mode")

def safe_quotes(payload):
    if fyers is None:
        return None
    try:
        res = fyers.quotes(payload)
        if not isinstance(res, dict) or res.get("s") != "ok" or "d" not in res:
            logger.warning("Invalid fyers quotes: %s", res)
            send_msg(f"FYERS QUOTES ERROR: {str(res)[:400]}")
            return None
        return res
    except Exception as e:
        logger.exception("safe_quotes error")
        send_msg(f"safe_quotes exception: {e}")
        return None

def get_underlying_ltp():
    res = safe_quotes({"symbols": "NSE:NIFTYBANK-INDEX"})
    if not res:
        return None
    try:
        return res["d"][0]["v"].get("lp")
    except Exception:
        return None

def get_option_premium(symbol):
    res = safe_quotes({"symbols": symbol})
    if not res:
        return None
    try:
        return res["d"][0]["v"].get("lp")
    except Exception:
        return None

def place_order(symbol, side, qty):
    order = {
        "symbol": symbol,
        "qty": qty,
        "type": 2,
        "side": side,
        "productType": "INTRADAY",
        "validity": "DAY"
    }
    # Test/sim mode controlled by control.json param
    control = load_json_safe(CONTROL_FILE, DEFAULT_CONTROL)
    test_mode = control.get("params", {}).get("test_mode", True)
    if test_mode:
        logger.info("[SIM ORDER] %s", order)
        send_msg("[SIM ORDER] " + json.dumps(order))
        return {"status": "SIM", "order": order}
    if fyers is None:
        logger.error("Cannot place order: fyers client not initialized")
        send_msg("ORDER FAILED: fyers client not initialized")
        return None
    try:
        res = fyers.place_order(order)
        logger.info("Order response: %s", str(res)[:400])
        send_msg("ORDER LIVE: " + str(res)[:400])
        return res
    except Exception as e:
        logger.exception("Order placement exception")
        send_msg("ORDER EXCEPTION: " + str(e))
        return None

# ------------- Trading logic (keeps your SL/Target rules) -------------
def compute_strike_from_spot(spot):
    # Round to nearest 100
    try:
        s = float(spot)
        return int(round(s / 100.0) * 100)
    except Exception:
        return None

def resolve_symbol(direction, strike):
    MONTH_ABBR = [None, 'JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']
    today = datetime.today().date()
    # search next 4 Thursdays
    thurs = []
    d = today
    while len(thurs) < 4:
        if d.weekday() == 3:
            thurs.append(d)
            d = d + timedelta(days=7)
        else:
            d = d + timedelta(days=1)
    opt_type = "CE" if direction == "HIGH" else "PE"
    for dt in thurs:
        yy = str(dt.year)[2:]
        mon = MONTH_ABBR[dt.month]
        dd = f"{dt.day:02d}"
        for suf in (f"{yy}{mon}", f"{yy}{mon}{dd}"):
            sym = f"NFO:BANKNIFTY{suf}{strike}{opt_type}"
            prem = get_option_premium(sym)
            if prem is not None:
                return sym, prem
    return None, None

def manage_trade_blocking(symbol, entry, qty):
    # sl -7, target +10
    sl = entry - 7
    target = entry + 10
    send_msg(f"ENTRY={entry}, SL={sl}, TARGET={target} for {symbol}")
    logger.info("Manage trade: %s entry=%s sl=%s target=%s", symbol, entry, sl, target)
    while True:
        # check control file to allow early stop
        control = load_json_safe(CONTROL_FILE, DEFAULT_CONTROL)
        if control.get("mode") != "running":
            logger.info("Stop requested by controller during manage_trade")
            send_msg("Stop requested during open trade. Closing position if possible.")
            # attempt to exit safely
            place_order(symbol, -1, qty)
            return

        cur = get_option_premium(symbol)
        if cur is None:
            time.sleep(2)
            continue
        try:
            cur = float(cur)
        except Exception:
            pass

        if isinstance(cur, (int, float)):
            if cur <= sl:
                send_msg(f"SL HIT {symbol} @ {cur}")
                place_order(symbol, -1, qty)
                return
            if cur >= target:
                send_msg(f"TARGET HIT {symbol} @ {cur}")
                place_order(symbol, -1, qty)
                return
        time.sleep(2)

# ------------- Main bot run loop -------------
def run_iteration():
    # Called repeatedly while control says running.
    control = load_json_safe(CONTROL_FILE, DEFAULT_CONTROL)
    params = control.get("params", DEFAULT_CONTROL["params"])
    premium_min = params.get("premium_min", 35)
    premium_max = params.get("premium_max", 45)
    qty = params.get("qty", 1)
    high = params.get("candle_high", 56000)
    low = params.get("candle_low", 55800)

    state = load_json_safe(STATE_FILE, {"trade_taken_date": None, "open_trade": None})
    if state.get("trade_taken_date"):
        try:
            dt = datetime.fromisoformat(state["trade_taken_date"])
            if dt.date() == datetime.now().date():
                logger.info("Trade already taken today; sleeping.")
                return
        except Exception:
            pass

    price = get_underlying_ltp()
    if price is None:
        logger.info("Could not get underlying LTP")
        return

    try:
        price_float = float(price)
    except Exception:
        logger.info("Price is not numeric: %s", price)
        return

    direction = None
    if price_float > high:
        direction = "HIGH"
    elif price_float < low:
        direction = "LOW"

    if not direction:
        return

    strike = compute_strike_from_spot(price_float)
    symbol, premium = resolve_symbol(direction, strike)
    if symbol is None or premium is None:
        send_msg(f"Symbol resolution failed for direction {direction} strike {strike}")
        return

    try:
        premium_f = float(premium)
    except Exception:
        premium_f = premium

    if not (premium_min <= premium_f <= premium_max):
        logger.info("Premium %s not in range %s-%s", premium_f, premium_min, premium_max)
        return

    # Place buy order
    res = place_order(symbol, 1, qty)
    if res is None:
        logger.warning("Order placement failed")
        return

    # Save state
    state["trade_taken_date"] = datetime.now().isoformat()
    state["open_trade"] = {"symbol": symbol, "entry": premium_f, "qty": qty}
    save_json_safe(STATE_FILE, state)

    # Manage exit
    manage_trade_blocking(symbol, premium_f, qty)

def main_loop():
    ensure_files()
    send_msg("Bot process started (background).")
    logger.info("Bot started.")
    while True:
        try:
            control = load_json_safe(CONTROL_FILE, DEFAULT_CONTROL)
            mode = control.get("mode", "idle")
            # scheduled stop handling
            stop_at = control.get("stop_at")
            if stop_at:
                try:
                    stop_dt = datetime.fromisoformat(stop_at)
                    if datetime.now() >= stop_dt:
                        control["mode"] = "idle"
                        control["stop_at"] = None
                        control["last_command_time"] = datetime.now().isoformat()
                        save_json_safe(CONTROL_FILE, control)
                        send_msg("Scheduled stop executed; switching to idle.")
                        mode = "idle"
                except Exception:
                    pass

            if mode == "running":
                # run a single iteration; the manage_trade is blocking until exit or stop
                run_iteration()
            else:
                # idle: sleep longer
                time.sleep(5)
        except Exception:
            tb = traceback.format_exc()
            logger.exception("Unhandled exception in main_loop")
            send_msg("Bot crashed: " + tb[:600])
            time.sleep(5)

if __name__ == "__main__":
    main_loop()
