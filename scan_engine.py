"""
scan_engine.py
==============
Core scanning logic for CryptoSignalBot.

KEY DESIGN: "Best Signal Per Session" model
─────────────────────────────────────────────
Instead of firing every candle that crosses the score threshold (which produces
0 or 10+ signals depending on the day), we divide the trading day into two
sessions and pick the single BEST-scored candle from each session.

  Session 1 (morning):   06:00–12:00 UTC  → 0 or 1 signal
  Session 2 (afternoon): 13:00–22:00 UTC  → 0 or 1 signal

This naturally produces 1–3 quality calls per day:
  • Quiet day:    0–1 signals (one session qualifies)
  • Normal day:   2 signals (both sessions qualify)
  • Strong trend: up to 3 (both + an intra-session bonus if a score spikes ≥80)

Why not just raise/lower the threshold?
  • Too low  → 6-10 clustered signals that all enter the same move
  • Too high → 0 signals on most days (80 was unreachable on clean days)
  • Session model → quality is enforced by "best of session" competition,
    not by an arbitrary absolute number.

Regime logic:
  LONG  = close > EMA200 AND EMA50 > EMA200 (confirmed uptrend)
  SHORT = close < EMA200 AND EMA50 < EMA200 (confirmed downtrend)
  NEUTRAL = price within 0.5% of EMA200 (no trade — too choppy)
  Tiebreaker: 4h trend (BTC master switch)

Both LONG and SHORT are fully supported and evaluated independently.
"""

import logging
import pandas as pd
from data_fetcher import fetch_historical_ohlcv, fetch_surgical_ohlcv
from indicators import compute_indicators
from scorer import calculate_score
from config import (
    TIMEFRAME, TIMEFRAME_4H, WEAK_SIGNAL_THRESHOLD, STRONG_SIGNAL_THRESHOLD,
    TP_LONG_PERCENT, SL_LONG_PERCENT, TRAIL_ACTIVATE_LONG,
    TP_SHORT_PERCENT, SL_SHORT_PERCENT, TRAIL_ACTIVATE_SHORT,
    MAX_HOLD_CANDLES_LONG, MAX_HOLD_CANDLES_SHORT,
    TRAILING_STOP_PERCENT, LEVERAGE, BUY_AMOUNT,
    SIGNAL_COOLDOWN_HOURS, TRADE_HOURS_START, TRADE_HOURS_END,
    TRADE_HOURS_BLACKOUT_START, TRADE_HOURS_BLACKOUT_END,
    NEUTRAL_ZONE_PCT, TRADE_DAYS_BLOCKED, SL_OVERRIDES,
    FEE_PERCENT, DAILY_LOSS_CAP, CONSECUTIVE_SL_STOP,
    ENABLE_ATR_SL, ATR_SL_MULTIPLIER, ATR_TP_RR, ATR_SL_MIN_PCT, ATR_SL_MAX_PCT,
    SCAN_DATE,
    SESSION_MORNING_START, SESSION_MORNING_END,
    SESSION_AFTERNOON_START, SESSION_AFTERNOON_END,
    SESSION_MIN_SCORE, TOXIC_ZONE_MIN, TOXIC_ZONE_MAX,
    SAME_COIN_COOLDOWN_MIN,
    PARTIAL_TP_PERCENT, PARTIAL_TP_SIZE, TIMEOUT_HOURS,
)

logger = logging.getLogger(__name__)

# ── In-memory 4h data cache (cleared by caller before each full run) ─────────
_4h_cache: dict = {}

# Same-coin cooldown tracker: {symbol: last_trade_time}
_last_trade_time: dict = {}


# ─────────────────────────────────────────────────────────────────────────────
# 4h trend helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_4h_cached(symbol: str, days_back: int, end_time: pd.Timestamp) -> pd.DataFrame:
    cache_key = f"{symbol}_{days_back}"
    if cache_key not in _4h_cache:
        logger.info(f"Fetching {days_back}d of 4h data for {symbol} (cache miss)")
        _4h_cache[cache_key] = fetch_historical_ohlcv(symbol, timeframe=TIMEFRAME_4H, days_back=days_back)
    df = _4h_cache[cache_key].copy()
    return df[df.index <= end_time]


def check_4h_trend(symbol: str, as_of_time: pd.Timestamp = None) -> bool:
    """
    Returns True (bullish) if 4h close > EMA200 at as_of_time.
    Used both in historical scanning (as_of_time given) and live scanning (None → now).
    """
    try:
        if as_of_time is None:
            as_of_time = pd.Timestamp.now(tz='UTC')
        df_4h = get_4h_cached(symbol, days_back=60, end_time=as_of_time)
        if len(df_4h) < 50:
            return True   # not enough data → allow trade
        df_4h = compute_indicators(df_4h)
        latest    = df_4h.iloc[-1]
        ema_val   = float(latest['EMA_200'])
        close_val = float(latest['close'])
        result    = close_val > ema_val
        logger.debug(f"4h {symbol} @{as_of_time}: close={close_val:.2f} EMA200={ema_val:.2f} → {'BULL' if result else 'BEAR'}")
        return result
    except Exception as exc:
        logger.warning(f"4h trend check failed for {symbol}: {exc}")
        return True   # fail-open


def determine_regime(df: pd.DataFrame, as_of_time: pd.Timestamp = None) -> str:
    """
    Determine 15m market regime at the tail of df.

    Returns "LONG", "SHORT", or "NEUTRAL".
    Uses slice of df up to as_of_time if supplied (for historical walk-forward).
    """
    if len(df) < 2:
        return "NEUTRAL"

    # Re-compute on a copy so indicators don't contaminate the caller's df
    sample = df.copy() if as_of_time is None else df[df.index <= as_of_time].copy()
    if len(sample) < 2:
        return "NEUTRAL"

    sample = compute_indicators(sample)
    if len(sample) < 1:
        return "NEUTRAL"

    latest = sample.iloc[-1]
    close  = float(latest['close'])
    ema200 = float(latest['EMA_200'])
    ema50  = float(latest['EMA_50'])

    # Hard neutral zone: within 0.5% of EMA200 → no directional edge
    pct_from_ema = abs(close - ema200) / ema200 * 100
    if pct_from_ema <= NEUTRAL_ZONE_PCT:
        return "NEUTRAL"

    long_confirmed  = close > ema200 and ema50 > ema200
    short_confirmed = close < ema200 and ema50 < ema200

    if long_confirmed:
        return "LONG"
    if short_confirmed:
        return "SHORT"

    # Ambiguous 15m structure → use 4h BTC trend as tiebreaker
    btc_bullish = check_4h_trend("BTC/USDT", as_of_time)
    return "LONG" if btc_bullish else "SHORT"


# ─────────────────────────────────────────────────────────────────────────────
# Session helpers
# ─────────────────────────────────────────────────────────────────────────────

def _session_id(hour: int) -> str | None:
    """Map an hour (UTC) to a session label or None if outside all sessions."""
    if SESSION_MORNING_START <= hour < SESSION_MORNING_END:
        return "morning"
    if SESSION_AFTERNOON_START <= hour < SESSION_AFTERNOON_END:
        return "afternoon"
    return None


def _collect_session_candidates(
    df: pd.DataFrame,
    target_date_str: str,
    symbol: str,
) -> dict[str, list[dict]]:
    """
    Walk every candle on target_date_str, score it, and return the
    candidates grouped by session.

    Each candidate is:
        {"idx": i, "time": Timestamp, "score": int, "reason": str,
         "direction": str, "regime": str}

    We evaluate ALL candles regardless of position/cooldown state here —
    the best-signal selection happens in scan_daily_historical.
    """
    total = len(df)
    candidates: dict[str, list[dict]] = {"morning": [], "afternoon": []}

    regime_check_time: pd.Timestamp | None = None
    current_regime = "NEUTRAL"

    for i in range(50, total):
        current_time = df.index[i]

        # Date filter
        if current_time.strftime("%Y-%m-%d") != target_date_str:
            continue

        hour = current_time.hour

        # Trade hours gate
        if hour < TRADE_HOURS_START or hour >= TRADE_HOURS_END:
            continue
        
        # Time-window blackout (13:45 - 16:00 UTC)
        if TRADE_HOURS_BLACKOUT_START <= hour < TRADE_HOURS_BLACKOUT_END:
            continue

        # Blocked days gate (only Saturday blocked by default)
        if current_time.weekday() in TRADE_DAYS_BLOCKED:
            continue

        # Session assignment
        sess = _session_id(hour)
        if sess is None:
            continue

        # Regime — refresh every 4 hours
        if regime_check_time is None or \
                (current_time - regime_check_time).total_seconds() >= 14400:
            current_regime = determine_regime(df.iloc[:i], current_time)
            regime_check_time = current_time
            logger.debug(f"Regime @ {current_time}: {current_regime}")

        if current_regime == "NEUTRAL":
            continue

        direction = current_regime   # "LONG" or "SHORT"

        window = df.iloc[:i]
        if len(window) < 50:
            continue

        # 4h alignment bonus
        is_4h_aligned = check_4h_trend(symbol, current_time)
        trend_bonus = 5 if (
            (direction == "LONG"  and     is_4h_aligned) or
            (direction == "SHORT" and not is_4h_aligned)
        ) else 0

        score, reason = calculate_score(window, trend_bonus=trend_bonus, direction=direction)

        # Skip if BB is too wide (choppy coin)
        latest = window.iloc[-1]
        bb_width_pct = (latest['BBU_20_2'] - latest['BBL_20_2']) / latest['close'] * 100
        if bb_width_pct > 4.0:   # >4% Bollinger width = chop
            continue

        # Toxic Zone filter: skip scores 87-89 and 91-93
        if (TOXIC_ZONE_MIN <= score <= TOXIC_ZONE_MAX) or (91 <= score <= 93):
            continue

        # Macro Regime Filter: Price vs Daily EMA50
        daily_ema50 = latest.get('EMA_50')
        if daily_ema50:
            current_price = latest['close']
            if direction == "LONG" and current_price < daily_ema50:
                continue  # Block LONG when price below daily EMA50
            if direction == "SHORT" and current_price > daily_ema50:
                continue  # Block SHORT when price above daily EMA50

        if score >= SESSION_MIN_SCORE:
            candidates[sess].append({
                "idx":       i,
                "time":      current_time,
                "score":     score,
                "reason":    reason,
                "direction": direction,
                "regime":    current_regime,
            })

    return candidates


# ─────────────────────────────────────────────────────────────────────────────
# Trade simulation
# ─────────────────────────────────────────────────────────────────────────────

def _simulate_trade(
    df: pd.DataFrame,
    i: int,
    direction: str,
    score: int,
    reason: str,
    symbol: str,
    day_date: str,
) -> dict:
    """
    Simulate a single trade starting at candle i.
    Returns a result dict ready to be appended to results.
    """
    total_candles = len(df)

    # Base parameters from config
    if direction == "LONG":
        tp_percent     = TP_LONG_PERCENT
        sl_percent     = SL_OVERRIDES.get(symbol, SL_LONG_PERCENT)
        trail_activate = TRAIL_ACTIVATE_LONG
        max_hold       = MAX_HOLD_CANDLES_LONG
    else:
        tp_percent     = TP_SHORT_PERCENT
        sl_percent     = SL_OVERRIDES.get(symbol, SL_SHORT_PERCENT)
        trail_activate = TRAIL_ACTIVATE_SHORT
        max_hold       = MAX_HOLD_CANDLES_SHORT

    # Entry at NEXT candle open (not signal candle close)
    # This avoids 0.0h instant SL hits
    if i + 1 >= len(df):
        return None
    entry_price = float(df.iloc[i + 1]['open'])
    current_time = df.index[i + 1]  # Entry happens at next candle
    
    # Check timeout (3 hours = 12 candles on 15m)
    max_candles_timeout = int(TIMEOUT_HOURS * 60 / 15)  # 12 candles
    hold_candles = min(max_hold, total_candles - i - 1, max_candles_timeout)

    # ATR-based adaptive SL/TP with clamping
    if ENABLE_ATR_SL:
        atr_raw = df.iloc[i].get('ATR_14', None)
        if atr_raw is not None:
            atr_val = float(atr_raw)
            if atr_val > 0:
                raw_sl_pct = (atr_val * ATR_SL_MULTIPLIER / entry_price) * 100
                sl_percent = max(ATR_SL_MIN_PCT, min(ATR_SL_MAX_PCT, raw_sl_pct))
                tp_percent = sl_percent * ATR_TP_RR
                # Keep trail_activate strictly inside TP range (60% of TP)
                trail_activate = min(trail_activate, tp_percent * 0.6)

    # Price levels
    if direction == "LONG":
        tp_price         = entry_price * (1 + tp_percent    / 100)
        sl_price         = entry_price * (1 - sl_percent    / 100)
        partial_tp_price = entry_price * (1 + PARTIAL_TP_PERCENT / 100)  # 1% partial TP
    else:
        tp_price         = entry_price * (1 - tp_percent    / 100)
        sl_price         = entry_price * (1 + sl_percent    / 100)
        partial_tp_price = entry_price * (1 - PARTIAL_TP_PERCENT / 100)  # 1% partial TP

    hold_candles = min(max_hold, total_candles - i - 1)
    future       = df.iloc[i: i + hold_candles]

    # MFE / MAE (direction-aware)
    if direction == "LONG":
        mfe_pct = ((future['high'].max()  - entry_price) / entry_price) * 100
        mae_pct = ((entry_price - future['low'].min())   / entry_price) * 100
    else:
        mfe_pct = ((entry_price - future['low'].min())   / entry_price) * 100
        mae_pct = ((future['high'].max()  - entry_price) / entry_price) * 100

    # ── Candle-by-candle trade simulation ────────────────────────────────────
    partial_tp_hit = False
    position_closed_50 = False  # Flag for partial close at 50%
    trailing_sl    = sl_price
    running_high   = entry_price
    running_low    = entry_price
    trade_closed   = False
    exit_price     = entry_price
    exit_time      = current_time
    pnl_pct        = 0.0
    pnl_50_percent = 0.0  # PnL from the 50% that was closed at partial TP
    result         = "PENDING"

    for j in range(len(future)):
        candle = future.iloc[j]

        if direction == "LONG":
            running_high = max(running_high, float(candle['high']))

            # Partial TP at PARTIAL_TP_PERCENT (1%) - close 50%, move SL to breakeven
            if not partial_tp_hit and float(candle['high']) >= partial_tp_price:
                partial_tp_hit = True
                # Close 50% of position at this price
                closed_50_price = partial_tp_price
                pnl_50_percent = (PARTIAL_TP_SIZE * (closed_50_price - entry_price) / entry_price * 100 * LEVERAGE)
                position_closed_50 = True
                # Move SL to breakeven for remaining 50%
                trailing_sl = entry_price

            # Trail for remaining 50%
            if partial_tp_hit:
                new_trail = running_high * (1 - TRAILING_STOP_PERCENT / 100)
                trailing_sl = max(trailing_sl, new_trail)

            # Check TP (requires partial hit first)
            if partial_tp_hit and float(candle['high']) >= tp_price:
                remaining_pnl = (1 - PARTIAL_TP_SIZE) * tp_percent * LEVERAGE
                pnl_pct      = pnl_50_percent + remaining_pnl
                result       = "TP HIT"
                exit_price   = tp_price
                exit_time    = future.index[j]
                trade_closed = True
                break

            # Check SL / trail stop
            if float(candle['low']) <= trailing_sl:
                exit_price   = trailing_sl
                exit_time    = future.index[j]
                trade_closed = True
                if partial_tp_hit:
                    remaining_pnl = (1 - PARTIAL_TP_SIZE) * ((trailing_sl - entry_price) / entry_price) * 100 * LEVERAGE
                    pnl_pct = pnl_50_percent + remaining_pnl
                    result  = "TRAIL STOP"
                else:
                    pnl_pct = -sl_percent * LEVERAGE
                    result  = "SL HIT"
                break

        else:  # SHORT
            running_low = float(candle['low']) if j == 0 else min(running_low, float(candle['low']))

            # Partial TP at PARTIAL_TP_PERCENT (1%) - close 50%, move SL to breakeven
            if not partial_tp_hit and float(candle['low']) <= partial_tp_price:
                partial_tp_hit = True
                closed_50_price = partial_tp_price
                pnl_50_percent = (PARTIAL_TP_SIZE * (entry_price - closed_50_price) / entry_price * 100 * LEVERAGE)
                position_closed_50 = True
                trailing_sl = entry_price  # move SL to breakeven

            if partial_tp_hit:
                new_trail = running_low * (1 + TRAILING_STOP_PERCENT / 100)
                trailing_sl = min(trailing_sl, new_trail)

            if partial_tp_hit and float(candle['low']) <= tp_price:
                remaining_pnl = (1 - PARTIAL_TP_SIZE) * tp_percent * LEVERAGE
                pnl_pct      = pnl_50_percent + remaining_pnl
                result       = "TP HIT"
                exit_price   = tp_price
                exit_time    = future.index[j]
                trade_closed = True
                break

            if float(candle['high']) >= trailing_sl:
                exit_price   = trailing_sl
                exit_time    = future.index[j]
                trade_closed = True
                if partial_tp_hit:
                    remaining_pnl = (1 - PARTIAL_TP_SIZE) * ((entry_price - trailing_sl) / entry_price) * 100 * LEVERAGE
                    pnl_pct = pnl_50_percent + remaining_pnl
                    result  = "TRAIL STOP"
                else:
                    pnl_pct = -sl_percent * LEVERAGE
                    result  = "SL HIT"
                break

    # Timeout
    if not trade_closed:
        timeout_idx  = min(i + hold_candles - 1, total_candles - 1)
        exit_price   = float(df.iloc[timeout_idx]['close'])
        exit_time    = df.index[timeout_idx]
        if direction == "LONG":
            remaining_pnl = (1 - PARTIAL_TP_SIZE) * ((exit_price - entry_price) / entry_price) * 100 * LEVERAGE
            pnl_pct = pnl_50_percent + remaining_pnl
        else:
            remaining_pnl = (1 - PARTIAL_TP_SIZE) * ((entry_price - exit_price) / entry_price) * 100 * LEVERAGE
            pnl_pct = pnl_50_percent + remaining_pnl
        result = "TIMEOUT"

    hold_hours         = (exit_time - current_time).total_seconds() / 3600
    fee_pct            = FEE_PERCENT * 2
    pnl_after_fee      = pnl_pct - fee_pct
    pnl_usd            = BUY_AMOUNT * LEVERAGE * (pnl_pct       / 100)
    pnl_usd_after_fee  = BUY_AMOUNT * LEVERAGE * (pnl_after_fee / 100)

    return {
        "symbol":           symbol,
        "date":             day_date,
        "direction":        direction,
        "entry_time":       str(current_time)[11:16],
        "exit_time":        str(exit_time)[11:16],
        "score":            score,
        "reason":           reason,
        "entry":            round(entry_price,   2),
        "exit":             round(exit_price,    2),
        "tp":               round(tp_price,      2),
        "sl":               round(sl_price,      2),
        "result":           result,
        "pnl_pct":          round(pnl_pct,           2),
        "pnl_after_fee":    round(pnl_after_fee,     2),
        "pnl_usd":          round(pnl_usd,           2),
        "pnl_usd_after_fee": round(pnl_usd_after_fee, 2),
        "leverage":         LEVERAGE,
        "buy_amount":       BUY_AMOUNT,
        "hold_hours":       round(hold_hours, 1),
        "mfe_pct":          round(mfe_pct, 2),
        "mae_pct":          round(mae_pct, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def scan_daily_historical(symbol: str, target_date: str = None, days: int = 90) -> list:
    """
    Run the best-signal-per-session scan for `symbol`.

    In SURGICAL mode (SCAN_DATE set or target_date given):
        Fetches data around that specific date and returns 0-3 signals for it.

    In historical mode (days given, no date):
        Fetches `days` of data, scans each calendar day independently.

    Returns a list of trade result dicts.
    """
    try:
        # ── Fetch data ──────────────────────────────────────────────────────
        if SCAN_DATE or target_date:
            scan_date = SCAN_DATE or target_date
            logger.info(f"=== SURGICAL SCAN | {symbol} | {scan_date} ===")
            df = fetch_surgical_ohlcv(symbol, timeframe=TIMEFRAME, target_date=scan_date)
            scan_dates = [scan_date]
        else:
            logger.info(f"=== HISTORICAL SCAN | {symbol} | {days} days ===")
            df = fetch_historical_ohlcv(symbol, timeframe=TIMEFRAME, days_back=days)
            # Collect all unique dates in the fetched window
            scan_dates = sorted(set(df.index.strftime("%Y-%m-%d").tolist()))

        if df is None or len(df) < 100:
            logger.warning(f"Not enough data for {symbol}: {0 if df is None else len(df)} candles")
            return []

        df = compute_indicators(df)
        logger.info(f"Indicators computed: {len(df)} candles for {symbol}")

        all_results: list      = []
        consecutive_sl_hits: int = 0

        # Per-day risk tracking (reset across days)
        daily_losses: dict = {}

        # ── Walk each date ──────────────────────────────────────────────────
        for date_str in scan_dates:
            if consecutive_sl_hits >= CONSECUTIVE_SL_STOP:
                logger.warning(f"Auto-stop: {consecutive_sl_hits} consecutive SL hits — halting scan")
                break

            # Reset daily loss counter for this date
            daily_losses[date_str] = 0

            # ── Phase 1: collect scored candidates for both sessions ────────
            candidates = _collect_session_candidates(df, date_str, symbol)

            logger.info(
                f"{date_str}: morning={len(candidates['morning'])} candidates, "
                f"afternoon={len(candidates['afternoon'])} candidates"
            )

            # ── Phase 2: pick best from each session ────────────────────────
            fired_signals: list[dict] = []

            for sess_name in ("morning", "afternoon"):
                sess_candidates = candidates[sess_name]
                if not sess_candidates:
                    continue

                # Best = highest score; tie-break by later candle (more confirmation)
                best = max(sess_candidates, key=lambda c: (c["score"], c["idx"]))

                # Daily loss cap check
                if daily_losses[date_str] >= DAILY_LOSS_CAP:
                    logger.info(f"{date_str} {sess_name}: daily loss cap reached, skipping")
                    continue

                fired_signals.append(best)

            # ── Phase 3: optional intra-session bonus signal ─────────────────
            # If BOTH sessions qualified AND the absolute best score is ≥ STRONG_SIGNAL_THRESHOLD,
            # allow one additional signal at the peak-score candle (only if it is
            # in a different session half from the already-selected candle).
            # This is how we reach 3 calls on exceptional days.
            if len(fired_signals) == 2:
                all_cands = candidates["morning"] + candidates["afternoon"]
                if all_cands:
                    peak = max(all_cands, key=lambda c: c["score"])
                    # Only fire bonus if it's not the same candle already selected
                    already_selected_idxs = {s["idx"] for s in fired_signals}
                    if (peak["score"] >= STRONG_SIGNAL_THRESHOLD
                            and peak["idx"] not in already_selected_idxs
                            and daily_losses[date_str] < DAILY_LOSS_CAP):
                        fired_signals.append(peak)
                        logger.info(
                            f"{date_str}: bonus signal fired (score={peak['score']} ≥ {STRONG_SIGNAL_THRESHOLD})"
                        )

            # ── Phase 4: simulate trades for selected signals ────────────────
            # Sort by candle index so trades are logged chronologically
            fired_signals.sort(key=lambda c: c["idx"])

            for sig in fired_signals:
                if daily_losses[date_str] >= DAILY_LOSS_CAP:
                    break

                # Same-coin cooldown check (60 minutes)
                current_trade_time = df.index[sig["idx"]]
                last_trade = _last_trade_time.get(symbol)
                if last_trade is not None:
                    hours_since_last = (current_trade_time - last_trade).total_seconds() / 3600
                    if hours_since_last < (SAME_COIN_COOLDOWN_MIN / 60):
                        logger.info(f"SKIP {symbol}: same-coin cooldown ({hours_since_last:.1f}h < 1h)")
                        continue

                trade = _simulate_trade(
                    df       = df,
                    i        = sig["idx"],
                    direction= sig["direction"],
                    score    = sig["score"],
                    reason   = sig["reason"],
                    symbol   = symbol,
                    day_date = date_str,
                )

                result_label = trade["result"]
                is_loss = "SL" in result_label or (result_label == "TIMEOUT" and trade["pnl_pct"] < 0)

                if is_loss:
                    daily_losses[date_str] += 1
                    consecutive_sl_hits    += 1
                else:
                    consecutive_sl_hits = 0

                logger.info(
                    f"TRADE | {symbol} {date_str} {trade['entry_time']} | "
                    f"{sig['direction']} | score={sig['score']} | {result_label} | "
                    f"entry={trade['entry']:.2f} exit={trade['exit']:.2f} | "
                    f"PnL={trade['pnl_pct']:.2f}% (${trade['pnl_usd_after_fee']:.2f}) | "
                    f"hold={trade['hold_hours']}h | {sig['reason'][:80]}"
                )

                all_results.append(trade)
                
                # Record trade time for cooldown
                _last_trade_time[symbol] = current_trade_time

        logger.info(
            f"=== SCAN DONE | {symbol} | "
            f"{len(all_results)} trades across {len(scan_dates)} day(s) ==="
        )
        return all_results

    except Exception as exc:
        import traceback
        logger.error(f"scan_daily_historical error for {symbol}: {exc}")
        logger.error(traceback.format_exc())
        return []
