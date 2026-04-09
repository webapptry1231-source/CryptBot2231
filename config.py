import os
from dotenv import load_dotenv
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BTC_ONLY = os.getenv("BTC_ONLY", "true").lower() == "true"

COINS = ["BTC/USDT"] if BTC_ONLY else [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
    "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT", "TON/USDT"
]

TIMEFRAME = "15m"
TIMEFRAME_4H = "4h"
CANDLES_NEEDED = 300
SCAN_INTERVAL_SECONDS = 900

STRONG_SIGNAL_THRESHOLD = 80
WEAK_SIGNAL_THRESHOLD = 75

TP_PERCENT = 2.0
SL_PERCENT = 0.5
MAX_HOLD_CANDLES = 48
FEE_PERCENT = 0.05

LEVERAGE = int(os.getenv("LEVERAGE", "3"))

SIMULATION_MODE = True

HISTORIC_MODE = os.getenv("HISTORIC_MODE", "false").lower() == "true"
LIVE_MODE = os.getenv("LIVE_MODE", "false").lower() == "true"
HYBRID_MODE = os.getenv("HYBRID_MODE", "false").lower() == "true"

HISTORICAL_DAYS = int(os.getenv("HISTORICAL_DAYS", "30"))
BUY_AMOUNT = float(os.getenv("BUY_AMOUNT", "50"))

DAILY_TRADE_CAP = 2
SIGNAL_COOLDOWN_HOURS = 1
TRADE_HOURS_START = 11
TRADE_HOURS_END = 19
MAX_CONCURRENT_TRADES = 3