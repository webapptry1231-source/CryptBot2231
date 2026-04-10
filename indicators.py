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

    ema20 = df.ta.ema(length=20)
    if ema20 is not None:
        df['EMA_20'] = ema20 if not isinstance(ema20, pd.DataFrame) else ema20

    ema50 = df.ta.ema(length=50)
    if ema50 is not None:
        df['EMA_50'] = ema50 if not isinstance(ema50, pd.DataFrame) else ema50.iloc[:, -1]

    ema200 = df.ta.ema(length=200)
    if ema200 is not None:
        df['EMA_200'] = ema200 if not isinstance(ema200, pd.DataFrame) else ema200.iloc[:, -1]

    bbands = df.ta.bbands(length=20, std=2)
    if bbands is not None:
        bb_lower_col = [c for c in bbands.columns if c.startswith('BBL')][0]
        bb_mid_col   = [c for c in bbands.columns if c.startswith('BBM')][0]
        bb_upper_col = [c for c in bbands.columns if c.startswith('BBU')][0]
        df['BBL_20_2'] = bbands[bb_lower_col]
        df['BBM_20_2'] = bbands[bb_mid_col]
        df['BBU_20_2'] = bbands[bb_upper_col]

    adx_result = df.ta.adx(length=14)
    if adx_result is not None:
        df['ADX_14'] = adx_result['ADX_14']
        df['DMP_14'] = adx_result['DMP_14']
        df['DMN_14'] = adx_result['DMN_14']

    stochrsi = df.ta.stochrsi(length=14, rsi_length=14, k=3, d=3)
    if stochrsi is not None:
        df['STOCHRSI_K'] = stochrsi.iloc[:, 0]
        df['STOCHRSI_D'] = stochrsi.iloc[:, 1]

    atr = df.ta.atr(length=14)
    if atr is not None:
        df['ATR_14'] = atr

    df['VOL_SMA_20'] = df['volume'].rolling(window=20).mean()

    df.dropna(inplace=True)
    return df
