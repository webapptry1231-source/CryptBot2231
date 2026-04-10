import pandas as pd

def calculate_score(df: pd.DataFrame, trend_bonus: int = 0, direction: str = "LONG") -> tuple[int, str]:
    if len(df) < 2:
        return 0, "insufficient_data"

    latest = df.iloc[-1]
    prev   = df.iloc[-2]
    
    # direction: "LONG" or "SHORT"
    direction = direction.upper()
    
    # ── Volume gate ──────────────────────────────────────────────────────────
    if latest['VOL_SMA_20'] <= 0:
        return 0, "no_volume_data"

    vol_ratio = latest['volume'] / latest['VOL_SMA_20']
    if vol_ratio < 0.8:
        return 0, "low_volume"

    score   = 0
    reasons = []

    # ── Macro Regime (Long or Short) ─────────────────────────────────────────
    ema200 = latest['EMA_200']
    ema50 = latest['EMA_50']
    close = latest['close']
    
    if direction == "LONG":
        # LONG: Price > EMA200 AND EMA50 > EMA200 OR 4h_bullish
        if close > ema200:
            score += 15
            reasons.append("price>EMA200")
        if ema50 > ema200:
            score += 15
            reasons.append("EMA50>EMA200")
    else:
        # SHORT: Price < EMA200 AND EMA50 < EMA200 OR 4h_bearish
        if close < ema200:
            score += 15
            reasons.append("price<EMA200")
        if ema50 < ema200:
            score += 15
            reasons.append("EMA50<EMA200")

    # ── MACD ─────────────────────────────────────────────────────────────────
    macd_bullish = latest['close'] > latest['BBL_20_2'] and latest['MACDh_12_26_9'] > 0
    macd_bearish = latest['close'] < latest['BBU_20_2'] and latest['MACDh_12_26_9'] < 0
    
    if direction == "LONG" and macd_bullish:
        score += 10
        reasons.append("MACD_bullish")
    elif direction == "SHORT" and macd_bearish:
        score += 10
        reasons.append("MACD_bearish")

    macd_cross = (
        prev['MACD_12_26_9']   <= prev['MACDs_12_26_9'] and
        latest['MACD_12_26_9'] >  latest['MACDs_12_26_9']
    )
    macd_cross_down = (
        prev['MACD_12_26_9']   >= prev['MACDs_12_26_9'] and
        latest['MACD_12_26_9'] <  latest['MACDs_12_26_9']
    )
    if direction == "LONG" and macd_cross:
        score += 10
        reasons.append("MACD_crossover")
    elif direction == "SHORT" and macd_cross_down:
        score += 10
        reasons.append("MACD_crossdown")
    elif latest['MACDh_12_26_9'] > 0 and prev['MACDh_12_26_9'] <= 0:
        score += 8
        reasons.append("MACD_hist_positive")
    elif latest['MACDh_12_26_9'] < 0 and prev['MACDh_12_26_9'] >= 0:
        score += 8
        reasons.append("MACD_hist_negative")

    # ── RSI ───────────────────────────────────────────────────────────────────
    rsi = latest['RSI_14']
    if direction == "LONG":
        # Long: Recovering from bottom
        if rsi < 30:
            score += 5
            reasons.append("RSI_oversold_warning")
        elif 30 <= rsi < 45:
            score += 15
            reasons.append("RSI_recovering")
        elif 45 <= rsi < 65:
            score += 10
            reasons.append("RSI_neutral")
    else:
        # Short: Falling from top
        if rsi > 70:
            score += 5
            reasons.append("RSI_overbought_warning")
        elif 55 < rsi <= 70:
            score += 15
            reasons.append("RSI_falling")
        elif 35 <= rsi <= 55:
            score += 10
            reasons.append("RSI_neutral")

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    bb_range      = latest['BBU_20_2'] - latest['BBL_20_2']
    bb_lower_zone = latest['BBL_20_2'] + (bb_range * 0.15)
    bb_upper_zone = latest['BBU_20_2'] - (bb_range * 0.15)
    bb_width      = bb_range
    prev_bb_width = prev['BBU_20_2'] - prev['BBL_20_2']
    bb_expanding  = bb_width > prev_bb_width
    
    if direction == "LONG":
        # Long: BB_below_mid / BB_lower_zone_expanding
        if latest['close'] <= bb_lower_zone:
            score += 12 if bb_expanding else 8
            reasons.append("BB_lower_zone_expanding" if bb_expanding else "BB_lower_zone")
        elif latest['close'] <= latest['BBM_20_2']:
            score += 10
            reasons.append("BB_below_mid")
        elif latest['close'] >= latest['BBU_20_2']:
            score -= 10
            reasons.append("BB_upper_penalty")
    else:
        # Short: BB_above_mid / BB_upper_zone_expanding
        if latest['close'] >= bb_upper_zone:
            score += 12 if bb_expanding else 8
            reasons.append("BB_upper_zone_expanding" if bb_expanding else "BB_upper_zone")
        elif latest['close'] >= latest['BBM_20_2']:
            score += 10
            reasons.append("BB_above_mid")
        elif latest['close'] <= latest['BBL_20_2']:
            score -= 10
            reasons.append("BB_lower_penalty")

    # ── ADX / Trend strength ─────────────────────────────────────────────────
    # ADX removed as positive score (causes "top buyer" signal per audit)

    # ── Volume scoring ───────────────────────────────────────────────────────
    vol_threshold = 1.3 if direction == "SHORT" else 1.15
    if vol_ratio >= 1.5:
        score += 15
        reasons.append("high_volume_spike")
    elif vol_ratio >= vol_threshold:
        score += 10
        reasons.append("volume_spike")
    elif vol_ratio >= 0.9:
        score += 5
        reasons.append("normal_volume")

    # ── 4h trend bonus (passed in from scan_engine) ───────────────────────────
    score += trend_bonus
    if trend_bonus > 0:
        if direction == "LONG":
            reasons.append("4h_bullish")
        else:
            reasons.append("4h_bearish")

    # ── EMA20 Stack Alignment ─────────────────────────────────────────────────
    ema20 = latest.get('EMA_20', close)
    if ema20 and close > ema20 and ema20 > ema50:
        score += 8
        reasons.append("EMA20_stack_bullish" if direction == "LONG" else "EMA20_stack_bearish")

    # ── Stochastic RSI ─────────────────────────────────────────────────
    stoch_k = latest.get('STOCHRSI_K', 50)
    stoch_d = latest.get('STOCHRSI_D', 50)
    if direction == "LONG" and stoch_k < 20 and stoch_k > stoch_d:
        score += 10
        reasons.append("StochRSI_oversold_cross")
    elif direction == "SHORT" and stoch_k > 80 and stoch_k < stoch_d:
        score += 10
        reasons.append("StochRSI_overbought_cross")

    # ── Score floor ─────────────────────────────────────────────────────────
    score = max(0, score)
    
    reason_str = " + ".join(reasons) if reasons else "no_signal"
    return score, reason_str
