import ccxt
import pandas as pd
import time
import logging
import threading

logger = logging.getLogger(__name__)
_exchange_lock = threading.Lock()

def _get_exchange():
    return ccxt.bybit({
        'enableRateLimit': True,
        'options': {'defaultType': 'future'}
    })

def fetch_ohlcv(symbol: str, timeframe: str = "15m", limit: int = 300) -> pd.DataFrame:
    with _exchange_lock:
        exchange = _get_exchange()
        raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df.set_index('timestamp', inplace=True)
    df = df.astype({'open': float, 'high': float, 'low': float, 'close': float, 'volume': float})
    return df

def fetch_historical_ohlcv(symbol: str, timeframe: str = "15m", days_back: int = 90) -> pd.DataFrame:
    logger.info(f"Fetching {days_back} days of {timeframe} data for {symbol}")
    all_candles = []
    exchange = _get_exchange()
    
    limit_needed = days_back * 96 if timeframe == "15m" else days_back * 6
    limit_needed = min(limit_needed, 10000)
    
    max_retries = 5
    for retry in range(max_retries):
        try:
            all_candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit_needed)
            break
        except Exception as e:
            logger.warning(f"Fetch retry {retry+1}: {e}")
            time.sleep(2)
    
    if not all_candles or len(all_candles) == 0:
        logger.error("No candles fetched!")
        return pd.DataFrame()
    
    df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df.set_index('timestamp', inplace=True)
    df = df.astype({'open': float, 'high': float, 'low': float, 'close': float, 'volume': float})
    df = df[~df.index.duplicated(keep='last')]
    df = df.sort_index()
    
    days_covered = (df.index[-1] - df.index[0]).total_seconds() / 86400
    logger.info(f"Fetched {len(df)} candles (~{days_covered:.1f} days)")
    return df