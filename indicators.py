import pandas as pd
import pandas_ta as ta

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df.ta.rsi(length=14, append=True)
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    df.ta.ema(length=50, append=True)
    df.ta.ema(length=200, append=True)
    df.ta.bbands(length=20, std=2.0, append=True)

    df['VOL_SMA_20'] = df['volume'].rolling(window=20).mean()

    df.dropna(inplace=True)
    return df