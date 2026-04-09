import asyncio
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from config import (TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, COINS,
                    TIMEFRAME, CANDLES_NEEDED, SCAN_INTERVAL_SECONDS, SIMULATION_MODE,
                    STRONG_SIGNAL_THRESHOLD, WEAK_SIGNAL_THRESHOLD,
                    HISTORIC_MODE, LIVE_MODE, HYBRID_MODE, HISTORICAL_DAYS, LEVERAGE,
                    TP_PERCENT, SL_PERCENT, FEE_PERCENT, BUY_AMOUNT, MAX_HOLD_CANDLES)

MAX_OPEN_TRADES = 3
from data_fetcher import fetch_ohlcv
from scan_engine import scan_daily_historical, check_4h_trend
from signal_formatter import format_signal_message
from trade_logger import init_db, log_signal
from concurrent.futures import ThreadPoolExecutor
from indicators import compute_indicators
from scorer import calculate_score
from config import (TRADE_HOURS_START, TRADE_HOURS_END)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def scan_coin(symbol: str) -> dict:
    try:
        from datetime import datetime
        current_hour = datetime.utcnow().hour
        if current_hour < TRADE_HOURS_START or current_hour >= TRADE_HOURS_END:
            return {"symbol": symbol, "score": 0, "reason": "outside_trade_hours", "price": 0, "error": None}
        
        if not check_4h_trend(symbol):
            return {"symbol": symbol, "score": 0, "reason": "4h_bearish", "price": 0, "error": None}
        
        df = fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=CANDLES_NEEDED)
        df = compute_indicators(df)
        score, reason = calculate_score(df)
        
        if score < WEAK_SIGNAL_THRESHOLD or "low_volume" in reason:
            return {"symbol": symbol, "score": 0, "reason": reason, "price": 0, "error": None}
        
        price = df.iloc[-1]['close']
        return {"symbol": symbol, "score": score, "reason": reason, "price": price, "error": None}
    except Exception as e:
        return {"symbol": symbol, "score": 0, "reason": "", "price": 0, "error": str(e)}

def calculate_summary(results: list) -> dict:
    if not results:
        return {"total": 0, "tp": 0, "sl": 0, "profit": 0, "loss": 0, "win_rate": 0, "total_pnl": 0, "total_pnl_usd": 0}
    
    tp = sum(1 for r in results if r['result'] == "TP HIT")
    sl = sum(1 for r in results if r['result'] == "SL HIT")
    profit = sum(1 for r in results if r['result'] == "PROFIT")
    loss = sum(1 for r in results if r['result'] == "LOSS")
    wins = tp + profit
    total = len(results)
    
    total_pnl_usd = sum(r['pnl_usd_after_fee'] for r in results)
    
    return {
        "total": total,
        "tp": tp,
        "sl": sl,
        "profit": profit,
        "loss": loss,
        "win_rate": round((wins / total) * 100, 1) if total > 0 else 0,
        "total_pnl": round(sum(r['pnl_after_fee'] for r in results), 2),
        "total_pnl_usd": round(total_pnl_usd, 2)
    }

async def run_historical_scan(send_func):
    logger.info(f"Starting HISTORICAL scan for {HISTORICAL_DAYS} days...")
    await send_func(f"📊 Starting Historical Scan\n🔄 {HISTORICAL_DAYS} days, {LEVERAGE}x, ${BUY_AMOUNT}")
    
    symbol = COINS[0]
    results = scan_daily_historical(symbol, HISTORICAL_DAYS)
    
    if not results:
        await send_func("❌ No scan results generated")
        return
    
    results = results[:100]
    
    summary = calculate_summary(results)
    
    msg = f"📊 BTC BACKTEST SUMMARY ({HISTORICAL_DAYS} days)\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"💰 Buy: ${BUY_AMOUNT} × {LEVERAGE}x = ${BUY_AMOUNT * LEVERAGE}\n"
    msg += f"📈 Total Trades: {summary['total']}\n"
    msg += f"✅ TP: {summary['tp']} | ❌ SL: {summary['sl']} | 🎯 Win: {summary['win_rate']}%\n"
    msg += f"💵 Total PnL: ${summary['total_pnl_usd']:.2f}\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    await send_func(msg)
    
    chunk_size = 10
    for i in range(0, len(results), chunk_size):
        chunk = results[i:i+chunk_size]
        msg = ""
        for r in chunk:
            emoji = "✅" if r['pnl_usd_after_fee'] > 0 else "❌"
            notional = r['buy_amount'] * r['leverage']
            qty = notional / r['entry']
            msg += f"{emoji} {r['date']} {r['entry_time']} → {r['exit_time']}\n"
            msg += f"📌 {r['entry']} → {r['exit']}\n"
            msg += f"🛑 SL:{r['sl']} | 🎯 TP:{r['tp']}\n"
            msg += f"📦 ${notional} | Qty:{qty:.4f} BTC | Hold:{r['hold_hours']}h\n"
            msg += f"💵 PnL: ${r['pnl_usd_after_fee']:.2f} ({r['pnl_after_fee']:.2f}%) | {r['result']}\n"
            msg += f"📊 Score:{r['score']} | {r['reason']}\n"
            msg += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        await send_func(msg)
    
    logger.info(f"Historical scan complete: {summary['total']} trades, PnL: ${summary['total_pnl_usd']}")

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
                tp = round(price * (1 + TP_PERCENT/100), 4)
                sl = round(price * (1 - SL_PERCENT/100), 4)
                log_signal(symbol, score, reason, price, tp, sl)
    
    return messages

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = "SIMULATION" if SIMULATION_MODE else "LIVE"
    mode_info = []
    if HISTORIC_MODE: mode_info.append("HISTORICAL")
    if LIVE_MODE: mode_info.append("LIVE")
    if HYBRID_MODE: mode_info.append("HYBRID")
    mode_str = ", ".join(mode_info) if mode_info else "LIVE"
    
    await update.message.reply_text(
        f"✅ CryptoSignalBot\n"
        f"Mode: {mode}\n"
        f"Type: {mode_str}\n"
        f"Leverage: {LEVERAGE}x\n"
        f"Historical Days: {HISTORICAL_DAYS}\n"
        f"Max Hold: {MAX_HOLD_CANDLES * 15 // 60}h\n"
        f"Coins: {COINS}"
    )

async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Scanning...")
    
    async def reply(msg: str):
        await update.message.reply_text(msg)

    if HISTORIC_MODE:
        await run_historical_scan(reply)
    else:
        found = await run_live_scan(reply)
        if not found:
            await update.message.reply_text("No signals above threshold.")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start — status\n"
        "/scan — scan now\n"
        "/help — this"
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
    
    if not HISTORIC_MODE:
        app.job_queue.run_repeating(scheduled_scan, interval=SCAN_INTERVAL_SECONDS, first=10)
    
    logger.info(f"Bot started. Historic:{HISTORIC_MODE}, Live:{LIVE_MODE}, Hybrid:{HYBRID_MODE}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()