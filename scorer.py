import pandas as pd


def calculate_score(df: pd.DataFrame, trend_bonus: int = 0, direction: str = "LONG") -> tuple[int, str]:
    """
    Score a window of OHLCV+indicator data for a LONG or SHORT setup.

    Returns (score: int, reason: str).
    score = 0 means "no signal" (either hard gate failed or total < 1).
    """
    if len(df) < 2:
        return 0, "insufficient_data"

    latest = df.iloc[-1]
    prev   = df.iloc[-2]

    direction = direction.upper()

    # ── Hard gate: volume ────────────────────────────────────────────────────
    vol_sma = latest.get('VOL_SMA_20', 0)
    if vol_sma <= 0:
        return 0, "no_volume_data"

    vol_ratio = latest['volume'] / vol_sma
    if vol_ratio < 0.8:
        return 0, "low_volume"

    score   = 0
    reasons = []

    # ── 1. Macro regime alignment (max 30 pts) ───────────────────────────────
    ema200 = float(latest['EMA_200'])
    ema50  = float(latest['EMA_50'])
    close  = float(latest['close'])

    if direction == "LONG":
        if close > ema200:
            score += 15
            reasons.append("price>EMA200")
        if ema50 > ema200:
            score += 15
            reasons.append("EMA50>EMA200")
    else:  # SHORT
        if close < ema200:
            score += 15
            reasons.append("price<EMA200")
        if ema50 < ema200:
            score += 15
            reasons.append("EMA50<EMA200")

    # ── 2. EMA20 stack alignment (max 8 pts) ─────────────────────────────────
    # Direction-specific: only adds score when stack agrees with trade direction
    ema20 = float(latest.get('EMA_20', close))
    if direction == "LONG":
        if close > ema20 > ema50:
            score += 8
            reasons.append("EMA20_stack_bullish")
    else:
        if close < ema20 < ema50:
            score += 8
            reasons.append("EMA20_stack_bearish")

    # ── 3. MACD (max 10 pts, crossover/hist are mutually exclusive) ──────────
    macd_line = float(latest.get('MACD_12_26_9', 0))
    macd_sig  = float(latest.get('MACDs_12_26_9', 0))
    macd_hist = float(latest.get('MACDh_12_26_9', 0))
    prev_macd_line = float(prev.get('MACD_12_26_9', 0))
    prev_macd_sig  = float(prev.get('MACDs_12_26_9', 0))
    prev_macd_hist = float(prev.get('MACDh_12_26_9', 0))
    bbl = float(latest.get('BBL_20_2', 0))
    bbu = float(latest.get('BBU_20_2', close * 2))

    macd_bullish = close > bbl and macd_hist > 0
    macd_bearish = close < bbu and macd_hist < 0

    macd_cross_up   = prev_macd_line <= prev_macd_sig and macd_line > macd_sig
    macd_cross_down = prev_macd_line >= prev_macd_sig and macd_line < macd_sig

    if direction == "LONG":
        if macd_bullish:
            score += 10
            reasons.append("MACD_bullish")
        if macd_cross_up:
            score += 10
            reasons.append("MACD_crossover")
        elif macd_hist > 0 and prev_macd_hist <= 0:
            score += 8
            reasons.append("MACD_hist_positive")
    else:  # SHORT
        if macd_bearish:
            score += 10
            reasons.append("MACD_bearish")
        if macd_cross_down:
            score += 10
            reasons.append("MACD_crossdown")
        elif macd_hist < 0 and prev_macd_hist >= 0:
            score += 8
            reasons.append("MACD_hist_negative")
    
    # Penalize high-score MACD crossovers (exhaustion trap)
    if ("MACD_crossover" in reasons or "MACD_crossdown" in reasons) and score > 80:
        score -= 15
        reasons.append("MACD_exhaustion_penalty")

    # ── 4. RSI (max 15 pts) ──────────────────────────────────────────────────
    rsi = float(latest.get('RSI_14', 50))

    if direction == "LONG":
        if rsi < 30:
            score += 5
            reasons.append("RSI_oversold_warning")
        elif 30 <= rsi < 45:
            score += 15
            reasons.append("RSI_recovering")
        elif 45 <= rsi < 65:
            score += 10
            reasons.append("RSI_neutral")
        # rsi >= 65: no points (chasing momentum)
    else:  # SHORT
        if rsi > 70:
            score += 5
            reasons.append("RSI_overbought_warning")
        elif 55 < rsi <= 70:
            score += 15
            reasons.append("RSI_falling")
        elif 35 <= rsi <= 55:
            score += 10
            reasons.append("RSI_neutral")

    # ── 5. Bollinger Bands (max 12 pts, penalty -10) ─────────────────────────
    bbm  = float(latest.get('BBM_20_2', close))
    bb_range       = bbu - bbl
    bb_lower_zone  = bbl + (bb_range * 0.15)
    bb_upper_zone  = bbu - (bb_range * 0.15)
    prev_bbu       = float(prev.get('BBU_20_2', bbu))
    prev_bbl       = float(prev.get('BBL_20_2', bbl))
    bb_expanding   = (bbu - bbl) > (prev_bbu - prev_bbl)

    if direction == "LONG":
        if close <= bb_lower_zone:
            score += 12 if bb_expanding else 8
            reasons.append("BB_lower_zone_expanding" if bb_expanding else "BB_lower_zone")
        elif close <= bbm:
            score += 10
            reasons.append("BB_below_mid")
        elif close >= bbu:
            score -= 10
            reasons.append("BB_upper_penalty")
    else:  # SHORT
        if close >= bb_upper_zone:
            score += 12 if bb_expanding else 8
            reasons.append("BB_upper_zone_expanding" if bb_expanding else "BB_upper_zone")
        elif close >= bbm:
            score += 10
            reasons.append("BB_above_mid")
        elif close <= bbl:
            score -= 10
            reasons.append("BB_lower_penalty")

    # ── 6. Volume (max 15 pts) ───────────────────────────────────────────────
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

    # ── 7. Stochastic RSI (max 10 pts) ──────────────────────────────────────
    stoch_k = float(latest.get('STOCHRSI_K', 50))
    stoch_d = float(latest.get('STOCHRSI_D', 50))
    if direction == "LONG" and stoch_k < 20 and stoch_k > stoch_d:
        score += 10
        reasons.append("StochRSI_oversold_cross")
    elif direction == "SHORT" and stoch_k > 80 and stoch_k < stoch_d:
        score += 10
        reasons.append("StochRSI_overbought_cross")

    # ── 8. ADX trend confirmation (max 8 pts, no penalty) ────────────────────
    # Added back as a CONFIRMATION bonus only (not a primary driver).
    # Only scores when ADX is rising AND above 20 (genuine momentum).
    # Per audit: never subtract for ADX, just add when confirmed.
    adx = float(latest.get('ADX_14', 0))
    prev_adx = float(prev.get('ADX_14', 0))
    adx_rising = adx > prev_adx
    if adx > 20 and adx_rising:
        score += 8
        reasons.append("ADX_trending")

    # ── 9. 4h trend alignment bonus (passed in from scan_engine) ─────────────
    if trend_bonus > 0:
        score += trend_bonus
        reasons.append("4h_bullish" if direction == "LONG" else "4h_bearish")

    # ── Floor only — no ceiling cap ──────────────────────────────────────────
    score = max(0, score)

    reason_str = " + ".join(reasons) if reasons else "no_signal"
    return score, reason_str
