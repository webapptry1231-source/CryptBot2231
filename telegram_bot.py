import asyncio
import csv
import json
import logging
import os
from datetime import datetime

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

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

async def run_historical_scan(send_func):
    # Clear 4h cache once before scanning all symbols
    _4h_cache.clear()

    all_results: list = []

    if SCAN_DATE:
        logger.info(f"SURGICAL scan: {SCAN_DATE}")
        await send_func(
            f"📊 Starting Surgical Scan\n"
            f"📅 Date: {SCAN_DATE}\n"
            f"🔄 {LEVERAGE}x | ${BUY_AMOUNT}/trade\n"
            f"🪙 Coins: {', '.join(COINS)}"
        )
        for symbol in COINS:
            results = scan_daily_historical(symbol, target_date=SCAN_DATE)
            all_results.extend(results)
    else:
        logger.info(f"HISTORICAL scan: {HISTORICAL_DAYS} days")
        await send_func(
            f"📊 Starting Historical Scan\n"
            f"🔄 {HISTORICAL_DAYS} days | {LEVERAGE}x | ${BUY_AMOUNT}/trade\n"
            f"🪙 Coins: {', '.join(COINS)}"
        )
        for symbol in COINS:
            results = scan_daily_historical(symbol, days=HISTORICAL_DAYS)
            all_results.extend(results)

    if not all_results:
        await send_func("❌ No signals generated — check logs for details")
        return

    # Cap at 500 to avoid Telegram flood
    all_results = all_results[:500]
    summary = calculate_summary(all_results)

    # ── Summary message ───────────────────────────────────────────────────────
    header = f"📊 {'SURGICAL REPORT: ' + SCAN_DATE if SCAN_DATE else f'BACKTEST ({HISTORICAL_DAYS}d)'}\n"
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
    )


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
        "/start  — bot status\n"
        "/scan   — run scan now\n"
        "/daily  — today's P&L summary\n"
        "/help   — this message"
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

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("scan",  cmd_scan))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(CommandHandler("daily", cmd_daily))

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
