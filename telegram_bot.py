import asyncio
import json
import logging
import os
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from config import (TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, COINS,
                    TIMEFRAME, CANDLES_NEEDED, SCAN_INTERVAL_SECONDS,
                    STRONG_SIGNAL_THRESHOLD, WEAK_SIGNAL_THRESHOLD,
                    HISTORICAL_DAYS, SCAN_DATE, LEVERAGE,
                    TP_LONG_PERCENT, TP_SHORT_PERCENT,
                    SL_LONG_PERCENT, SL_SHORT_PERCENT,
                    BUY_AMOUNT, MAX_HOLD_CANDLES)

MAX_OPEN_TRADES = 3
from data_fetcher import fetch_ohlcv
from scan_engine import scan_daily_historical, check_4h_trend, determine_regime
from signal_formatter import format_signal_message
from trade_logger import init_db, log_signal, log_backtest_trade
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
        
        if symbol != "BTC/USDT":
            btc_bullish = check_4h_trend("BTC/USDT")
            if not btc_bullish:
                return {"symbol": symbol, "score": 0, "reason": "btc_bearish_block", "price": 0, "error": None}
        
        df = fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=CANDLES_NEEDED)
        df = compute_indicators(df)
        
        direction = determine_regime(df)
        
        if direction == "NEUTRAL":
            return {"symbol": symbol, "score": 0, "reason": "neutral_regime", "price": 0, "error": None}
        
        score, reason = calculate_score(df, direction=direction)
        
        if score < WEAK_SIGNAL_THRESHOLD or "low_volume" in reason:
            return {"symbol": symbol, "score": 0, "reason": reason, "price": 0, "error": None}
        
        price = df.iloc[-1]['close']
        return {"symbol": symbol, "score": score, "reason": reason, "price": price, "direction": direction, "error": None}
    except Exception as e:
        return {"symbol": symbol, "score": 0, "reason": "", "price": 0, "error": str(e)}

def calculate_summary(results: list) -> dict:
    if not results:
        return {"total": 0, "tp": 0, "sl": 0, "profit": 0, "loss": 0, "win_rate": 0, "total_pnl": 0, "total_pnl_usd": 0, "long_wins": 0, "short_wins": 0}
    
    wins = sum(1 for r in results if r['pnl_usd_after_fee'] > 0)
    losses = sum(1 for r in results if r['pnl_usd_after_fee'] < 0)
    
    long_wins = sum(1 for r in results if r.get('direction') == "LONG" and r['pnl_usd_after_fee'] > 0)
    short_wins = sum(1 for r in results if r.get('direction') == "SHORT" and r['pnl_usd_after_fee'] > 0)
    
    tp = sum(1 for r in results if r['result'] == "TP HIT")
    sl = sum(1 for r in results if r['result'] == "SL HIT")
    trail = sum(1 for r in results if r['result'] == "TRAIL STOP")
    timeout = sum(1 for r in results if r['result'] == "TIMEOUT")
    
    total = len(results)
    total_pnl_usd = sum(r['pnl_usd_after_fee'] for r in results)
    
    return {
        "total": total,
        "tp": tp,
        "sl": sl,
        "trail": trail,
        "timeout": timeout,
        "wins": wins,
        "losses": losses,
        "long_wins": long_wins,
        "short_wins": short_wins,
        "win_rate": round((wins / total) * 100, 1) if total > 0 else 0,
        "total_pnl": round(sum(r['pnl_after_fee'] for r in results), 2),
        "total_pnl_usd": round(total_pnl_usd, 2)
    }

async def run_historical_scan(send_func):
    all_results = []
    
    if SCAN_DATE:
        logger.info(f"Starting SURGICAL scan for {SCAN_DATE}...")
        await send_func(f"📊 SURGICAL DAILY REPORT: {SCAN_DATE}\n🔄 {LEVERAGE}x, ${BUY_AMOUNT}")
        for symbol in COINS:
            results = scan_daily_historical(symbol, target_date=SCAN_DATE)
            all_results.extend(results)
    else:
        logger.info(f"Starting HISTORICAL scan for {HISTORICAL_DAYS} days...")
        await send_func(f"📊 Starting Historical Scan\n🔄 {HISTORICAL_DAYS} days, {LEVERAGE}x, ${BUY_AMOUNT}")
        for symbol in COINS:
            results = scan_daily_historical(symbol, days=HISTORICAL_DAYS)
            all_results.extend(results)
    
    results = all_results
    
    if not results:
        await send_func("❌ No scan results generated")
        return
    
    results = results[:500]
    summary = calculate_summary(results)
    
    if SCAN_DATE:
        msg = f"📊 SURGICAL REPORT: {SCAN_DATE}\n"
    else:
        msg = f"📊 BTC BACKTEST SUMMARY ({HISTORICAL_DAYS} days)\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"💰 Buy: ${BUY_AMOUNT} × {LEVERAGE}x = ${BUY_AMOUNT * LEVERAGE}\n"
    msg += f"📈 Total Trades: {summary['total']}\n"
    msg += f"✅ Wins: {summary.get('wins', summary['tp']+summary.get('trail',0))} | ❌ Losses: {summary.get('losses', summary['sl'])}\n"
    msg += f"🔺 Long Wins: {summary.get('long_wins', 0)} | 🔻 Short Wins: {summary.get('short_wins', 0)}\n"
    msg += f"🎯 Win Rate: {summary['win_rate']}%\n"
    msg += f"💵 Total PnL: ${summary['total_pnl_usd']:.2f}\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    
    for r in results:
        emoji = "✅" if r['pnl_usd_after_fee'] > 0 else "❌"
        direction_emoji = "🔺" if r.get('direction') == "LONG" else "🔻"
        notional = r['buy_amount'] * r['leverage']
        msg += f"{emoji}{direction_emoji} {r['date']} {r['entry_time']} → {r['exit_time']}\n"
        msg += f"📌 {r['entry']} → {r['exit']}\n"
        msg += f"🛑 SL:{r['sl']} | 🎯 TP:{r['tp']}\n"
        msg += f"📦 ${notional} | Hold:{r['hold_hours']}h\n"
        msg += f"💵 PnL: ${r['pnl_usd_after_fee']:.2f} ({r['pnl_after_fee']:.2f}%) | {r['result']}\n"
        msg += f"📊 Score:{r['score']} | {r['reason'][:50]}\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    
    await send_func(msg)
    
    logger.info(f"Historical scan complete: {summary['total']} trades, PnL: ${summary['total_pnl_usd']}")
    
    run_timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    
    os.makedirs("logs", exist_ok=True)
    with open(f"logs/backtest_{run_timestamp}.json", "w") as f:
        json.dump({
            "run_timestamp": run_timestamp,
            "days": HISTORICAL_DAYS,
            "leverage": LEVERAGE,
            "buy_amount": BUY_AMOUNT,
            "summary": summary,
            "trades": results
        }, f, indent=2)
    logger.info(f"Saved backtest results to logs/backtest_{run_timestamp}.json")
    
    for trade in results:
        log_backtest_trade(run_timestamp, trade)

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
            direction = r.get('direction', 'LONG')
            msg = format_signal_message(symbol, score, reason, price, direction)
            if msg:
                await send_func(msg)
                messages.append(msg)
                await asyncio.sleep(0.5)
                if direction == "LONG":
                    tp = round(price * (1 + TP_LONG_PERCENT / 100), 4)
                    sl = round(price * (1 - SL_LONG_PERCENT / 100), 4)
                else:
                    tp = round(price * (1 - TP_SHORT_PERCENT / 100), 4)
                    sl = round(price * (1 + SL_SHORT_PERCENT / 100), 4)
                log_signal(symbol, score, reason, price, tp, sl)
    
    return messages

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = os.getenv("MODE", "SURGICAL")
    await update.message.reply_text(
        f"✅ CryptoSignalBot\n"
        f"Mode: {mode}\n"
        f"Leverage: {LEVERAGE}x\n"
        f"Historical Days: {HISTORICAL_DAYS}\n"
        f"Max Hold: {MAX_HOLD_CANDLES * 15 // 60}h\n"
        f"Coins: {COINS}"
    )

async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Scanning...")
    
    async def reply(msg: str):
        await update.message.reply_text(msg)

    if SCAN_DATE:
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
    
    if SCAN_DATE:
        await run_historical_scan(send_to_chat)
    else:
        await run_live_scan(send_to_chat)

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("help", cmd_help))
    
    if not SCAN_DATE:
        app.job_queue.run_repeating(scheduled_scan, interval=SCAN_INTERVAL_SECONDS, first=10)
    
    logger.info(f"Bot started. Mode: {os.getenv('MODE', 'SURGICAL')}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()