import os
from dotenv import load_dotenv
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BTC_ONLY = os.getenv("BTC_ONLY", "false").lower() == "true"

COINS = ["BTC/USDT"] if BTC_ONLY else [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT",
    "DOGE/USDT", "LINK/USDT", "TON/USDT"
]

TIMEFRAME     = "15m"
TIMEFRAME_4H  = "4h"
CANDLES_NEEDED       = 300
SCAN_INTERVAL_SECONDS = 900

# ── Signal quality thresholds ────────────────────────────────────────────────
# WEAK_SIGNAL_THRESHOLD : minimum score to qualify as a signal at all
# STRONG_SIGNAL_THRESHOLD: score that earns "STRONG" label in Telegram message
WEAK_SIGNAL_THRESHOLD   = 65
STRONG_SIGNAL_THRESHOLD = 78
TOXIC_ZONE_MIN = 87    # Scores in this range are filtered out
TOXIC_ZONE_MAX = 89    # Also 91-93 filtered

# ── Session windows (UTC hours, inclusive start, exclusive end) ──────────────
# The engine picks the single BEST-scoring candle inside each session.
# This is the mechanism that produces 1-3 calls per day instead of spam.
SESSION_MORNING_START = 6    # 06:00 UTC
SESSION_MORNING_END   = 12   # 12:00 UTC  (exclusive → last candle at 11:45)
SESSION_AFTERNOON_START = 13  # 13:00 UTC
SESSION_AFTERNOON_END   = 22  # 22:00 UTC  (exclusive → last candle at 21:45)

# Minimum score a session's best candle must reach to actually fire a signal.
# Keeps quality high; prevents scraping the barrel on quiet days.
SESSION_MIN_SCORE = 73

# ── Exit strategy ────────────────────────────────────────────────────────────
PARTIAL_TP_PERCENT = 1.0    # When profit hits 1%, close 50%
PARTIAL_TP_SIZE = 0.5       # Close 50% of position
TIMEOUT_HOURS = 2.5         # Max hold time in hours (2.5h = 10 candles)

# ── Long settings ────────────────────────────────────────────────────────────
TP_LONG_PERCENT    = 2.0
SL_LONG_PERCENT    = 1.0
TRAIL_ACTIVATE_LONG = 1.0
MAX_HOLD_CANDLES_LONG = 10    # 2.5 hours on 15m chart

# ── Short settings (tighter) ─────────────────────────────────────────────────
TP_SHORT_PERCENT    = 1.8
SL_SHORT_PERCENT    = 1.0
TRAIL_ACTIVATE_SHORT = 1.0
MAX_HOLD_CANDLES_SHORT = 10   # 2.5 hours

# ── Trailing stop ────────────────────────────────────────────────────────────
TRAILING_STOP_PERCENT = 1.5   # wider trail = let winners run to 2:1 TP

# ── ATR-based SL/TP (adaptive sizing) ────────────────────────────────────────
ENABLE_ATR_SL    = os.getenv("ENABLE_ATR_SL", "true").lower() == "true"
ATR_SL_MULTIPLIER = 1.5    # SL = ATR × 1.5
ATR_TP_RR         = 2.0    # TP = SL × 2.0  (2:1 RR minimum)
ATR_SL_MIN_PCT    = 1.0    # floor: never tighter than 1.0% (covers 15m noise)
ATR_SL_MAX_PCT    = 2.0    # ceiling: never wider than 2.0%

# ── Fee model ────────────────────────────────────────────────────────────────
FEE_PERCENT = 0.06   # 0.06% taker fee per side → 0.12% round-trip
SLIPPAGE_PERCENT = 0.10   # 0.10% market order slippage per side
TOTAL_COST_PCT = (FEE_PERCENT + SLIPPAGE_PERCENT) * 2  # = 0.32% round-trip

# ── Position sizing ──────────────────────────────────────────────────────────
BUY_AMOUNT = float(os.getenv("BUY_AMOUNT", "100"))
LEVERAGE   = int(os.getenv("LEVERAGE", "2"))

# ── Mode: SURGICAL (default) or LIVE ─────────────────────────────────────────
# SURGICAL = test a specific past date via SCAN_DATE env var
# LIVE     = scheduled live scanning every SCAN_INTERVAL_SECONDS
MODE      = os.getenv("MODE", "SURGICAL")
SCAN_DATE = os.getenv("SCAN_DATE", "")          # e.g. "2024-11-15"
HISTORICAL_DAYS = int(os.getenv("HISTORICAL_DAYS", "90"))

# ── Risk controls ────────────────────────────────────────────────────────────
MAX_CONCURRENT_TRADES  = 3
SIGNAL_COOLDOWN_HOURS  = 0.5    # minimum gap between signals for same symbol
SAME_COIN_COOLDOWN_MIN = 60     # 60-minute lockout per coin after any trade
DAILY_LOSS_CAP         = 3      # stop trading a symbol after 3 losses in a day
CONSECUTIVE_SL_STOP    = 3      # halt entire scan after 3 consecutive SL hits
TRADE_HOURS_START      = 6      # UTC — no trades before this hour
TRADE_HOURS_END        = 22     # UTC — no trades at/after this hour
TRADE_HOURS_BLACKOUT_START = 13.75  # 13:45 UTC - no trade zone (DEPRECATED - use BLACKOUT_START_H/M)
BLACKOUT_START_H = 13
BLACKOUT_START_M = 30  # 13:30 UTC
BLACKOUT_END_H = 16
BLACKOUT_END_M = 0    # 16:00 UTC
TRADE_HOURS_BLACKOUT_END   = 16     # 16:00 UTC - no trade zone
NEUTRAL_ZONE_PCT       = 0.5    # % band around EMA200 = sleep zone
TRADE_DAYS_BLOCKED     = [5]    # 5 = Saturday (Sunday=6 not blocked)

# ── Per-symbol SL overrides ───────────────────────────────────────────────────
SL_OVERRIDES = {
    "DOGE/USDT": 1.2,
    "SOL/USDT":  1.1,
    "AVAX/USDT": 1.1,
    "LINK/USDT": 1.0,
}
