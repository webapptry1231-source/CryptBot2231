import asyncio
import csv
import json
import logging
import os
from datetime import datetime, timedelta

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters
)

from config import (
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, COINS,
    TIMEFRAME, CANDLES_NEEDED, SCAN_INTERVAL_SECONDS,
    WEAK_SIGNAL_THRESHOLD, STRONG_SIGNAL_THRESHOLD,
    HISTORICAL_DAYS, SCAN_DATE, LEVERAGE,
    TP_LONG_PERCENT, TP_SHORT_PERCENT,
    SL_LONG_PERCENT, SL_SHORT_PERCENT,
    BUY_AMOUNT,
    MAX_HOLD_CANDLES_LONG, MAX_HOLD_CANDLES_SHORT,
    TRADE_HOURS_START, TRADE_HOURS_END,
)

# Conversation states
SELECT_MODE, SELECT_DATE_DAYS, SELECT_DAYS_CUSTOM, SELECT_DATE_CUSTOM, SELECT_COINS = range(5)

# User session data storage
user_config = {
    "mode": None,
    "scan_date": None,
    "days": None,
    "coins": None,
}

# Quick date options (last 7 days)
def get_date_options():
    today = datetime.now().date()
    return [
        (today - timedelta(days=i)).strftime("%Y-%m-%d") 
        for i in range(7)
    ]

# Quick days options
DAYS_OPTIONS = ["7", "14", "30", "60", "90"]

# Coin presets
COIN_PRESETS = {
    "BTC Only": ["BTC/USDT"],
    "Top 3": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
    "Top 5": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"],
    "Top 10": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", 
               "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT", "TON/USDT"],
}

from data_fetcher import fetch_ohlcv
from scan_engine import scan_daily_historical, check_4h_trend, determine_regime, _4h_cache
from signal_formatter import format_signal_message
from trade_logger import init_db, log_signal, log_backtest_trade
from concurrent.futures import ThreadPoolExecutor
from indicators import compute_indicators
from scorer import calculate_score

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Live scan: semaphore to cap concurrent open positions ─────────────────────
import threading
_MAX_OPEN = 3
_position_semaphore = threading.Semaphore(_MAX_OPEN)
_open_positions: dict = {}


# ─────────────────────────────────────────────────────────────────────────────
# Live scan helpers
# ─────────────────────────────────────────────────────────────────────────────

def scan_coin_live(symbol: str) -> dict:
    """
    Evaluate one coin for a live (real-time) signal.
    Returns a result dict; score=0 means no actionable signal.
    """
    try:
        current_hour = datetime.utcnow().hour
        if current_hour < TRADE_HOURS_START or current_hour >= TRADE_HOURS_END:
            return {"symbol": symbol, "score": 0, "reason": "outside_trade_hours",
                    "price": 0, "direction": "LONG", "error": None}

        df = fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=CANDLES_NEEDED)
        df = compute_indicators(df)

        direction = determine_regime(df)
        if direction == "NEUTRAL":
            return {"symbol": symbol, "score": 0, "reason": "neutral_regime",
                    "price": 0, "direction": "LONG", "error": None}

        # BTC 4h alignment bonus for altcoins
        trend_bonus = 0
        if symbol != "BTC/USDT":
            btc_bullish = check_4h_trend("BTC/USDT")
            if (direction == "LONG" and btc_bullish) or (direction == "SHORT" and not btc_bullish):
                trend_bonus = 5

        score, reason = calculate_score(df, trend_bonus=trend_bonus, direction=direction)

        if score < WEAK_SIGNAL_THRESHOLD or "low_volume" in reason:
            return {"symbol": symbol, "score": 0, "reason": reason,
                    "price": 0, "direction": direction, "error": None}

        price = float(df.iloc[-1]["close"])
        return {"symbol": symbol, "score": score, "reason": reason,
                "price": price, "direction": direction, "error": None}

    except Exception as exc:
        return {"symbol": symbol, "score": 0, "reason": "",
                "price": 0, "direction": "LONG", "error": str(exc)}


# ─────────────────────────────────────────────────────────────────────────────
# Summary calculation
# ─────────────────────────────────────────────────────────────────────────────

def calculate_summary(results: list) -> dict:
    if not results:
        return {
            "total": 0, "tp": 0, "sl": 0, "trail": 0, "timeout": 0,
            "wins": 0, "losses": 0, "long_wins": 0, "short_wins": 0,
            "win_rate": 0, "total_pnl": 0, "total_pnl_usd": 0,
        }

    wins       = sum(1 for r in results if r["pnl_usd_after_fee"] > 0)
    losses     = sum(1 for r in results if r["pnl_usd_after_fee"] <= 0)
    long_wins  = sum(1 for r in results if r.get("direction") == "LONG"  and r["pnl_usd_after_fee"] > 0)
    short_wins = sum(1 for r in results if r.get("direction") == "SHORT" and r["pnl_usd_after_fee"] > 0)
    tp         = sum(1 for r in results if r["result"] == "TP HIT")
    sl         = sum(1 for r in results if r["result"] == "SL HIT")
    trail      = sum(1 for r in results if r["result"] == "TRAIL STOP")
    timeout    = sum(1 for r in results if r["result"] == "TIMEOUT")
    total      = len(results)

    return {
        "total":         total,
        "tp":            tp,
        "sl":            sl,
        "trail":         trail,
        "timeout":       timeout,
        "wins":          wins,
        "losses":        losses,
        "long_wins":     long_wins,
        "short_wins":    short_wins,
        "win_rate":      round(wins / total * 100, 1) if total else 0,
        "total_pnl":     round(sum(r["pnl_after_fee"]    for r in results), 2),
        "total_pnl_usd": round(sum(r["pnl_usd_after_fee"] for r in results), 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Historical / Surgical scan runner
# ─────────────────────────────────────────────────────────────────────────────

async def run_historical_scan(send_func, scan_date=None, days=None, coins=None, force_historical=False, end_date=None):
    # Clear 4h cache once before scanning all symbols
    _4h_cache.clear()
    
    # Determine actual values:
    # - If scan_date/days passed explicitly (not None), use them
    # - If force_historical=True, ignore SCAN_DATE from env
    # - Otherwise, use env defaults
    if force_historical:
        actual_scan_date = None  # Force historical mode
    else:
        actual_scan_date = scan_date if scan_date is not None else SCAN_DATE
    
    actual_days = days if days is not None else HISTORICAL_DAYS
    actual_coins = coins or COINS

    all_results: list = []

    if end_date:
        # Date range mode (for test data periods like Q1 2026)
        logger.info(f"DATE RANGE scan: {actual_days} days ending {end_date}")
        await send_func(
            f"📊 Starting Date Range Scan\n"
            f"📅 Period: {actual_days} days ending {end_date}\n"
            f"🔄 {LEVERAGE}x | ${BUY_AMOUNT}/trade\n"
            f"🪙 Coins: {', '.join(actual_coins)}"
        )
        for symbol in actual_coins:
            results = scan_daily_historical(symbol, days=actual_days, end_date=end_date)
            all_results.extend(results)
    elif actual_scan_date:
        logger.info(f"SURGICAL scan: {actual_scan_date}")
        await send_func(
            f"📊 Starting Surgical Scan\n"
            f"📅 Date: {actual_scan_date}\n"
            f"🔄 {LEVERAGE}x | ${BUY_AMOUNT}/trade\n"
            f"🪙 Coins: {', '.join(actual_coins)}"
        )
        for symbol in actual_coins:
            results = scan_daily_historical(symbol, target_date=actual_scan_date)
            all_results.extend(results)
    else:
        logger.info(f"HISTORICAL scan: {actual_days} days")
        await send_func(
            f"📊 Starting Historical Scan\n"
            f"🔄 {actual_days} days | {LEVERAGE}x | ${BUY_AMOUNT}/trade\n"
            f"🪙 Coins: {', '.join(actual_coins)}"
        )
        for symbol in actual_coins:
            results = scan_daily_historical(symbol, days=actual_days)
            all_results.extend(results)

    if not all_results:
        await send_func("❌ No signals generated — check logs for details")
        return

    # Cap at 500 to avoid Telegram flood
    all_results = all_results[:500]
    summary = calculate_summary(all_results)

    # ── Summary message ───────────────────────────────────────────────────────
    header = f"📊 {'SURGICAL REPORT: ' + actual_scan_date if actual_scan_date else f'BACKTEST ({actual_days}d)'}\n"
    header += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    header += f"💰 ${BUY_AMOUNT} × {LEVERAGE}x = ${BUY_AMOUNT * LEVERAGE:.0f} notional\n"
    header += f"📈 Total Trades : {summary['total']}\n"
    header += (
        f"✅ Wins  : {summary['wins']}  "
        f"(🔺L {summary['long_wins']} | 🔻S {summary['short_wins']})\n"
    )
    header += f"❌ Losses: {summary['losses']}\n"
    header += (
        f"🎯 Breakdown: TP={summary['tp']} | SL={summary['sl']} | "
        f"Trail={summary['trail']} | TO={summary['timeout']}\n"
    )
    header += f"📊 Win Rate  : {summary['win_rate']}%\n"
    header += f"💵 Net PnL   : ${summary['total_pnl_usd']:.2f}\n"
    header += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    await send_func(header)

    # ── One message per trade (safe within Telegram 4096-char limit) ──────────
    for r in all_results:
        win_emoji  = "✅" if r["pnl_usd_after_fee"] > 0 else "❌"
        dir_emoji  = "🔺" if r.get("direction") == "LONG" else "🔻"
        notional   = r["buy_amount"] * r["leverage"]

        trade_msg = (
            f"{win_emoji}{dir_emoji} {r.get('symbol', '?')} "
            f"{r['date']} {r['entry_time']} → {r['exit_time']}\n"
            f"📌 {r['entry']} → {r['exit']}\n"
            f"🛑 SL:{r['sl']} | 🎯 TP:{r['tp']}\n"
            f"📦 ${notional:.0f} | Hold:{r['hold_hours']}h\n"
            f"📈 MFE:{r.get('mfe_pct', 0):.2f}% | MAE:{r.get('mae_pct', 0):.2f}%\n"
            f"💵 PnL: ${r['pnl_usd_after_fee']:.2f} "
            f"({r['pnl_after_fee']:.2f}%) | {r['result']}\n"
            f"📊 Score:{r['score']} | {r.get('reason', '')[:80]}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        await send_func(trade_msg)

    # ── Persist results ───────────────────────────────────────────────────────
    run_ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    os.makedirs("logs", exist_ok=True)

    json_path = f"logs/backtest_{run_ts}.json"
    with open(json_path, "w") as fh:
        json.dump({
            "run_timestamp": run_ts,
            "scan_date":     SCAN_DATE or None,
            "days":          HISTORICAL_DAYS,
            "leverage":      LEVERAGE,
            "buy_amount":    BUY_AMOUNT,
            "summary":       summary,
            "trades":        all_results,
        }, fh, indent=2)
    logger.info(f"JSON saved → {json_path}")

    csv_path = f"logs/backtest_{run_ts}.csv"
    with open(csv_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=all_results[0].keys())
        writer.writeheader()
        writer.writerows(all_results)
    logger.info(f"CSV  saved → {csv_path}")

    for trade in all_results:
        log_backtest_trade(run_ts, trade)

    logger.info(f"Scan complete — {summary['total']} trades | PnL ${summary['total_pnl_usd']:.2f}")


# ─────────────────────────────────────────────────────────────────────────────
# Live scan runner
# ─────────────────────────────────────────────────────────────────────────────

async def run_live_scan(send_func) -> list[str]:
    messages: list[str] = []
    logger.info("LIVE scan starting …")

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(scan_coin_live, s): s for s in COINS}
        raw_results = [f.result() for f in futures]

    for r in raw_results:
        if r["error"]:
            logger.error(f"Live scan error for {r['symbol']}: {r['error']}")
            continue

        score     = r["score"]
        symbol    = r["symbol"]
        reason    = r["reason"]
        price     = r["price"]
        direction = r["direction"]

        logger.info(f"LIVE | {symbol} dir={direction} score={score} reason={reason}")

        if score < WEAK_SIGNAL_THRESHOLD:
            continue

        # Semaphore: cap concurrent live positions
        if not _position_semaphore.acquire(blocking=False):
            logger.info(f"LIVE | {symbol} blocked — max concurrent positions reached")
            continue
        _open_positions[symbol] = True

        msg = format_signal_message(symbol, score, reason, price, direction)
        if msg:
            await send_func(msg)
            messages.append(msg)
            await asyncio.sleep(0.3)

            # Compute and log TP/SL
            if direction == "LONG":
                tp = round(price * (1 + TP_LONG_PERCENT  / 100), 4)
                sl = round(price * (1 - SL_LONG_PERCENT  / 100), 4)
            else:
                tp = round(price * (1 - TP_SHORT_PERCENT / 100), 4)
                sl = round(price * (1 + SL_SHORT_PERCENT / 100), 4)

            log_signal(symbol, score, reason, direction, price, tp, sl)

    return messages


# ─────────────────────────────────────────────────────────────────────────────
# Telegram command handlers
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = os.getenv("MODE", "SURGICAL")
    await update.message.reply_text(
        f"✅ CryptoSignalBot\n"
        f"Mode     : {mode}\n"
        f"Leverage : {LEVERAGE}x\n"
        f"Max Hold : Long {MAX_HOLD_CANDLES_LONG * 15 // 60}h | Short {MAX_HOLD_CANDLES_SHORT * 15 // 60}h\n"
        f"Coins    : {', '.join(COINS)}\n"
        f"SCAN_DATE: {SCAN_DATE or '(live)'}"
        f"\n\n🔧 Use /config to configure scan mode, date, days, and coins interactively."
    )


async def cmd_config_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start interactive configuration - Reset and show mode selection"""
    user_config["mode"] = None
    user_config["scan_date"] = None
    user_config["days"] = None
    user_config["coins"] = None
    
    keyboard = [
        [KeyboardButton("🔴 LIVE"), KeyboardButton("🟡 HISTORICAL"), KeyboardButton("🔵 SURGICAL")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    await update.message.reply_text(
        "⚙️ *Configure Scan*\n\n"
        "Select scan mode:\n"
        "• 🔴 LIVE - Real-time market scan\n"
        "• 🟡 HISTORICAL - Backtest for X days\n"
        "• 🔵 SURGICAL - Scan specific date",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    return SELECT_MODE


async def cmd_config_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle mode selection with follow-up buttons"""
    text = update.message.text
    
    if "LIVE" in text:
        user_config["mode"] = "LIVE"
        await show_coin_selection(update)
        return SELECT_COINS
        
    elif "HISTORICAL" in text:
        user_config["mode"] = "HISTORICAL"
        keyboard = [
            [KeyboardButton("7 Days"), KeyboardButton("14 Days"), KeyboardButton("30 Days")],
            [KeyboardButton("60 Days"), KeyboardButton("90 Days"), KeyboardButton("180 Days")],
            [KeyboardButton("1 Year (365)"), KeyboardButton("2 Years (730)"), KeyboardButton("Custom ✏️")],
            [KeyboardButton("📅 Q1 2026 (90d)"), KeyboardButton("📅 Q4 2025 (90d)")],
            [KeyboardButton("📅 Q3 2025 (90d)"), KeyboardButton("📅 Q2 2025 (90d)")],
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
        await update.message.reply_text(
            "✅ *HISTORICAL Mode Selected*\n\n"
            "Select number of days to scan:\n"
            "• Short: 7, 14, 30 days\n"
            "• Medium: 60, 90, 180 days\n"
            "• Long: 1 Year, 2 Years\n"
            "• Test Data (from days.txt):\n"
            "  Q1 2026: Apr 11 - Jan 11\n"
            "  Q4 2025: Jan 11 - Oct 13\n"
            "  Q3 2025: Oct 13 - Jul 15\n"
            "  Q2 2025: Jul 15 - Apr 16",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return SELECT_DATE_DAYS
        
    elif "SURGICAL" in text:
        user_config["mode"] = "SURGICAL"
        date_options = get_date_options()
        keyboard = []
        row = []
        for i, date in enumerate(date_options):
            row.append(KeyboardButton(date))
            if (i + 1) % 3 == 0:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([KeyboardButton("📅 Custom Date")])
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
        await update.message.reply_text(
            "✅ *SURGICAL Mode Selected*\n\n"
            "Select a date to scan:",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return SELECT_DATE_DAYS
    else:
        await update.message.reply_text("❌ Invalid. Send /config to start fresh.")
        return ConversationHandler.END


async def cmd_config_date_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle date/days selection"""
    text = update.message.text.strip()
    mode = user_config.get("mode")
    
    if mode == "HISTORICAL":
        # Test data presets (from days.txt) - use scan_date to set end date
        if "Q1 2026" in text:
            user_config["days"] = 90
            user_config["scan_date"] = "2026-04-11"  # End of Q1 2026
            await show_coin_selection(update)
            return SELECT_COINS
        elif "Q4 2025" in text:
            user_config["days"] = 90
            user_config["scan_date"] = "2026-01-11"  # End of Q4 2025
            await show_coin_selection(update)
            return SELECT_COINS
        elif "Q3 2025" in text:
            user_config["days"] = 90
            user_config["scan_date"] = "2025-10-13"  # End of Q3 2025
            await show_coin_selection(update)
            return SELECT_COINS
        elif "Q2 2025" in text:
            user_config["days"] = 90
            user_config["scan_date"] = "2025-07-15"  # End of Q2 2025
            await show_coin_selection(update)
            return SELECT_COINS
        
        if text.endswith("Days") or text == "180 Days":
            if text == "180 Days":
                days = 180
            else:
                days = int(text.split()[0])
            user_config["days"] = days
            await show_coin_selection(update)
            return SELECT_COINS
        elif "1 Year" in text:
            user_config["days"] = 365
            await show_coin_selection(update)
            return SELECT_COINS
        elif "2 Years" in text:
            user_config["days"] = 730
            await show_coin_selection(update)
            return SELECT_COINS
        elif "Custom" in text:
            await update.message.reply_text("Enter number of days (e.g., 45, 120, 180):", reply_markup=None)
            return SELECT_DAYS_CUSTOM
        else:
            await update.message.reply_text("Select from the options")
            return SELECT_DATE_DAYS
    
    if mode == "SURGICAL":
        if text == "📅 Custom Date":
            await update.message.reply_text("Enter date (YYYY-MM-DD, e.g., 2026-04-01):", reply_markup=None)
            return SELECT_DATE_CUSTOM
        elif len(text) == 10 and text[4] == "-" and text[7] == "-":
            try:
                datetime.strptime(text, "%Y-%m-%d")
                user_config["scan_date"] = text
                await show_coin_selection(update)
                return SELECT_COINS
            except:
                await update.message.reply_text("❌ Invalid date.")
                return SELECT_DATE_DAYS
        else:
            await update.message.reply_text("Select a date from buttons")
            return SELECT_DATE_DAYS
    
    await update.message.reply_text("❌ Session expired. Start /config again.")
    return ConversationHandler.END


async def cmd_config_date_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle custom date input"""
    text = update.message.text.strip()
    try:
        date = datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
        # Check not in future
        if datetime.strptime(date, "%Y-%m-%d").date() > datetime.now().date():
            await update.message.reply_text("❌ Cannot scan future dates.")
            return SELECT_DATE_CUSTOM
        user_config["scan_date"] = date
        await show_coin_selection(update)
        return SELECT_COINS
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid format. Use YYYY-MM-DD (e.g., 2026-04-01)"
        )
        return SELECT_DATE_CUSTOM


async def cmd_config_days_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle custom days input"""
    text = update.message.text.strip()
    try:
        days = int(text)
        if days < 1 or days > 730:
            await update.message.reply_text("❌ Days must be between 1 and 730")
            return SELECT_DAYS_CUSTOM
        user_config["days"] = days
        await show_coin_selection(update)
        return SELECT_COINS
    except ValueError:
        await update.message.reply_text("❌ Enter a valid number (1-365)")
        return SELECT_DAYS_CUSTOM


async def show_coin_selection(update: Update):
    """Show coin selection with presets"""
    keyboard = [
        [KeyboardButton(k)] for k in COIN_PRESETS.keys()
    ]
    keyboard.append([KeyboardButton("✏️ Custom")])
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    
    presets_text = "\n".join([f"• {k}: {', '.join(v)}" for k, v in COIN_PRESETS.items()])
    
    await update.message.reply_text(
        f"🪙 *Select Coins*\n\n"
        f"{presets_text}\n"
        f"• ✏️ Custom - Enter your own\n\n"
        f"Default: {', '.join(COINS)}",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def cmd_config_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle coins selection with presets"""
    text = update.message.text.strip()
    
    if text in COIN_PRESETS:
        user_config["coins"] = COIN_PRESETS[text]
    else:
        coins = [c.strip().upper() for c in text.split(",") if c.strip()]
        user_config["coins"] = coins if coins else COINS
    
    # Show config and run immediately
    mode = user_config.get("mode", "LIVE")
    date = user_config.get("scan_date", "N/A")
    days = user_config.get("days", "N/A")
    coins_list = user_config.get("coins", COINS)
    
    mode_emoji = {"LIVE": "🔴", "HISTORICAL": "🟡", "SURGICAL": "🔵"}.get(mode, "⚪")
    
    await update.message.reply_text(
        f"📋 *Configuration*\n\n"
        f"Mode  : {mode_emoji} {mode}\n"
        f"Date  : {date}\n"
        f"Days  : {days}\n"
        f"Coins : {', '.join(coins_list)}\n\n"
        f"🚀 Starting scan...",
        parse_mode="Markdown"
    )
    
    await cmd_run(update, None)
    return ConversationHandler.END


# Placeholder - not used anymore


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run scan with user-configured or default settings"""
    mode = user_config.get("mode") or os.getenv("MODE", "SURGICAL")
    user_scan_date = user_config.get("scan_date")
    user_days = user_config.get("days")
    coins = user_config.get("coins") or COINS
    
    async def reply(msg: str):
        await update.message.reply_text(msg)
    
    # Clear cache
    _4h_cache.clear()
    
    if mode == "HISTORICAL":
        # Check if test period was selected (has end_date set)
        user_scan_date = user_config.get("scan_date")  # This is used as end_date for test periods
        
        if user_scan_date and user_days:
            # Test data preset selected - use date range scan
            await run_historical_scan(reply, days=user_days, end_date=user_scan_date, coins=coins, force_historical=True)
        elif user_scan_date:
            # Regular historical scan
            days_to_use = user_days if user_days else HISTORICAL_DAYS
            await run_historical_scan(reply, days=days_to_use, coins=coins, force_historical=True)
        else:
            # Regular historical scan
            days_to_use = user_days if user_days else HISTORICAL_DAYS
            await run_historical_scan(reply, days=days_to_use, coins=coins, force_historical=True)
    elif mode == "SURGICAL":
        # For surgical: use user_scan_date if set, else env default
        date_to_use = user_scan_date if user_scan_date else SCAN_DATE
        if date_to_use:
            await run_historical_scan(reply, scan_date=date_to_use, coins=coins, force_historical=False)
        else:
            await reply("❌ No date configured. Set SCAN_DATE in env or use /config.")
    else:  # LIVE
        await reply("🔴 Starting LIVE scan...")
        await run_live_scan_with_custom_coins(reply, coins)


async def run_live_scan_with_custom_coins(send_func, coins: list):
    """Run live scan with custom coin list"""
    messages = []
    
    with ThreadPoolExecutor(max_workers=len(coins)) as pool:
        futures = {pool.submit(scan_coin_live, s): s for s in coins}
        raw_results = [f.result() for f in futures]
    
    for r in raw_results:
        if r["error"]:
            continue
        if r["score"] < WEAK_SIGNAL_THRESHOLD:
            continue
        if not _position_semaphore.acquire(blocking=False):
            continue
        
        msg = format_signal_message(r["symbol"], r["score"], r["reason"], r["price"], r["direction"])
        if msg:
            await send_func(msg)
            messages.append(msg)
            await asyncio.sleep(0.3)
    
    if not messages:
        await send_func("📭 No live signals above threshold.")


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Scanning …")

    async def reply(msg: str):
        await update.message.reply_text(msg)

    if SCAN_DATE:
        await run_historical_scan(reply)
    else:
        found = await run_live_scan(reply)
        if not found:
            await update.message.reply_text("📭 No signals above threshold right now.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start   — bot status\n"
        "/scan    — run scan with env config\n"
        "/config  — interactive config (mode, date, coins)\n"
        "/run     — run scan with configured settings\n"
        "/daily   — today's P&L summary\n"
        "/help    — this message"
    )


async def cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import sqlite3
    from trade_logger import DB_PATH
    today = datetime.utcnow().strftime("%Y-%m-%d")
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT result, pnl_usd_after_fee, direction FROM backtest_trades WHERE date=?",
            (today,),
        ).fetchall()
        conn.close()
    except Exception as exc:
        await update.message.reply_text(f"❌ DB error: {exc}")
        return

    wins      = sum(1 for r in rows if r[1] and r[1] > 0)
    total_pnl = sum(r[1] or 0 for r in rows)
    longs     = sum(1 for r in rows if r[2] == "LONG")
    shorts    = sum(1 for r in rows if r[2] == "SHORT")

    await update.message.reply_text(
        f"📊 Today ({today})\n"
        f"Trades : {len(rows)} (🔺{longs} long | 🔻{shorts} short)\n"
        f"Wins   : {wins}\n"
        f"P&L    : ${total_pnl:.2f}"
    )


async def scheduled_scan(context: ContextTypes.DEFAULT_TYPE):
    async def send_to_chat(msg: str):
        await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)
    await run_live_scan(send_to_chat)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Conversation handler for /config
    config_conv = ConversationHandler(
        entry_points=[CommandHandler("config", cmd_config_start)],
        states={
            SELECT_MODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_config_mode)],
            SELECT_DATE_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_config_date_days)],
            SELECT_DAYS_CUSTOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_config_days_custom)],
            SELECT_DATE_CUSTOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_config_date_custom)],
            SELECT_COINS: [MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_config_coins)],
        },
        fallbacks=[CommandHandler("cancel", cmd_config_start)],
    )
    
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("scan",  cmd_scan))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(CommandHandler("daily", cmd_daily))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(config_conv)

    mode = os.getenv("MODE", "SURGICAL")
    if mode == "LIVE" and not SCAN_DATE:
        app.job_queue.run_repeating(
            scheduled_scan,
            interval=SCAN_INTERVAL_SECONDS,
            first=10,
        )
        logger.info("Scheduled live scan registered")

    logger.info(f"Bot starting | MODE={mode} | SCAN_DATE={SCAN_DATE or '(none)'}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
