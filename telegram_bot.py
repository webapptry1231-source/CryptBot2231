import asyncio
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from config import (TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, COINS,
                    TIMEFRAME, CANDLES_NEEDED, SCAN_INTERVAL_SECONDS, SIMULATION_MODE,
                    STRONG_SIGNAL_THRESHOLD, WEAK_SIGNAL_THRESHOLD,
                    HISTORIC_MODE, LIVE_MODE, HYBRID_MODE, HISTORICAL_DAYS)
from data_fetcher import fetch_ohlcv, fetch_historical_ohlcv
from indicators import compute_indicators
from scorer import calculate_score
from signal_formatter import format_signal_message
from trade_logger import init_db, log_signal
from concurrent.futures import ThreadPoolExecutor
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def scan_coin(symbol: str) -> dict:
    try:
        df = fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=CANDLES_NEEDED)
        df = compute_indicators(df)
        score, reason = calculate_score(df)
        price = df.iloc[-1]['close']
        return {"symbol": symbol, "score": score, "reason": reason, "price": price, "error": None}
    except Exception as e:
        return {"symbol": symbol, "score": 0, "reason": "", "price": 0, "error": str(e)}

def scan_coin_historical(symbol: str, days: int) -> dict:
    try:
        df = fetch_historical_ohlcv(symbol, timeframe=TIMEFRAME, days_back=days)
        df = compute_indicators(df)
        
        results = []
        for i in range(50, len(df) - 1):
            window = df.iloc[:i]
            score, reason = calculate_score(window)
            price = window.iloc[-1]['close']
            results.append({"score": score, "reason": reason, "price": price})
        
        if results:
            best = max(results, key=lambda x: x['score'])
            return {"symbol": symbol, "best_score": best['score'], "best_reason": best['reason'], "price": best['price'], "total_signals": len([r for r in results if r['score'] >= WEAK_SIGNAL_THRESHOLD]), "error": None}
        return {"symbol": symbol, "best_score": 0, "best_reason": "", "price": 0, "total_signals": 0, "error": "No data"}
    except Exception as e:
        return {"symbol": symbol, "best_score": 0, "best_reason": "", "price": 0, "total_signals": 0, "error": str(e)}

async def run_historical_scan(send_func):
    logger.info(f"Starting HISTORICAL scan for {HISTORICAL_DAYS} days...")
    await send_func(f"📊 Running Historical Scan ({HISTORICAL_DAYS} days)...")
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(scan_coin_historical, symbol, HISTORICAL_DAYS): symbol for symbol in COINS}
        results = [f.result() for f in futures]
    
    msg = "📊 HISTORICAL SCAN RESULTS\n" + "="*30 + "\n\n"
    for r in sorted(results, key=lambda x: x['best_score'], reverse=True):
        if r['error']:
            msg += f"❌ {r['symbol']}: {r['error']}\n"
        else:
            msg += f"🔹 {r['symbol']}: Best Score={r['best_score']} | Signals={r['total_signals']} | Price={r['price']}\n"
            msg += f"   Reason: {r['best_reason']}\n\n"
    
    await send_func(msg)
    logger.info(f"Historical scan complete: {len(results)} coins scanned")

async def run_live_scan(send_func) -> list[str]:
    messages = []
    logger.info("Starting LIVE market scan...")
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(scan_coin, symbol): symbol for symbol in COINS}
        results = [f.result() for f in futures]
    
    for r in results:
        if r['error']:
            logger.error(f"Error scanning {r['symbol']}: {r['error']}")
            continue
        
        score = r['score']
        reason = r['reason']
        price = r['price']
        symbol = r['symbol']
        
        logger.info(f"RESULT: {symbol} - score={score}, reason={reason}, price={price}")
        
        if score >= WEAK_SIGNAL_THRESHOLD:
            logger.info(f"SIGNAL: {symbol} qualifies with score {score}")
            msg = format_signal_message(symbol, score, reason, price)
            if msg:
                await send_func(msg)
                messages.append(msg)
                tp = round(price * 1.01, 4)
                sl = round(price * 0.995, 4)
                log_signal(symbol, score, reason, price, tp, sl)
    
    return messages

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = "SIMULATION" if SIMULATION_MODE else "LIVE"
    mode_info = []
    if HISTORIC_MODE: mode_info.append("HISTORICAL")
    if LIVE_MODE: mode_info.append("LIVE")
    if HYBRID_MODE: mode_info.append("HYBRID")
    mode_str = ", ".join(mode_info) if mode_info else "NONE"
    
    await update.message.reply_text(
        f"✅ CryptoSignalBot is running\n"
        f"Mode: {mode}\n"
        f"Scan Type: {mode_str}\n"
        f"Timeframe: {TIMEFRAME}\n"
        f"Coins: {len(COINS)}\n"
        f"Historical Days: {HISTORICAL_DAYS}"
    )

async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Scanning all coins...")

    async def reply(msg: str):
        await update.message.reply_text(msg)

    if HISTORIC_MODE:
        await run_historical_scan(reply)
    else:
        found = await run_live_scan(reply)
        if not found:
            await update.message.reply_text("No signals above threshold. Market may be quiet.")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start — bot status\n"
        "/scan — scan all coins now\n"
        "/help — this message\n"
        f"\nAuto-scan every {SCAN_INTERVAL_SECONDS//60} minutes."
    )

async def scheduled_scan(context: ContextTypes.DEFAULT_TYPE):
    bot = context.bot

    async def send_to_chat(msg: str):
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)

    if HISTORIC_MODE:
        await run_historical_scan(send_to_chat)
    else:
        await run_live_scan(send_to_chat)

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("help", cmd_help))

    if HYBRID_MODE:
        logger.info("HYBRID MODE: Will run historical on first scan, then live")
    
    if not HISTORIC_MODE:
        app.job_queue.run_repeating(scheduled_scan, interval=SCAN_INTERVAL_SECONDS, first=10)

    logger.info(f"Bot started. Modes - Historic:{HISTORIC_MODE}, Live:{LIVE_MODE}, Hybrid:{HYBRID_MODE}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()