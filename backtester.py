# DEPRECATED: Use scan_engine.scan_daily_historical() instead. This file is not maintained.
import pandas as pd
from config import (COINS, TIMEFRAME, TP_PERCENT, SL_PERCENT,
                    MAX_HOLD_CANDLES, FEE_PERCENT, STRONG_SIGNAL_THRESHOLD)
from data_fetcher import fetch_historical_ohlcv
from indicators import compute_indicators
from scorer import calculate_score

LOOKBACK = 250

def backtest_coin(symbol: str, days_back: int = 90) -> dict:
    print(f"Fetching {days_back} days of {TIMEFRAME} data for {symbol}...")
    raw_df = fetch_historical_ohlcv(symbol, timeframe=TIMEFRAME, days_back=days_back)

    trades = []
    i = LOOKBACK

    while i < len(raw_df):
        window = raw_df.iloc[i - LOOKBACK: i]
        df_with_indicators = compute_indicators(window)

        if len(df_with_indicators) < 2:
            i += 1
            continue

        score, reason = calculate_score(df_with_indicators)

        if score >= STRONG_SIGNAL_THRESHOLD:
            entry_price = raw_df.iloc[i]['close']
            tp_price = entry_price * (1 + TP_PERCENT / 100)
            sl_price = entry_price * (1 - SL_PERCENT / 100)
            entry_time = raw_df.index[i]
            outcome = "TIMEOUT"
            exit_price = entry_price
            hold_candles = 0

            for j in range(i + 1, min(i + 1 + MAX_HOLD_CANDLES, len(raw_df))):
                candle_high = raw_df.iloc[j]['high']
                candle_low = raw_df.iloc[j]['low']
                hold_candles += 1

                if candle_high >= tp_price:
                    outcome = "TP"
                    exit_price = tp_price
                    break
                elif candle_low <= sl_price:
                    outcome = "SL"
                    exit_price = sl_price
                    break

            if outcome == "TIMEOUT":
                exit_price = raw_df.iloc[min(i + MAX_HOLD_CANDLES, len(raw_df) - 1)]['close']

            gross_pnl = (exit_price - entry_price) / entry_price * 100
            net_pnl = gross_pnl - (FEE_PERCENT * 2)

            trades.append({
                'symbol': symbol,
                'entry_time': entry_time,
                'entry_price': entry_price,
                'exit_price': exit_price,
                'outcome': outcome,
                'gross_pnl_pct': round(gross_pnl, 4),
                'net_pnl_pct': round(net_pnl, 4),
                'score': score,
                'reason': reason,
                'hold_candles': hold_candles
            })

            i += hold_candles + 1
        else:
            i += 1

    return compute_metrics(symbol, trades)

def compute_metrics(symbol: str, trades: list) -> dict:
    if not trades:
        return {'symbol': symbol, 'total_trades': 0}

    df = pd.DataFrame(trades)
    wins = df[df['outcome'] == 'TP']
    losses = df[df['outcome'] == 'SL']

    total_profit = wins['net_pnl_pct'].sum()
    total_loss = abs(losses['net_pnl_pct'].sum())
    win_rate = len(wins) / len(df) * 100 if len(df) > 0 else 0
    profit_factor = total_profit / total_loss if total_loss > 0 else float('inf')

    cumulative = df['net_pnl_pct'].cumsum()
    rolling_max = cumulative.cummax()
    drawdown = (cumulative - rolling_max)
    max_drawdown = drawdown.min()

    return {
        'symbol': symbol,
        'total_trades': len(df),
        'win_rate_pct': round(win_rate, 2),
        'total_return_pct': round(df['net_pnl_pct'].sum(), 4),
        'profit_factor': round(profit_factor, 3),
        'max_drawdown_pct': round(max_drawdown, 4),
        'avg_hold_candles': round(df['hold_candles'].mean(), 1),
        'trades': trades
    }

def run_full_backtest(days_back: int = 90):
    results = []
    for coin in COINS:
        try:
            result = backtest_coin(coin, days_back=days_back)
            results.append(result)
            print(f"  {coin}: {result['total_trades']} trades | "
                  f"WR: {result.get('win_rate_pct', 0):.1f}% | "
                  f"Return: {result.get('total_return_pct', 0):.2f}% | "
                  f"MaxDD: {result.get('max_drawdown_pct', 0):.2f}%")
        except Exception as e:
            print(f"  {coin}: ERROR — {e}")
    return results