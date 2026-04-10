import sqlite3
import os
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "/data/trades.db")

def init_db():
    os.makedirs("logs", exist_ok=True)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            symbol TEXT,
            score INTEGER,
            reason TEXT,
            entry_price REAL,
            tp_price REAL,
            sl_price REAL,
            mode TEXT DEFAULT 'SIMULATION'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_timestamp TEXT,
            symbol TEXT,
            date TEXT,
            entry_time TEXT,
            exit_time TEXT,
            score INTEGER,
            reason TEXT,
            entry REAL,
            exit REAL,
            tp REAL,
            sl REAL,
            result TEXT,
            pnl_pct REAL,
            pnl_after_fee REAL,
            pnl_usd REAL,
            pnl_usd_after_fee REAL,
            leverage INTEGER,
            buy_amount REAL,
            hold_hours REAL,
            mfe_pct REAL,
            mae_pct REAL,
            direction TEXT
        )
    """)
    conn.commit()
    conn.close()

def log_signal(symbol: str, score: int, reason: str, entry: float, tp: float, sl: float):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO signals (timestamp, symbol, score, reason, entry_price, tp_price, sl_price) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (datetime.utcnow().isoformat(), symbol, score, reason, entry, tp, sl)
    )
    conn.commit()
    conn.close()

def log_backtest_trade(run_timestamp: str, trade: dict):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """INSERT INTO backtest_trades 
           (run_timestamp, symbol, date, entry_time, exit_time, score, reason, entry, exit, tp, sl, result,
            pnl_pct, pnl_after_fee, pnl_usd, pnl_usd_after_fee, leverage, buy_amount, hold_hours, mfe_pct, mae_pct, direction)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_timestamp, trade.get("symbol", "UNKNOWN"), trade["date"], trade["entry_time"], trade["exit_time"], trade["score"], trade["reason"],
         trade["entry"], trade["exit"], trade["tp"], trade["sl"], trade["result"], trade["pnl_pct"], trade["pnl_after_fee"],
         trade["pnl_usd"], trade["pnl_usd_after_fee"], trade["leverage"], trade["buy_amount"], trade["hold_hours"],
         trade["mfe_pct"], trade["mae_pct"], trade.get("direction", "LONG"))
    )
    conn.commit()
    conn.close()