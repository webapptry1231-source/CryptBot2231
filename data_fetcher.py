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
    logger.info(f"Fetching FULL {days_back} days of {timeframe} data for {symbol} (this may take 20-40s)")
    all_candles = []
    exchange = _get_exchange()
    
    since_ms = int((pd.Timestamp.now(tz='UTC') - pd.Timedelta(days=days_back)).timestamp() * 1000)
    max_retries = 20
    
    for _ in range(max_retries):
        with _exchange_lock:
            batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since_ms, limit=1000)
        
        if not batch or len(batch) == 0:
            break
        
        all_candles.extend(batch)
        since_ms = batch[-1][0] + 1
        time.sleep(0.5)
        
        logger.info(f"  Fetched batch of {len(batch)} candles (total now: {len(all_candles)})")
        
        if len(batch) < 1000:
            break
    
    if len(all_candles) < 500:
        logger.error(f"Only {len(all_candles)} candles fetched — data issue!")
    
    df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df.set_index('timestamp', inplace=True)
    df = df.astype({'open': float, 'high': float, 'low': float, 'close': float, 'volume': float})
    df = df[~df.index.duplicated(keep='last')]
    logger.info(f"Successfully fetched {len(df)} candles (~{len(df)/96:.1f} days)")
    return df