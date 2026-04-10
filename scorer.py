import pandas as pd

def calculate_score(df: pd.DataFrame, trend_bonus: int = 0) -> tuple[int, str]:
    if len(df) < 2:
        return 0, "insufficient_data"

    latest = df.iloc[-1]
    prev   = df.iloc[-2]

    # ── Volume gate ──────────────────────────────────────────────────────────
    if latest['VOL_SMA_20'] <= 0:
        return 0, "no_volume_data"

    vol_ratio = latest['volume'] / latest['VOL_SMA_20']
    if vol_ratio < 0.8:
        return 0, "low_volume"

    score   = 0
    reasons = []

    # ── Trend: EMA stack ─────────────────────────────────────────────────────
    price_above_ema200 = latest['close'] > latest['EMA_200']
    ema_golden_cross   = latest['EMA_50'] > latest['EMA_200']

    if price_above_ema200:
        score += 15
        reasons.append("price>EMA200")

    if ema_golden_cross:
        score += 15
        reasons.append("EMA50>EMA200")

    # ── MACD ─────────────────────────────────────────────────────────────────
    if latest['close'] > latest['BBL_20_2'] and latest['MACDh_12_26_9'] > 0:
        score += 10
        reasons.append("MACD_bullish")

    macd_cross = (
        prev['MACD_12_26_9']   <= prev['MACDs_12_26_9'] and
        latest['MACD_12_26_9'] >  latest['MACDs_12_26_9']
    )
    if macd_cross:
        score += 10
        reasons.append("MACD_crossover")
    elif latest['MACDh_12_26_9'] > 0 and prev['MACDh_12_26_9'] <= 0:
        score += 8
        reasons.append("MACD_hist_positive")

    # ── RSI ───────────────────────────────────────────────────────────────────
    rsi = latest['RSI_14']
    if rsi < 30:
        score += 5
        reasons.append("RSI_oversold_warning")
    elif 30 <= rsi < 45:
        score += 10
        reasons.append("RSI_recovering")
    elif 45 <= rsi < 65:
        score += 10
        reasons.append("RSI_neutral")

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    bb_range      = latest['BBU_20_2'] - latest['BBL_20_2']
    bb_lower_zone = latest['BBL_20_2'] + (bb_range * 0.15)
    bb_width      = bb_range
    prev_bb_width = prev['BBU_20_2'] - prev['BBL_20_2']
    bb_expanding  = bb_width > prev_bb_width

    if latest['close'] <= bb_lower_zone:
        score += 12 if bb_expanding else 8
        reasons.append("BB_lower_zone_expanding" if bb_expanding else "BB_lower_zone")
    elif latest['close'] <= latest['BBM_20_2']:   # NOW SAFE: BBM_20_2 exists
        score += 5
        reasons.append("BB_below_mid")
    elif latest['close'] >= latest['BBU_20_2']:
        score -= 10
        reasons.append("BB_upper_penalty")

    # ── ADX / Trend strength ─────────────────────────────────────────────────
    if latest['ADX_14'] > 20:
        score += 10
        reasons.append("ADX_trending")

    # ── Volume scoring ────────────────────────────────────────────────────────
    if vol_ratio >= 1.5:
        score += 15
        reasons.append("high_volume_spike")
    elif vol_ratio >= 1.15:
        score += 10
        reasons.append("volume_spike")
    elif vol_ratio >= 0.9:
        score += 5
        reasons.append("normal_volume")

    # ── 4h trend bonus (passed in from scan_engine) ───────────────────────────
    score += trend_bonus
    if trend_bonus > 0:
        reasons.append("4h_bullish")

    score = max(0, min(100, score))
    reason_str = " + ".join(reasons) if reasons else "no_signal"
    return score, reason_str
