import sqlite3
import os
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "/data/trades.db")


def init_db():
    os.makedirs("logs", exist_ok=True)
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT,
            symbol      TEXT,
            score       INTEGER,
            reason      TEXT,
            direction   TEXT,
            entry_price REAL,
            tp_price    REAL,
            sl_price    REAL,
            mode        TEXT DEFAULT 'SIMULATION'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_trades (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            run_timestamp     TEXT,
            symbol            TEXT,
            date              TEXT,
            direction         TEXT,
            entry_time        TEXT,
            exit_time         TEXT,
            score             INTEGER,
            reason            TEXT,
            entry             REAL,
            exit              REAL,
            tp                REAL,
            sl                REAL,
            result            TEXT,
            pnl_pct           REAL,
            pnl_after_fee     REAL,
            pnl_usd           REAL,
            pnl_usd_after_fee REAL,
            leverage          INTEGER,
            buy_amount        REAL,
            hold_hours        REAL,
            mfe_pct           REAL,
            mae_pct           REAL
        )
    """)
    conn.commit()
    conn.close()


def log_signal(symbol: str, score: int, reason: str, direction: str,
               entry: float, tp: float, sl: float):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO signals "
        "(timestamp, symbol, score, reason, direction, entry_price, tp_price, sl_price) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (datetime.utcnow().isoformat(), symbol, score, reason, direction, entry, tp, sl),
    )
    conn.commit()
    conn.close()


def log_backtest_trade(run_timestamp: str, trade: dict):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT INTO backtest_trades
            (run_timestamp, symbol, date, direction,
             entry_time, exit_time, score, reason,
             entry, exit, tp, sl, result,
             pnl_pct, pnl_after_fee, pnl_usd, pnl_usd_after_fee,
             leverage, buy_amount, hold_hours, mfe_pct, mae_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_timestamp,
            trade.get("symbol", "UNKNOWN"),
            trade.get("date", ""),
            trade.get("direction", "LONG"),
            trade.get("entry_time", ""),
            trade.get("exit_time", ""),
            trade.get("score", 0),
            trade.get("reason", ""),
            trade.get("entry", 0),
            trade.get("exit", 0),
            trade.get("tp", 0),
            trade.get("sl", 0),
            trade.get("result", ""),
            trade.get("pnl_pct", 0),
            trade.get("pnl_after_fee", 0),
            trade.get("pnl_usd", 0),
            trade.get("pnl_usd_after_fee", 0),
            trade.get("leverage", 1),
            trade.get("buy_amount", 0),
            trade.get("hold_hours", 0),
            trade.get("mfe_pct", 0),
            trade.get("mae_pct", 0),
        ),
    )
    conn.commit()
    conn.close()
