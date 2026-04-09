import pandas as pd
import pandas_ta as ta

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    rsi_result = df.ta.rsi(length=14)
    if rsi_result is not None:
        df['RSI_14'] = rsi_result
    
    macd = df.ta.macd(fast=12, slow=26, signal=9)
    if macd is not None:
        for col in macd.columns:
            df[col] = macd[col]
    
    ema50 = df.ta.ema(length=50)
    if ema50 is not None:
        df['EMA_50'] = ema50
    
    ema200 = df.ta.ema(length=200)
    if ema200 is not None:
        df['EMA_200'] = ema200
    
    bbands = df.ta.bbands(length=20, std=2)
    if bbands is not None:
        bb_lower_col = [c for c in bbands.columns if c.startswith('BBL')][0]
        bb_upper_col = [c for c in bbands.columns if c.startswith('BBU')][0]
        df['BBL_20_2'] = bbands[bb_lower_col]
        df['BBU_20_2'] = bbands[bb_upper_col]

    df['VOL_SMA_20'] = df['volume'].rolling(window=20).mean()

    df.dropna(inplace=True)
    return df