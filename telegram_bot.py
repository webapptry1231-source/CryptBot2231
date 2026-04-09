import asyncio
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from config import (TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, COINS,
                    TIMEFRAME, CANDLES_NEEDED, SCAN_INTERVAL_SECONDS, SIMULATION_MODE)
from data_fetcher import fetch_ohlcv
from indicators import compute_indicators
from scorer import calculate_score
from signal_formatter import format_signal_message
from trade_logger import init_db, log_signal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def scan_all_coins(send_func) -> list[str]:
    messages = []
    for symbol in COINS:
        try:
            df = fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=CANDLES_NEEDED)
            df = compute_indicators(df)
            score, reason = calculate_score(df)
            price = df.iloc[-1]['close']
            msg = format_signal_message(symbol, score, reason, price)
            if msg:
                await send_func(msg)
                messages.append(msg)
                tp = round(price * 1.01, 4)
                sl = round(price * 0.995, 4)
                log_signal(symbol, score, reason, price, tp, sl)
        except Exception as e:
            logger.error(f"Error scanning {symbol}: {e}")
            logger.info(f"Traceback:", exc_info=True)
        else:
            logger.info(f"RESULT: {symbol} - score={score}, reason={reason}, price={price}")
            if score >= WEAK_SIGNAL_THRESHOLD:
                logger.info(f"SIGNAL: {symbol} qualifies with score {score}")
    return messages

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = "SIMULATION" if SIMULATION_MODE else "LIVE"
    await update.message.reply_text(
        f"✅ CryptoSignalBot is running\n"
        f"Mode: {mode}\n"
        f"Timeframe: {TIMEFRAME}\n"
        f"Coins: {len(COINS)}\n"
        f"Use /scan to scan all coins now."
    )

async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Scanning all coins...")

    async def reply(msg: str):
        await update.message.reply_text(msg)

    found = await scan_all_coins(reply)
    if not found:
        await update.message.reply_text("No signals above threshold. Market may be quiet.")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start — bot status\n"
        "/scan — scan all coins now\n"
        "/help — this message\n"
        "\nBot auto-scans every 15 minutes."
    )

async def scheduled_scan(context: ContextTypes.DEFAULT_TYPE):
    bot = context.bot

    async def send_to_chat(msg: str):
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)

    await scan_all_coins(send_to_chat)

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("help", cmd_help))

    app.job_queue.run_repeating(scheduled_scan, interval=SCAN_INTERVAL_SECONDS, first=10)

    logger.info("Bot started. Polling for updates...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()