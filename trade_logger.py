import sqlite3
import os
from datetime import datetime

DB_PATH = "logs/trades.db"

def init_db():
    os.makedirs("logs", exist_ok=True)
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