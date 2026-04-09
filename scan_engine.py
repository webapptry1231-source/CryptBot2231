import logging
from datetime import datetime, timedelta
from data_fetcher import fetch_historical_ohlcv
from indicators import compute_indicators
from scorer import calculate_score
from config import (TIMEFRAME, TIMEFRAME_4H, MAX_HOLD_CANDLES, WEAK_SIGNAL_THRESHOLD,
                   TP_PERCENT, SL_PERCENT, LEVERAGE, BUY_AMOUNT,
                   DAILY_TRADE_CAP, SIGNAL_COOLDOWN_HOURS, TRADE_HOURS_START, TRADE_HOURS_END,
                   SL_OVERRIDES, MAX_CONCURRENT_TRADES,
                   PARTIAL_TP_PERCENT, PARTIAL_TP_SIZE, TRAILING_STOP_PERCENT, FEE_PERCENT)

logger = logging.getLogger(__name__)
MAX_OPEN_TRADES = 3

def check_4h_trend(symbol: str) -> bool:
    try:
        df_4h = fetch_historical_ohlcv(symbol, timeframe=TIMEFRAME_4H, days_back=7)
        if len(df_4h) < 200:
            return True
        df_4h = compute_indicators(df_4h)
        latest = df_4h.iloc[-1]
        return latest['close'] > latest['EMA_200']
    except Exception as e:
        logger.warning(f"4h trend check failed: {e}")
        return True

def scan_daily_historical(symbol: str, days: int) -> list:
    try:
        logger.info(f"Fetching {days} days of historical data for {symbol}")
        df = fetch_historical_ohlcv(symbol, timeframe=TIMEFRAME, days_back=days)
        logger.info(f"Fetched {len(df)} candles")
        
        df = compute_indicators(df)
        
        results = []
        total_candles = len(df)
        days_checked = min(days, total_candles - MAX_HOLD_CANDLES)
        
        trend_bullish = check_4h_trend(symbol)
        trend_check_interval = 16
        
        trades_per_day = {}
        last_signal_time = {}
        open_positions = set()
        total_concurrent = 0
        total_scanned = 0
        signals_found = 0
        
        for i in range(24, total_candles - MAX_HOLD_CANDLES):
            total_scanned += 1
            day_idx = i // 24
            if day_idx >= days_checked:
                break
            
            if total_concurrent >= MAX_CONCURRENT_TRADES:
                continue
            
            if i % trend_check_interval == 0:
                trend_bullish = check_4h_trend(symbol)
            
            if not trend_bullish:
                continue
            
            if symbol in open_positions:
                continue
            
            window = df.iloc[:i]
            if len(window) < 50:
                continue
            
            score, reason = calculate_score(window)
            
            if score < WEAK_SIGNAL_THRESHOLD:
                continue
            
            if "low_volume" in reason:
                continue
            
            last_signal = last_signal_time.get(symbol)
            if last_signal and (entry_time - last_signal).total_seconds() < (SIGNAL_COOLDOWN_HOURS * 3600):
                continue
            
            day_date = str(entry_time)[:10]
            if day_date not in trades_per_day:
                trades_per_day[day_date] = 0
            if trades_per_day[day_date] >= DAILY_TRADE_CAP:
                continue
            trades_per_day[day_date] += 1
            
            signals_found += 1
            last_signal_time[symbol] = entry_time
            total_concurrent += 1
            
            entry_price = window.iloc[-1]['close']
            open_positions.add(symbol)
            
            hold_candles = min(MAX_HOLD_CANDLES, total_candles - i - 1)
            future = df.iloc[i:i+hold_candles]
            if len(future) == 0:
                open_positions.discard(symbol)
            total_concurrent = max(0, total_concurrent - 1)
                continue
            
            mfe = future['high'].max()
            mae = future['low'].min()
            mfe_pct = ((mfe - entry_price) / entry_price) * 100
            mae_pct = ((entry_price - mae) / entry_price) * 100
            
            exit_price = future.iloc[-1]['close']
            exit_time = future.index[-1]
            hold_hours = (exit_time - entry_time).total_seconds() / 3600
            
            sl_percent = SL_OVERRIDES.get(symbol, SL_PERCENT)
            tp_price = entry_price * (1 + TP_PERCENT/100)
            sl_price = entry_price * (1 - sl_percent/100)
            
            partial_tp_hit = False
            partial_tp_price = entry_price * (1 + PARTIAL_TP_PERCENT/100)
            trailing_sl = sl_price
            running_high = entry_price
            trade_closed = False
            
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
                    hold_hours = (exit_time - entry_time).total_seconds() / 3600
                    trade_closed = True
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
                    hold_hours = (exit_time - entry_time).total_seconds() / 3600
                    trade_closed = True
                    break
            
            if not trade_closed:
                if exit_price > entry_price:
                    pnl_pct = ((exit_price - entry_price) / entry_price) * 100 * LEVERAGE
                    result = "PROFIT"
                else:
                    pnl_pct = ((exit_price - entry_price) / entry_price) * 100 * LEVERAGE
                    result = "LOSS"
            
            open_positions.discard(symbol)
            total_concurrent = max(0, total_concurrent - 1)
            
            fee_pct = FEE_PERCENT * 2
            pnl_after_fee = pnl_pct - fee_pct
            
            pnl_usd = (BUY_AMOUNT * LEVERAGE) * (pnl_pct / 100)
            pnl_usd_after_fee = (BUY_AMOUNT * LEVERAGE) * (pnl_after_fee / 100)
            
            day_date = str(entry_time)[:10]
            entry_time_str = str(entry_time)[11:16]
            exit_time_str = str(exit_time)[11:16]
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
        
        logger.info(f"Scan complete: scanned {total_scanned} candles, found {signals_found} signals, generated {len(results)} trades")
        return results
    except Exception as e:
        logger.error(f"Error in daily scan: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return []