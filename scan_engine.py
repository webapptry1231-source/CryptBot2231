import logging
from datetime import datetime, timedelta
from data_fetcher import fetch_historical_ohlcv
from indicators import compute_indicators
from scorer import calculate_score
from config import (TIMEFRAME, TIMEFRAME_4H, MAX_HOLD_CANDLES, WEAK_SIGNAL_THRESHOLD,
                   TP_PERCENT, SL_PERCENT, LEVERAGE, BUY_AMOUNT,
                   DAILY_TRADE_CAP, SIGNAL_COOLDOWN_HOURS, TRADE_HOURS_START, TRADE_HOURS_END)

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
        
        trades_per_day = {}
        last_signal_time = {}
        total_scanned = 0
        signals_found = 0
        
        for i in range(24, total_candles - MAX_HOLD_CANDLES):
            total_scanned += 1
            day_idx = i // 24
            if day_idx >= days_checked:
                break
            
            window = df.iloc[:i]
            if len(window) < 50:
                continue
            
            entry_time = window.index[-1]
            entry_hour = entry_time.hour
            
            if entry_hour < TRADE_HOURS_START or entry_hour >= TRADE_HOURS_END:
                continue
            
            if not check_4h_trend(symbol):
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
            
            entry_price = window.iloc[-1]['close']
            
            hold_candles = min(MAX_HOLD_CANDLES, total_candles - i - 1)
            future = df.iloc[i:i+hold_candles]
            if len(future) == 0:
                continue
            
            exit_price = future.iloc[-1]['close']
            exit_time = future.index[-1]
            hold_hours = (exit_time - entry_time).total_seconds() / 3600
            
            tp_price = entry_price * (1 + TP_PERCENT/100)
            sl_price = entry_price * (1 - SL_PERCENT/100)
            
            hit_tp_idx = None
            hit_sl_idx = None
            
            for j in range(len(future)):
                candle = future.iloc[j]
                if candle['high'] >= tp_price:
                    hit_tp_idx = j
                    break
            
            for j in range(len(future)):
                candle = future.iloc[j]
                if candle['low'] <= sl_price:
                    hit_sl_idx = j
                    break
            
            if hit_tp_idx is not None and (hit_sl_idx is None or hit_tp_idx < hit_sl_idx):
                pnl_pct = TP_PERCENT
                result = "TP HIT"
                exit_price = future.iloc[hit_tp_idx]['high']
                exit_time = future.index[hit_tp_idx]
                hold_hours = (exit_time - entry_time).total_seconds() / 3600
            elif hit_sl_idx is not None:
                pnl_pct = -SL_PERCENT
                result = "SL HIT"
                exit_price = future.iloc[hit_sl_idx]['low']
                exit_time = future.index[hit_sl_idx]
                hold_hours = (exit_time - entry_time).total_seconds() / 3600
            elif exit_price > entry_price:
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100 * LEVERAGE
                result = "PROFIT"
            else:
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100 * LEVERAGE
                result = "LOSS"
            
            fee = (TP_PERCENT + SL_PERCENT) / 2 * LEVERAGE * 0.001
            pnl_after_fee = pnl_pct - fee
            
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
                "hold_hours": round(hold_hours, 1)
            })
        
        logger.info(f"Scan complete: scanned {total_scanned} candles, found {signals_found} signals, generated {len(results)} trades")
        return results
    except Exception as e:
        logger.error(f"Error in daily scan: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return []