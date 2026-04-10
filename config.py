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

TIMEFRAME     = "15m"
TIMEFRAME_4H  = "4h"
CANDLES_NEEDED       = 300
SCAN_INTERVAL_SECONDS = 900

# ── Signal quality thresholds ────────────────────────────────────────────────
# WEAK_SIGNAL_THRESHOLD : minimum score to qualify as a signal at all
# STRONG_SIGNAL_THRESHOLD: score that earns "STRONG" label in Telegram message
WEAK_SIGNAL_THRESHOLD   = 60
STRONG_SIGNAL_THRESHOLD = 75   # lowered from 80; 80 was unreachable on most days

# ── Session windows (UTC hours, inclusive start, exclusive end) ──────────────
# The engine picks the single BEST-scoring candle inside each session.
# This is the mechanism that produces 1-3 calls per day instead of spam.
SESSION_MORNING_START = 6    # 06:00 UTC
SESSION_MORNING_END   = 12   # 12:00 UTC  (exclusive → last candle at 11:45)
SESSION_AFTERNOON_START = 13  # 13:00 UTC
SESSION_AFTERNOON_END   = 22  # 22:00 UTC  (exclusive → last candle at 21:45)

# Minimum score a session's best candle must reach to actually fire a signal.
# Keeps quality high; prevents scraping the barrel on quiet days.
SESSION_MIN_SCORE = 62

# ── Long settings ────────────────────────────────────────────────────────────
TP_LONG_PERCENT    = 2.0
SL_LONG_PERCENT    = 1.0
TRAIL_ACTIVATE_LONG = 1.2
MAX_HOLD_CANDLES_LONG = 20    # 5 hours on 15m chart

# ── Short settings (tighter) ─────────────────────────────────────────────────
TP_SHORT_PERCENT    = 1.8
SL_SHORT_PERCENT    = 1.0
TRAIL_ACTIVATE_SHORT = 1.0
MAX_HOLD_CANDLES_SHORT = 24   # 6 hours

# ── Trailing stop ────────────────────────────────────────────────────────────
TRAILING_STOP_PERCENT = 0.6   # single definition — no more double-assignment bug

# ── ATR-based SL/TP (adaptive sizing) ────────────────────────────────────────
ENABLE_ATR_SL    = os.getenv("ENABLE_ATR_SL", "true").lower() == "true"
ATR_SL_MULTIPLIER = 1.5    # SL = ATR × 1.5
ATR_TP_RR         = 2.0    # TP = SL × 2.0  (2:1 RR minimum)
ATR_SL_MIN_PCT    = 0.5    # floor: never tighter than 0.5%
ATR_SL_MAX_PCT    = 2.0    # ceiling: never wider than 2.0%

# ── Fee model ────────────────────────────────────────────────────────────────
FEE_PERCENT = 0.05   # 0.05% per side → 0.10% round-trip

# ── Position sizing ──────────────────────────────────────────────────────────
BUY_AMOUNT = float(os.getenv("BUY_AMOUNT", "150"))
LEVERAGE   = int(os.getenv("LEVERAGE", "3"))

# ── Mode: SURGICAL (default) or LIVE ─────────────────────────────────────────
# SURGICAL = test a specific past date via SCAN_DATE env var
# LIVE     = scheduled live scanning every SCAN_INTERVAL_SECONDS
MODE      = os.getenv("MODE", "SURGICAL")
SCAN_DATE = os.getenv("SCAN_DATE", "")          # e.g. "2024-11-15"
HISTORICAL_DAYS = int(os.getenv("HISTORICAL_DAYS", "90"))

# ── Risk controls ────────────────────────────────────────────────────────────
MAX_CONCURRENT_TRADES  = 3
SIGNAL_COOLDOWN_HOURS  = 0.5    # minimum gap between signals for same symbol
DAILY_LOSS_CAP         = 3      # stop trading a symbol after 3 losses in a day
CONSECUTIVE_SL_STOP    = 3      # halt entire scan after 3 consecutive SL hits
TRADE_HOURS_START      = 6      # UTC — no trades before this hour
TRADE_HOURS_END        = 22     # UTC — no trades at/after this hour
NEUTRAL_ZONE_PCT       = 0.5    # % band around EMA200 = sleep zone
TRADE_DAYS_BLOCKED     = [5]    # 5 = Saturday (Sunday=6 not blocked)

# ── Per-symbol SL overrides ───────────────────────────────────────────────────
SL_OVERRIDES = {
    "DOGE/USDT": 0.8,
    "SOL/USDT":  0.7,
    "AVAX/USDT": 0.7,
    "LINK/USDT": 0.6,
}
