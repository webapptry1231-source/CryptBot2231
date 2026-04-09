import asyncio
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from config import (TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, COINS,
                    TIMEFRAME, CANDLES_NEEDED, SCAN_INTERVAL_SECONDS, SIMULATION_MODE,
                    STRONG_SIGNAL_THRESHOLD, WEAK_SIGNAL_THRESHOLD,
                    HISTORIC_MODE, LIVE_MODE, HYBRID_MODE, HISTORICAL_DAYS, LEVERAGE,
                    TP_PERCENT, SL_PERCENT, FEE_PERCENT, BUY_AMOUNT)
from data_fetcher import fetch_ohlcv, fetch_historical_ohlcv
from indicators import compute_indicators
from scorer import calculate_score
from signal_formatter import format_signal_message
from trade_logger import init_db, log_signal
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import pandas as pd

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

def scan_daily_historical(symbol: str, days: int) -> list:
    try:
        df = fetch_historical_ohlcv(symbol, timeframe=TIMEFRAME, days_back=days)
        df = compute_indicators(df)
        
        results = []
        days_checked = min(days, len(df) - 24)
        
        for i in range(24, len(df) - 24):
            day_idx = i // 24
            if day_idx >= days_checked:
                break
            
            window = df.iloc[:i]
            if len(window) < 50:
                continue
                
            score, reason = calculate_score(window)
            entry_price = window.iloc[-1]['close']
            
            future = df.iloc[i:i+24]
            if len(future) == 0:
                continue
                
            high_24h = future['high'].max()
            low_24h = future['low'].min()
            close_24h = future.iloc[-1]['close']
            
            tp_price = entry_price * (1 + TP_PERCENT/100)
            sl_price = entry_price * (1 - SL_PERCENT/100)
            
            hit_tp = high_24h >= tp_price
            hit_sl = low_24h <= sl_price
            
            if hit_tp and not hit_sl:
                pnl_pct = TP_PERCENT
                result = "TP HIT"
            elif hit_sl and not hit_tp:
                pnl_pct = -SL_PERCENT
                result = "SL HIT"
            elif close_24h > entry_price:
                pnl_pct = ((close_24h - entry_price) / entry_price) * 100 * LEVERAGE
                result = "PROFIT"
            else:
                pnl_pct = ((close_24h - entry_price) / entry_price) * 100 * LEVERAGE
                result = "LOSS"
            
            fee = (TP_PERCENT + SL_PERCENT) / 2 * LEVERAGE * 0.001
            pnl_after_fee = pnl_pct - fee
            
            pnl_usd = (BUY_AMOUNT * LEVERAGE) * (pnl_pct / 100)
            pnl_usd_after_fee = (BUY_AMOUNT * LEVERAGE) * (pnl_after_fee / 100)
            
            day_date = str(window.index[-1])[:10]
            entry_time = str(window.index[-1])[11:16]
            results.append({
                "date": day_date,
                "time": entry_time,
                "score": score,
                "reason": reason,
                "entry": round(entry_price, 2),
                "exit": round(close_24h, 2),
                "tp": round(tp_price, 2),
                "sl": round(sl_price, 2),
                "result": result,
                "pnl_pct": round(pnl_pct, 2),
                "pnl_after_fee": round(pnl_after_fee, 2),
                "pnl_usd": round(pnl_usd, 2),
                "pnl_usd_after_fee": round(pnl_usd_after_fee, 2),
                "leverage": LEVERAGE,
                "buy_amount": BUY_AMOUNT
            })
        
        return results
    except Exception as e:
        logger.error(f"Error in daily scan: {e}")
        return []

def calculate_summary(results: list) -> dict:
    if not results:
        return {"total": 0, "tp": 0, "sl": 0, "profit": 0, "loss": 0, "win_rate": 0, "total_pnl": 0, "total_pnl_usd": 0}
    
    tp = sum(1 for r in results if r['result'] == "TP HIT")
    sl = sum(1 for r in results if r['result'] == "SL HIT")
    profit = sum(1 for r in results if r['result'] == "PROFIT")
    loss = sum(1 for r in results if r['result'] == "LOSS")
    wins = tp + profit
    total = len(results)
    
    total_pnl_usd = sum(r['pnl_usd'] for r in results)
    
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
    
    summary = calculate_summary(results)
    
    msg = f"📊 BTC DAILY BACKTEST ({HISTORICAL_DAYS} days)\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"💰 Buy Amount: ${BUY_AMOUNT} × {LEVERAGE}x = ${BUY_AMOUNT * LEVERAGE}\n"
    msg += f"📈 Total Trades: {summary['total']}\n"
    msg += f"✅ TP Hit: {summary['tp']} | ❌ SL Hit: {summary['sl']}\n"
    msg += f"💵 Total PnL: {summary['total_pnl']:.2f}% | ${summary['total_pnl_usd']}\n"
    msg += f"🎯 Win Rate: {summary['win_rate']}%\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    daily_pnl = {}
    for r in results:
        if r['date'] not in daily_pnl:
            daily_pnl[r['date']] = {"count": 0, "pnl": 0}
        daily_pnl[r['date']]["count"] += 1
        daily_pnl[r['date']]["pnl"] += r['pnl_usd_after_fee']
    
    for date, data in sorted(daily_pnl.items(), reverse=True)[:15]:
        emoji = "✅" if data['pnl'] > 0 else "❌"
        msg += f"{emoji} {date}: {data['count']} trades | PnL: ${data['pnl']:.2f}\n"
    
    msg += f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"📋 LAST 20 TRADES:\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    
    for r in results[-20:]:
        emoji = "✅" if r['pnl_usd_after_fee'] > 0 else "❌"
        msg += f"{emoji} {r['date']} {r['time']} | ${r['buy_amount']}×{r['leverage']}x | Entry:{r['entry']}\n"
        msg += f"   → Exit:{r['exit']} | {r['result']} | PnL:${r['pnl_usd_after_fee']:.2f}\n"
    
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
    
    logger.info(f"Bot started. Historic:{HISTORIC_MODE}, Live:{LIVE_MODE}, Hybrid:{HYBRID_MODE}, Days:{HISTORICAL_DAYS}, Leverage:{LEVERAGE}x")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()