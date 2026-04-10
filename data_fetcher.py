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
    end_time = now
    start_time = end_time - pd.Timedelta(days=days_back)
    end_ms = int(end_time.timestamp() * 1000)
    start_ms = int(start_time.timestamp() * 1000)

    all_candles = []
    batch_num = 0
    current_start = start_ms

    timeframe_ms = {
        '1m': 60000, '5m': 300000, '15m': 900000,
        '1h': 3600000, '4h': 14400000, '1d': 86400000
    }.get(timeframe, 900000)

    # ROOT CAUSE FIX:
    # Bybit returns at most 999 candles per request (not 1000).
    # The previous guard `if len(batch) < 1000: break` always fired on
    # the very first batch, so only ~10 days were ever fetched instead of 90.
    # We now stop ONLY when: exchange returns nothing, last candle >= end,
    # or batch is genuinely tiny (< 50 = true end of available data).
    while current_start < end_ms:
        batch_num += 1
        try:
            batch = exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, since=current_start, limit=1000
            )

            if not batch or len(batch) == 0:
                logger.info(f"  Batch {batch_num}: empty response – stopping")
                break

            all_candles.extend(batch)
            last_ts = batch[-1][0]
            logger.info(
                f"  Batch {batch_num}: {len(batch)} candles | "
                f"start={pd.Timestamp(batch[0][0], unit='ms', tz='UTC')} | "
                f"end={pd.Timestamp(last_ts, unit='ms', tz='UTC')}"
            )

            if last_ts >= end_ms:
                logger.info(f"  Reached target end time – stopping")
                break

            # True end of data (much smaller than a full page)
            if len(batch) < 50:
                logger.info(f"  Tiny batch ({len(batch)}) – stopping")
                break

            current_start = last_ts + timeframe_ms
            time.sleep(0.3)

        except Exception as e:
            logger.warning(f"Batch {batch_num} error: {e}")
            break

    if len(all_candles) < 100:
        logger.error(f"Only {len(all_candles)} candles fetched – check symbol/exchange")
        return pd.DataFrame()

    df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df.set_index('timestamp', inplace=True)
    df = df.astype({'open': float, 'high': float, 'low': float, 'close': float, 'volume': float})
    df = df[~df.index.duplicated(keep='last')]
    df = df.sort_index()
    df = df[df.index >= start_time]

    days_covered = (df.index[-1] - df.index[0]).total_seconds() / 86400
    logger.info(f"FETCHED {len(df)} candles (~{days_covered:.1f} days) for {symbol} [{timeframe}]")
    return df


def fetch_surgical_ohlcv(symbol: str, timeframe: str = "15m", target_date: str = None) -> pd.DataFrame:
    """
    Surgical mode: Fetch data for a specific date with 30-day warmup.
    target_date format: "YYYY-MM-DD"
    """
    if target_date is None:
        return fetch_historical_ohlcv(symbol, timeframe, days_back=90)
    
    logger.info(f"SURGICAL: Fetching data for {symbol} around {target_date}")
    exchange = _get_exchange()

    target = pd.Timestamp(target_date, tz='UTC')
    # Start 30 days before target for EMA200 warmup
    start_time = target - pd.Timedelta(days=30)
    # End 1 day after target
    end_time = target + pd.Timedelta(days=1)
    
    start_ms = int(start_time.timestamp() * 1000)
    end_ms = int(end_time.timestamp() * 1000)

    all_candles = []
    batch_num = 0
    current_start = start_ms

    timeframe_ms = {
        '1m': 60000, '5m': 300000, '15m': 900000,
        '1h': 3600000, '4h': 14400000, '1d': 86400000
    }.get(timeframe, 900000)

    while current_start < end_ms:
        batch_num += 1
        try:
            batch = exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, since=current_start, limit=1000
            )

            if not batch or len(batch) == 0:
                break

            all_candles.extend(batch)
            last_ts = batch[-1][0]
            current_start = last_ts + timeframe_ms

            if len(batch) < 1000:
                break
        except Exception as e:
            logger.warning(f"Batch {batch_num} failed for {symbol}: {e}")
            break

    if len(all_candles) < 100:
        logger.warning(f"Only {len(all_candles)} candles for surgical scan")
        return pd.DataFrame()

    df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df.set_index('timestamp', inplace=True)
    df = df.astype({'open': float, 'high': float, 'low': float, 'close': float, 'volume': float})
    df = df[~df.index.duplicated(keep='last')]
    df = df.sort_index()
    df = df[df.index >= start_time]

    logger.info(f"SURGICAL: {len(df)} candles for {target_date}")
    return df


def fetch_date_range_ohlcv(symbol: str, timeframe: str = "15m", end_date: str = None, days: int = 90) -> pd.DataFrame:
    """
    Fetch exact number of days ending on a specific date.
    end_date format: "YYYY-MM-DD"
    days: number of days to fetch before end_date
    """
    if end_date is None:
        return fetch_historical_ohlcv(symbol, timeframe, days_back=days)
    
    logger.info(f"DATE RANGE: Fetching {days} days ending {end_date} for {symbol}")
    exchange = _get_exchange()

    end = pd.Timestamp(end_date, tz='UTC') + pd.Timedelta(days=1)  # Include end date
    start = end - pd.Timedelta(days=days)
    
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    all_candles = []
    current_start = start_ms

    timeframe_ms = {
        '1m': 60000, '5m': 300000, '15m': 900000,
        '1h': 3600000, '4h': 14400000, '1d': 86400000
    }.get(timeframe, 900000)

    while current_start < end_ms:
        try:
            batch = exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, since=current_start, limit=1000
            )

            if not batch or len(batch) == 0:
                break

            all_candles.extend(batch)
            last_ts = batch[-1][0]
            current_start = last_ts + timeframe_ms

            if len(batch) < 1000:
                break
        except Exception as e:
            logger.warning(f"Fetch failed for {symbol}: {e}")
            break

    if len(all_candles) < 100:
        logger.warning(f"Only {len(all_candles)} candles for date range scan")
        return pd.DataFrame()

    df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df.set_index('timestamp', inplace=True)
    df = df.astype({'open': float, 'high': float, 'low': float, 'close': float, 'volume': float})
    df = df[~df.index.duplicated(keep='last')]
    df = df.sort_index()
    df = df[df.index >= start]

    logger.info(f"DATE RANGE: {len(df)} candles ({days} days ending {end_date})")
    return df
