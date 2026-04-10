import ccxt
import pandas as pd
import time
import logging

logger = logging.getLogger(__name__)

def _get_exchange():
    return ccxt.bybit({
        'enableRateLimit': True,
        'options': {'defaultType': 'future'}
    })

def fetch_ohlcv(symbol: str, timeframe: str = "15m", limit: int = 300) -> pd.DataFrame:
    exchange = _get_exchange()
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df.set_index('timestamp', inplace=True)
    df = df.astype({'open': float, 'high': float, 'low': float, 'close': float, 'volume': float})
    return df

def fetch_historical_ohlcv(symbol: str, timeframe: str = "15m", days_back: int = 90) -> pd.DataFrame:
    logger.info(f"Fetching {days_back} days of {timeframe} data for {symbol}")
    exchange = _get_exchange()
    
    now = pd.Timestamp.now(tz='UTC')
    start_time = now - pd.Timedelta(days=days_back)
    since_ms = int(start_time.timestamp() * 1000)
    
    all_candles = []
    batch_num = 0
    
    while True:
        batch_num += 1
        try:
            batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since_ms, limit=1000)
            
            if not batch or len(batch) == 0:
                break
                
            all_candles.extend(batch)
            last_ts = batch[-1][0]
            since_ms = last_ts + 1
            
            logger.info(f"  Batch {batch_num}: {len(batch)} candles (total: {len(all_candles)})")
            
            if len(batch) < 1000:
                break
                
            time.sleep(0.3)
            
        except Exception as e:
            logger.warning(f"Batch {batch_num} error: {e}")
            break
    
    if len(all_candles) < 100:
        logger.error(f"Only {len(all_candles)} candles fetched!")
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