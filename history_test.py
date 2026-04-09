from data_fetcher import fetch_historical_ohlcv
from indicators import compute_indicators
from scorer import calculate_score

SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT", "TON/USDT"]

print("=== Historical Scan (Last 90 Days) ===\n")

for symbol in SYMBOLS:
    try:
        df = fetch_historical_ohlcv(symbol, timeframe="15m", days_back=90)
        df = compute_indicators(df)
        
        scores = []
        for i in range(-100, 0):
            window = df.iloc[:i]
            if len(window) < 50:
                continue
            score, reason = calculate_score(window)
            scores.append((score, reason, window.iloc[-1]['close']))
        
        if scores:
            best = max(scores, key=lambda x: x[0])
            print(f"{symbol}: Best Score={best[0]} | Reason={best[1]} | Price={best[2]}")
    except Exception as e:
        print(f"{symbol}: ERROR - {e}")