import pandas as pd
import pandas_ta as ta

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df['RSI_14'] = df.ta.rsi(length=14)
    
    macd = df.ta.macd(fast=12, slow=26, signal=9)
    if macd is not None:
        macd.columns = ['MACD_12_26_9', 'MACDs_12_26_9', 'MACDh_12_26_9']
        df = pd.concat([df, macd], axis=1)
    
    df['EMA_50'] = df.ta.ema(length=50)
    df['EMA_200'] = df.ta.ema(length=200)
    
    bbands = df.ta.bbands(length=20, std=2.0)
    if bbands is not None:
        bbands.columns = ['BBL_20_2.0', 'BBM_20_2.0', 'BBU_20_2.0', 'BBB_20_2.0', 'BBP_20_2.0']
        df = pd.concat([df, bbands], axis=1)

    df['VOL_SMA_20'] = df['volume'].rolling(window=20).mean()

    df.dropna(inplace=True)
    return df