import os
from dotenv import load_dotenv
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BTC_ONLY = os.getenv("BTC_ONLY", "false").lower() == "true"

COINS = ["BTC/USDT"] if BTC_ONLY else [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
    "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT", "TON/USDT"
]

TIMEFRAME = "15m"
TIMEFRAME_4H = "4h"
CANDLES_NEEDED = 300
SCAN_INTERVAL_SECONDS = 900

STRONG_SIGNAL_THRESHOLD = 80
WEAK_SIGNAL_THRESHOLD = 55

# Long settings
TP_LONG_PERCENT = 2.0
SL_LONG_PERCENT = 1.0
TRAIL_ACTIVATE_LONG = 1.2
TRAILING_STOP_PERCENT = 0.6
MAX_HOLD_CANDLES_LONG = 32  # 8 hours

# Short settings (asymmetric - tighter)
TP_SHORT_PERCENT = 1.8
SL_SHORT_PERCENT = 1.0
TRAIL_ACTIVATE_SHORT = 1.0
MAX_HOLD_CANDLES_SHORT = 24  # 6 hours

# Legacy (for backward compatibility)
TP_PERCENT = 0.7
SL_PERCENT = 1.0
MAX_HOLD_CANDLES = 32
FEE_PERCENT = 0.05
PARTIAL_TP_PERCENT = 0.5
PARTIAL_TP_SIZE = 0.5
TRAILING_STOP_PERCENT = 0.5

LEVERAGE = int(os.getenv("LEVERAGE", "2"))

SIMULATION_MODE = True

HISTORIC_MODE = os.getenv("HISTORIC_MODE", "false").lower() == "true"
LIVE_MODE = os.getenv("LIVE_MODE", "false").lower() == "true"
HYBRID_MODE = os.getenv("HYBRID_MODE", "false").lower() == "true"

HISTORICAL_DAYS = int(os.getenv("HISTORICAL_DAYS", "90"))
SCAN_DATE = os.getenv("SCAN_DATE", "")  # Surgical mode: specific date (YYYY-MM-DD)
SURGICAL_MODE = os.getenv("SURGICAL_MODE", "false").lower() == "true"
BUY_AMOUNT = float(os.getenv("BUY_AMOUNT", "50"))

# Targeted testing: comma-separated dates (e.g., "2026-01-13,2026-01-14")
_test_dates_raw = os.getenv("TEST_DATES", "")
TEST_DATES = [d.strip() for d in _test_dates_raw.split(",") if d.strip()]

if SCAN_DATE:
    print(f"SURGICAL MODE: Scanning {SCAN_DATE}")
elif TEST_DATES:
    print(f"TARGETED TESTING: Scanning only dates: {TEST_DATES}")
else:
    print(f"GLOBAL TESTING: Scanning all {HISTORICAL_DAYS} days")

DAILY_TRADE_CAP = 999
SIGNAL_COOLDOWN_HOURS = 1.0
TRADE_HOURS_START = 7   # 07:00 UTC
TRADE_HOURS_END = 22    # 22:00 UTC
NEUTRAL_ZONE_PCT = 0.5  # Sleep if within 0.5% of EMA200
TRADE_HOURS_PREFERRED_START = 16
TRADE_HOURS_PREFERRED_END = 20
TRADE_DAYS_BLOCKED = [0, 5]  # Monday=0, Saturday=5
TRADE_HOURS_BLACKOUT_START = 4
TRADE_HOURS_BLACKOUT_END = 8
MAX_CONCURRENT_TRADES = 6
DAILY_LOSS_CAP = 2
CONSECUTIVE_SL_STOP = 3
MAX_NOTIONAL = 100.0

SL_OVERRIDES = {
    "DOGE/USDT": 0.8,
    "SOL/USDT": 0.7,
    "AVAX/USDT": 0.7,
    "LINK/USDT": 0.6
}