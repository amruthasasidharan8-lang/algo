# app.py
# Streamlit controller for BankNifty bot (Advanced)
# Run with: streamlit run app.py

import streamlit as st
import json
import datetime
import time
import os
from pathlib import Path

CONTROL_FILE = Path("control.json")
STATE_FILE = Path("state.json")
LOG_FILE = Path("bot.log")

DEFAULT_CONTROL = {
    "mode": "idle",            # "idle" | "running"
    "start_at": None,          # iso timestamp
    "stop_at": None,           # iso timestamp (optional)
    "params": {
        "premium_min": 35,
        "premium_max": 45,
        "qty": 1,
        "test_mode": True,
        # placeholder candle values (bot will compute real candle if available)
        "candle_high": 56000,
        "candle_low": 55800
    },
    "last_command_time": None,
    "last_command_by": None
}

def ensure_control():
    if not CONTROL_FILE.exists():
        with open(CONTROL_FILE, "w") as f:
            json.dump(DEFAULT_CONTROL, f, indent=2)

def read_control():
    ensure_control()
    try:
        with open(CONTROL_FILE) as f:
            return json.load(f)
    except Exception:
        return DEFAULT_CONTROL.copy()

def write_control(c):
    with open(CONTROL_FILE, "w") as f:
        json.dump(c, f, indent=2)

def read_state():
    if not STATE_FILE.exists():
        return {"trade_taken_date": None, "open_trade": None}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"trade_taken_date": None, "open_trade": None}

def tail_log(n=200):
    if not LOG_FILE.exists():
        return "No log file yet."
    try:
        with open(LOG_FILE, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            block = 1024
            data = b""
            while size > 0 and len(data) < n*100:
                size = max(0, size-block)
                f.seek(size)
                data = f.read() + data
            text = data.decode(errors="ignore")
            lines = text.splitlines()
            return "\n".join(lines[-n:])
    except Exception as e:
        return f"Could not read log: {e}"

st.set_page_config(page_title="BankNifty Bot Controller", layout="wide")
st.title("BankNifty 9:30 Breakout — Controller (Advanced)")

control = read_control()
state = read_state()

col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("Bot control")
    st.write("Current mode:", control.get("mode"))
    st.write("Last command:", control.get("last_command_time"), "by", control.get("last_command_by"))

    st.markdown("### Run parameters")
    params = control.get("params", {})
    premium_min = st.number_input("Premium min", value=int(params.get("premium_min", 35)), step=1)
    premium_max = st.number_input("Premium max", value=int(params.get("premium_max", 45)), step=1)
    qty = st.number_input("Qty", value=int(params.get("qty", 1)), step=1)
    test_mode = st.checkbox("TEST MODE (no live orders)", value=bool(params.get("test_mode", True)))
    candle_high = st.number_input("9:30 candle high (placeholder)", value=int(params.get("candle_high", 56000)))
    candle_low = st.number_input("9:30 candle low (placeholder)", value=int(params.get("candle_low", 55800)))

    start_btn, stop_btn = st.columns(2)
    with start_btn:
        if st.button("Start Bot (Immediate)"):
            control["mode"] = "running"
            control["start_at"] = datetime.datetime.now().isoformat()
            control["stop_at"] = None
            control["params"].update({
                "premium_min": int(premium_min),
                "premium_max": int(premium_max),
                "qty": int(qty),
                "test_mode": bool(test_mode),
                "candle_high": int(candle_high),
                "candle_low": int(candle_low)
            })
            control["last_command_time"] = datetime.datetime.now().isoformat()
            control["last_command_by"] = "streamlit"
            write_control(control)
            st.success("Start command written to control.json")

    with stop_btn:
        if st.button("Stop Bot (Immediate)"):
            control["mode"] = "idle"
            control["stop_at"] = datetime.datetime.now().isoformat()
            control["last_command_time"] = datetime.datetime.now().isoformat()
            control["last_command_by"] = "streamlit"
            write_control(control)
            st.warning("Stop command written to control.json")

    st.markdown("---")
    st.markdown("### Advanced controls")
    schedule = st.checkbox("Schedule a stop in X minutes")
    if schedule:
        mins = st.number_input("Stop after minutes", value=10, min_value=1)
        if st.button("Schedule stop"):
            control["stop_at"] = (datetime.datetime.now() + datetime.timedelta(minutes=int(mins))).isoformat()
            control["last_command_time"] = datetime.datetime.now().isoformat()
            control["last_command_by"] = "streamlit"
            write_control(control)
            st.info(f"Bot will be asked to stop after {mins} minutes.")

with col2:
    st.subheader("Current state")
    st.json(state)
    st.markdown("---")
    st.subheader("Recent bot log (tail)")
    st.code(tail_log(200), language="")

st.markdown("---")
st.write("Notes:")
st.write("- After changing tokens or config on server, restart `bot.py` with pm2 or systemd.")
st.write("- Do NOT store your ACCESS_TOKEN in a public repo. Use server env or protected file.")
