import pandas as pd

def calculate_score(df: pd.DataFrame) -> tuple[int, str]:
    if len(df) < 2:
        return 0, "insufficient_data"

    latest = df.iloc[-1]
    prev = df.iloc[-2]
    score = 0
    reasons = []

    if latest['close'] > latest['EMA_200']:
        score += 20
        reasons.append("price>EMA200")
    
    if latest['EMA_50'] > latest['EMA_200']:
        score += 20
        reasons.append("EMA50>EMA200")

    rsi = latest['RSI_14']
    if rsi < 30:
        score += 5
        reasons.append("RSI_oversold_warning")
    elif 30 <= rsi < 45:
        score += 10
        reasons.append("RSI_recovering")
    elif rsi >= 45 and rsi < 65:
        score += 5
        reasons.append("RSI_neutral")

    macd_cross = (
        prev['MACD_12_26_9'] <= prev['MACDs_12_26_9'] and
        latest['MACD_12_26_9'] > latest['MACDs_12_26_9']
    )
    if macd_cross:
        score += 5
        reasons.append("MACD_crossover")
    elif latest['MACDh_12_26_9'] > 0 and prev['MACDh_12_26_9'] <= 0:
        score += 8
        reasons.append("MACD_hist_positive")

    bb_width = latest['BBU_20_2'] - latest['BBL_20_2']
    prev_bb_width = prev['BBU_20_2'] - prev['BBL_20_2']
    bb_expanding = bb_width > prev_bb_width
    
    if latest['close'] <= latest['BBL_20_2']:
        if bb_expanding:
            score += 15
            reasons.append("BB_lower_touch_expanding")
        else:
            score += 5
            reasons.append("BB_lower_touch_narrow")
    elif latest['close'] >= latest['BBU_20_2']:
        score -= 10
        reasons.append("BB_upper_penalty")

    if latest['ADX_14'] > 25:
        score += 10
        reasons.append("ADX_trending")

    if latest['VOL_SMA_20'] > 0 and latest['volume'] > 1.3 * latest['VOL_SMA_20']:
        score += 10
        reasons.append("volume_spike")
    else:
        return 0, "low_volume"

    score = max(0, min(100, score))
    reason_str = " + ".join(reasons) if reasons else "no_signal"
    return score, reason_str