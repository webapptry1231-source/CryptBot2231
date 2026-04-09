import ccxt
import pandas as pd
import time

exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

def fetch_ohlcv(symbol: str, timeframe: str = "15m", limit: int = 300) -> pd.DataFrame:
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df.set_index('timestamp', inplace=True)
    df = df.astype({'open': float, 'high': float, 'low': float, 'close': float, 'volume': float})
    return df

def fetch_historical_ohlcv(symbol: str, timeframe: str = "15m", days_back: int = 90) -> pd.DataFrame:
    since_ms = int((pd.Timestamp.now(tz='UTC') - pd.Timedelta(days=days_back)).timestamp() * 1000)
    all_candles = []

    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since_ms, limit=1000)
        if not batch:
            break
        all_candles.extend(batch)
        since_ms = batch[-1][0] + 1
        time.sleep(exchange.rateLimit / 1000)
        if len(batch) < 1000:
            break

    df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df.set_index('timestamp', inplace=True)
    df = df.astype({'open': float, 'high': float, 'low': float, 'close': float, 'volume': float})
    df = df[~df.index.duplicated(keep='last')]
    return df