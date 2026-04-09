import logging
from data_fetcher import fetch_historical_ohlcv
from indicators import compute_indicators
from scorer import calculate_score
from config import (TIMEFRAME, MAX_HOLD_CANDLES, WEAK_SIGNAL_THRESHOLD,
                   TP_PERCENT, SL_PERCENT, LEVERAGE, BUY_AMOUNT)

logger = logging.getLogger(__name__)
MAX_OPEN_TRADES = 3

def scan_daily_historical(symbol: str, days: int) -> list:
    try:
        df = fetch_historical_ohlcv(symbol, timeframe=TIMEFRAME, days_back=days)
        df = compute_indicators(df)
        
        results = []
        days_checked = min(days, len(df) - MAX_HOLD_CANDLES)
        trades_per_day = {}
        
        for i in range(24, len(df) - MAX_HOLD_CANDLES):
            day_idx = i // 24
            if day_idx >= days_checked:
                break
            
            window = df.iloc[:i]
            if len(window) < 50:
                continue
                
            score, reason = calculate_score(window)
            
            if score < WEAK_SIGNAL_THRESHOLD:
                continue
            
            day_date = str(window.index[-1])[:10]
            if day_date not in trades_per_day:
                trades_per_day[day_date] = 0
            if trades_per_day[day_date] >= MAX_OPEN_TRADES:
                continue
            trades_per_day[day_date] += 1
            
            entry_price = window.iloc[-1]['close']
            entry_time = window.index[-1]
            
            hold_candles = min(MAX_HOLD_CANDLES, len(df) - i - 1)
            future = df.iloc[i:i+hold_candles]
            if len(future) == 0:
                continue
            
            high_hold = future['high'].max()
            low_hold = future['low'].min()
            exit_price = future.iloc[-1]['close']
            exit_time = future.index[-1]
            hold_hours = (exit_time - entry_time).total_seconds() / 3600
            
            tp_price = entry_price * (1 + TP_PERCENT/100)
            sl_price = entry_price * (1 - SL_PERCENT/100)
            
            hit_tp = high_hold >= tp_price
            hit_sl = low_hold <= sl_price
            hit_tp_idx = None
            hit_sl_idx = None
            
            for j, candle in enumerate(future):
                if candle['high'] >= tp_price:
                    hit_tp_idx = j
                    break
            
            for j, candle in enumerate(future):
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
        
        return results
    except Exception as e:
        logger.error(f"Error in daily scan: {e}")
        return []