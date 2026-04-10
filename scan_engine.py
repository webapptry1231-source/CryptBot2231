import logging
from data_fetcher import fetch_historical_ohlcv
from indicators import compute_indicators
from scorer import calculate_score
from config import (TIMEFRAME, TIMEFRAME_4H, MAX_HOLD_CANDLES, WEAK_SIGNAL_THRESHOLD,
                   TP_PERCENT, SL_PERCENT, LEVERAGE, BUY_AMOUNT,
                   DAILY_TRADE_CAP, SIGNAL_COOLDOWN_HOURS, TRADE_HOURS_START, TRADE_HOURS_END,
                   SL_OVERRIDES, MAX_CONCURRENT_TRADES,
                   PARTIAL_TP_PERCENT, PARTIAL_TP_SIZE, TRAILING_STOP_PERCENT, FEE_PERCENT)

logger = logging.getLogger(__name__)

def check_4h_trend(symbol: str) -> bool:
    try:
        df_4h = fetch_historical_ohlcv(symbol, timeframe=TIMEFRAME_4H, days_back=30)
        if len(df_4h) < 50:
            return True
        df_4h = compute_indicators(df_4h)
        latest = df_4h.iloc[-1]
        ema_val = latest['EMA_200']
        if hasattr(ema_val, 'iloc'):
            ema_val = ema_val.iloc[-1]
        close_val = latest['close']
        if hasattr(close_val, 'iloc'):
            close_val = close_val.iloc[-1]
        result = close_val > ema_val
        logger.info(f"  4h check: {len(df_4h)} candles, EMA200={ema_val:.2f}, close={close_val:.2f} -> {'BULLISH' if result else 'BEARISH'}")
        return result
    except Exception as e:
        logger.warning(f"4h trend check failed: {e}")
        return True

def scan_daily_historical(symbol: str, days: int) -> list:
    try:
        logger.info(f"=== Starting scan for {symbol}, {days} days ===")
        df = fetch_historical_ohlcv(symbol, timeframe=TIMEFRAME, days_back=days)
        logger.info(f"Fetched {len(df)} candles")
        
        if len(df) < 100:
            logger.warning(f"Not enough data: {len(df)}")
            return []
        
        df = compute_indicators(df)
        logger.info(f"Indicators computed: {len(df)} rows")
        
        results = []
        total_candles = len(df)
        
        trend_bullish = check_4h_trend(symbol)
        
        trades_per_day = {}
        last_signal_time = {}
        total_scanned = 0
        signals_found = 0
        scan_start_time = df.index[0]
        
        logger.info(f"Scan config: WEAK_THRESHOLD={WEAK_SIGNAL_THRESHOLD}, COOLDOWN={SIGNAL_COOLDOWN_HOURS}, DAILY_CAP={DAILY_TRADE_CAP}")
        
        for i in range(24, total_candles - MAX_HOLD_CANDLES):
            current_time = df.index[i]
            hours_elapsed = (current_time - scan_start_time).total_seconds() / 3600
            
            if hours_elapsed >= 4:
                trend_bullish = check_4h_trend(symbol)
                scan_start_time = current_time
            
            total_scanned += 1
            
            if total_scanned % 200 == 0:
                logger.info(f"Progress: {total_scanned}/{total_candles} candles, signals: {signals_found}")
            
            if not trend_bullish:
                continue
            
            window = df.iloc[:i]
            if len(window) < 50:
                continue
            
            entry_time = window.index[-1]
            entry_hour = entry_time.hour
            
            score, reason = calculate_score(window)
            
            if total_scanned < 50:
                logger.info(f"Candle {i} [{entry_time}]: score={score}, reason='{reason}'")
            
            if score < WEAK_SIGNAL_THRESHOLD:
                continue
            
            if "low_volume" in reason:
                logger.info(f"Candle {i}: low_volume blocked (score={score})")
                continue
            
            signals_found += 1
            last_signal_time[symbol] = entry_time
            
            entry_price = window.iloc[-1]['close']
            entry_price = entry_price * 1.001
            
            hold_candles = min(MAX_HOLD_CANDLES, total_candles - i - 1)
            if hold_candles < 1:
                logger.warning(f"Candle {i}: no future candles to evaluate")
                continue
            
            future = df.iloc[i:i+hold_candles]
            if len(future) == 0:
                logger.warning(f"Candle {i}: empty future data")
                continue
            
            mfe = future['high'].max()
            mae = future['low'].min()
            mfe_pct = ((mfe - entry_price) / entry_price) * 100
            mae_pct = ((entry_price - mae) / entry_price) * 100
            
            sl_percent = SL_OVERRIDES.get(symbol, SL_PERCENT)
            tp_price = entry_price * (1 + TP_PERCENT/100)
            sl_price = entry_price * (1 - sl_percent/100)
            
            partial_tp_hit = False
            partial_tp_price = entry_price * (1 + PARTIAL_TP_PERCENT/100)
            trailing_sl = sl_price
            running_high = entry_price
            trade_closed = False
            exit_price = entry_price
            exit_time = entry_time
            pnl_pct = 0
            result = "PENDING"
            
            for j in range(len(future)):
                candle = future.iloc[j]
                running_high = max(running_high, candle['high'])
                
                if not partial_tp_hit and candle['high'] >= partial_tp_price:
                    partial_tp_hit = True
                    trailing_sl = entry_price
                
                if partial_tp_hit:
                    new_trail = running_high * (1 - TRAILING_STOP_PERCENT/100)
                    trailing_sl = max(trailing_sl, new_trail)
                
                if partial_tp_hit and candle['high'] >= tp_price:
                    pnl_pct = TP_PERCENT
                    result = "TP HIT"
                    exit_price = future.iloc[j]['high']
                    exit_time = future.index[j]
                    break
                
                if candle['low'] <= trailing_sl:
                    if partial_tp_hit:
                        pnl_pct = ((exit_price - entry_price) / entry_price) * 100 * LEVERAGE
                        result = "TRAIL STOP"
                    else:
                        pnl_pct = -SL_PERCENT
                        result = "SL HIT"
                    exit_price = candle['low']
                    exit_time = future.index[j]
                    break
            
            if not trade_closed:
                if exit_price > entry_price:
                    pnl_pct = ((exit_price - entry_price) / entry_price) * 100 * LEVERAGE
                    result = "PROFIT"
                else:
                    pnl_pct = ((exit_price - entry_price) / entry_price) * 100 * LEVERAGE
                    result = "LOSS"
            
            hold_hours = (exit_time - entry_time).total_seconds() / 3600
            
            fee_pct = FEE_PERCENT * 2
            pnl_after_fee = pnl_pct - fee_pct
            
            pnl_usd = (BUY_AMOUNT * LEVERAGE) * (pnl_pct / 100)
            pnl_usd_after_fee = (BUY_AMOUNT * LEVERAGE) * (pnl_after_fee / 100)
            
            day_date = str(entry_time)[:10]
            entry_time_str = str(entry_time)[11:16]
            exit_time_str = str(exit_time)[11:16]
            
            logger.info(f"*** TRADE GENERATED: {symbol} {day_date} {entry_time_str}")
            logger.info(f"    Score: {score}, Reason: {reason}")
            logger.info(f"    Entry: {entry_price:.2f}, Exit: {exit_price:.2f}, TP: {tp_price:.2f}, SL: {sl_price:.2f}")
            logger.info(f"    Result: {result}, PnL: {pnl_usd_after_fee:.2f}%, Hold: {hold_hours:.1f}h")
            
            results.append({
                "date": day_date,
                "entry_time": entry_time_str,
                "exit_time": exit_time_str,
                "score": score,
                "reason": reason,
                "entry": round(entry_price, 2),
                "exit": round(exit_price, 2),
                "tp": round(tp_price, 2),
                "sl": round(sl_price, 2),
                "result": result,
                "pnl_pct": round(pnl_pct, 2),
                "pnl_after_fee": round(pnl_after_fee, 2),
                "pnl_usd": round(pnl_usd, 2),
                "pnl_usd_after_fee": round(pnl_usd_after_fee, 2),
                "leverage": LEVERAGE,
                "buy_amount": BUY_AMOUNT,
                "hold_hours": round(hold_hours, 1),
                "mfe_pct": round(mfe_pct, 2),
                "mae_pct": round(mae_pct, 2)
            })
        
        logger.info(f"=== SCAN COMPLETE: {total_scanned} candles, {signals_found} passed, {len(results)} trades ===")
        return results
    except Exception as e:
        logger.error(f"Error in scan: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return []