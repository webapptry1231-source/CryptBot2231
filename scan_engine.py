import logging
import pandas as pd
from data_fetcher import fetch_historical_ohlcv
from indicators import compute_indicators
from scorer import calculate_score
from config import (TIMEFRAME, TIMEFRAME_4H, WEAK_SIGNAL_THRESHOLD,
                    TP_LONG_PERCENT, SL_LONG_PERCENT, TRAIL_ACTIVATE_LONG, 
                    TP_SHORT_PERCENT, SL_SHORT_PERCENT, TRAIL_ACTIVATE_SHORT,
                    MAX_HOLD_CANDLES_LONG, MAX_HOLD_CANDLES_SHORT,
                    TRAILING_STOP_PERCENT, LEVERAGE, BUY_AMOUNT,
                    DAILY_TRADE_CAP, SIGNAL_COOLDOWN_HOURS, TRADE_HOURS_START, TRADE_HOURS_END,
                    NEUTRAL_ZONE_PCT, TRADE_DAYS_BLOCKED,
                    SL_OVERRIDES, MAX_CONCURRENT_TRADES,
                    FEE_PERCENT, DAILY_LOSS_CAP, CONSECUTIVE_SL_STOP)

logger = logging.getLogger(__name__)

_4h_cache = {}

def get_4h_cached(symbol: str, days_back: int, end_time: pd.Timestamp) -> pd.DataFrame:
    cache_key = f"{symbol}_{days_back}"
    if cache_key not in _4h_cache:
        logger.info(f"Fetching {days_back} days of 4h data for {symbol} (cache miss)")
        _4h_cache[cache_key] = fetch_historical_ohlcv(symbol, timeframe=TIMEFRAME_4H, days_back=days_back)
    df = _4h_cache[cache_key].copy()
    df = df[df.index <= end_time]
    return df

# ── check_4h_trend accepts optional as_of_time so it works both for
#    historical scanning (as_of_time given) and live scanning (no time arg).
def check_4h_trend(symbol: str, as_of_time: pd.Timestamp = None) -> bool:
    try:
        days_back = 60
        if as_of_time is None:
            as_of_time = pd.Timestamp.now(tz='UTC')
        df_4h = get_4h_cached(symbol, days_back, as_of_time)
        if len(df_4h) < 50:
            return True
        df_4h = compute_indicators(df_4h)
        latest   = df_4h.iloc[-1]
        ema_val  = float(latest['EMA_200'])
        close_val = float(latest['close'])
        result = close_val > ema_val
        logger.info(
            f"  4h check at {as_of_time}: EMA200={ema_val:.2f}, "
            f"close={close_val:.2f} -> {'BULLISH' if result else 'BEARISH'}"
        )
        return result
    except Exception as e:
        logger.warning(f"4h trend check failed: {e}")
        return True  # default to allowing trades if 4h check errors


def determine_regime(df: pd.DataFrame, as_of_time: pd.Timestamp = None) -> str:
    """
    Determine market regime based on r9.txt:
    - LONG: Price > EMA200 AND EMA50 > EMA200 OR 4h_bullish
    - SHORT: Price < EMA200 AND EMA50 < EMA200 OR 4h_bearish
    - NEUTRAL: Price within 0.5% of EMA200 (sleep mode)
    """
    if len(df) < 2:
        return "LONG"  # Default to LONG if insufficient data
    
    df = compute_indicators(df)
    latest = df.iloc[-1]
    
    close = float(latest['close'])
    ema200 = float(latest['EMA_200'])
    ema50 = float(latest['EMA_50'])
    
    # Check Neutral Zone (within 0.5% of EMA200)
    pct_from_ema = abs(close - ema200) / ema200 * 100
    if pct_from_ema <= NEUTRAL_ZONE_PCT:
        return "NEUTRAL"
    
    # Check Long conditions
    long_condition = (close > ema200 and ema50 > ema200)
    
    # Check Short conditions
    short_condition = (close < ema200 and ema50 < ema200)
    
    # Get 4h trend
    if as_of_time is not None:
        trend_bullish = check_4h_trend("BTC/USDT", as_of_time)
    else:
        trend_bullish = True
    
    # Apply 4h trend override
    if long_condition or trend_bullish:
        return "LONG"
    elif short_condition or not trend_bullish:
        return "SHORT"
    
    # Default to NEUTRAL if unclear
    return "NEUTRAL"


def scan_daily_historical(symbol: str, days: int) -> list:
    global _4h_cache
    _4h_cache.clear()

    try:
        logger.info(f"=== Starting scan for {symbol}, {days} days ===")
        df = fetch_historical_ohlcv(symbol, timeframe=TIMEFRAME, days_back=days)
        logger.info(f"Fetched {len(df)} candles")

        if len(df) < 100:
            logger.warning(f"Not enough data: {len(df)}")
            return []

        df = compute_indicators(df)
        logger.info(f"Indicators computed: {len(df)} rows")

        results       = []
        total_candles = len(df)

        last_check_time = None
        trend_bullish   = True

        trades_per_day  = {}
        last_signal_time = {}
        position_open   = False
        total_scanned   = 0
        signals_found   = 0

        # debug counters to diagnose filter attrition
        dbg_hours_filtered   = 0
        dbg_position_skipped = 0
        dbg_score_rejected   = 0
        dbg_cooldown_skipped = 0
        dbg_loss_cap_skipped = 0
        dbg_day_filtered     = 0
        dbg_neutral_filtered = 0
        dbg_long_skipped     = 0
        dbg_short_skipped    = 0

        # Track consecutive SL hits for auto-stop
        consecutive_sl_hits = 0
        
        # Track current regime
        current_regime = "LONG"
        regime_check_time = None

        logger.info(
            f"Scan config: WEAK_THRESHOLD={WEAK_SIGNAL_THRESHOLD}, "
            f"COOLDOWN={SIGNAL_COOLDOWN_HOURS}h, "
            f"TRADE_HOURS={TRADE_HOURS_START}-{TRADE_HOURS_END}UTC, "
            f"DAILY_CAP={DAILY_TRADE_CAP}, LOSS_CAP={DAILY_LOSS_CAP}"
        )

        for i in range(24, total_candles - MAX_HOLD_CANDLES_LONG):
            current_time = df.index[i]

            # refresh regime every 4 hours
            if regime_check_time is None or \
               (current_time - regime_check_time).total_seconds() / 3600 >= 4:
                current_regime = determine_regime(df, current_time)
                regime_check_time = current_time
                logger.info(f"Regime at {current_time}: {current_regime}")

            # ── FILTER: trade hours ───────────────────────────────────────────
            if current_time.hour < TRADE_HOURS_START or current_time.hour >= TRADE_HOURS_END:
                dbg_hours_filtered += 1
                total_scanned += 1
                continue

            # ── FILTER: blocked days (Mon=0, Sat=5) ─────────────────────────────
            if current_time.weekday() in TRADE_DAYS_BLOCKED:
                dbg_day_filtered += 1
                total_scanned += 1
                continue

            # ── FILTER: Neutral Zone (sleep mode) ───────────────────────────────
            if current_regime == "NEUTRAL":
                dbg_neutral_filtered += 1
                total_scanned += 1
                continue

            # ── FILTER: preferred window 16-20 UTC (log preference) ───────────────
            # (For informational purposes only - not blocking)

            total_scanned += 1

            if total_scanned % 500 == 0:
                logger.info(
                    f"Progress {total_scanned}/{total_candles} | "
                    f"signals={signals_found} | "
                    f"filtered: hours={dbg_hours_filtered} pos={dbg_position_skipped} "
                    f"score={dbg_score_rejected} cool={dbg_cooldown_skipped} "
                    f"losscap={dbg_loss_cap_skipped}"
                )

            # ── FILTER: existing position ─────────────────────────────────────
            if position_open:
                dbg_position_skipped += 1
                continue

            # Determine direction based on regime
            if current_regime == "LONG":
                direction = "LONG"
            elif current_regime == "SHORT":
                direction = "SHORT"
            else:
                continue  # NEUTRAL - should not reach here due to filter above

            window = df.iloc[:i]
            if len(window) < 50:
                continue

            score, reason = calculate_score(window, direction=direction)

            # detailed debug on first 10 in-hours candles
            if total_scanned <= 10:
                logger.info(
                    f"[DEBUG] candle {i} [{current_time}]: "
                    f"regime={current_regime}, score={score}, reason='{reason}'"
                )

            # ── FILTER: score threshold ───────────────────────────────────────
            if score < WEAK_SIGNAL_THRESHOLD:
                dbg_score_rejected += 1
                continue

            # ── FILTER: high score caution (score > 80 = exhaustion signal) ─────
            if score > 80:
                logger.info(f"Candle {i}: high score {score} - CAUTION: possible exhaustion")
                # Continue but log warning

            # ── FILTER: daily loss cap ────────────────────────────────────────
            day_date = str(current_time)[:10]
            if day_date in trades_per_day and \
               trades_per_day[day_date].get("losses", 0) >= DAILY_LOSS_CAP:
                dbg_loss_cap_skipped += 1
                continue

            # ── FILTER: cooldown ──────────────────────────────────────────────
            if symbol in last_signal_time:
                hours_since = (current_time - last_signal_time[symbol]).total_seconds() / 3600
                if hours_since < SIGNAL_COOLDOWN_HOURS:
                    dbg_cooldown_skipped += 1
                    continue

            # ─────────────────────────────────────────────────────────────────
            # SIGNAL ACCEPTED
            # ─────────────────────────────────────────────────────────────────
            signals_found += 1
            position_open  = True
            last_signal_time[symbol] = current_time
            
            # Determine params based on direction
            if direction == "LONG":
                tp_percent = TP_LONG_PERCENT
                sl_percent = SL_OVERRIDES.get(symbol, SL_LONG_PERCENT)
                trail_activate = TRAIL_ACTIVATE_LONG
                max_hold = MAX_HOLD_CANDLES_LONG
            else:  # SHORT
                tp_percent = TP_SHORT_PERCENT
                sl_percent = SL_OVERRIDES.get(symbol, SL_SHORT_PERCENT)
                trail_activate = TRAIL_ACTIVATE_SHORT
                max_hold = MAX_HOLD_CANDLES_SHORT

            entry_price = window.iloc[-1]['close'] * 1.001   # +0.1% slippage

            hold_candles = min(max_hold, total_candles - i - 1)
            if hold_candles < 1:
                position_open = False
                continue

            future = df.iloc[i:i + hold_candles]
            if len(future) == 0:
                position_open = False
                continue

            mfe_pct = ((future['high'].max() - entry_price) / entry_price) * 100
            mae_pct = ((entry_price - future['low'].min()) / entry_price) * 100

            tp_price        = entry_price * (1 + tp_percent / 100)
            sl_price        = entry_price * (1 - sl_percent / 100)
            partial_tp_price = entry_price * (1 + trail_activate / 100)

            partial_tp_hit = False
            trailing_sl    = sl_price
            running_high   = entry_price
            running_low    = entry_price
            trade_closed   = False
            exit_price     = entry_price
            exit_time      = current_time
            pnl_pct        = 0.0
            result         = "PENDING"

            for j in range(len(future)):
                candle = future.iloc[j]
                
                if direction == "LONG":
                    running_high = max(running_high, candle['high'])
                    if not partial_tp_hit and candle['high'] >= partial_tp_price:
                        partial_tp_hit = True
                        trailing_sl = entry_price  # move SL to breakeven
                    if partial_tp_hit:
                        new_trail = running_high * (1 - TRAILING_STOP_PERCENT / 100)
                        trailing_sl = max(trailing_sl, new_trail)
                    # LONG: TP on high, SL on low
                    if partial_tp_hit and candle['high'] >= tp_price:
                        pnl_pct    = tp_percent * LEVERAGE
                        result     = "TP HIT"
                        exit_price = tp_price
                        exit_time  = future.index[j]
                        trade_closed = True
                        break
                    if candle['low'] <= trailing_sl:
                        if partial_tp_hit:
                            pnl_pct = ((trailing_sl - entry_price) / entry_price) * 100 * LEVERAGE
                            result  = "TRAIL STOP"
                        else:
                            pnl_pct = -sl_percent * LEVERAGE
                            result  = "SL HIT"
                        exit_price = trailing_sl
                        exit_time  = future.index[j]
                        trade_closed = True
                        break
                else:  # SHORT
                    running_low = min(running_low, candle['low']) if j > 0 else candle['low']
                    if not partial_tp_hit and candle['low'] <= partial_tp_price:
                        partial_tp_hit = True
                        trailing_sl = entry_price  # move SL to breakeven
                    if partial_tp_hit:
                        new_trail = running_low * (1 + TRAILING_STOP_PERCENT / 100)
                        trailing_sl = min(trailing_sl, new_trail)
                    # SHORT: TP on low, SL on high
                    if partial_tp_hit and candle['low'] <= tp_price:
                        pnl_pct    = tp_percent * LEVERAGE
                        result     = "TP HIT"
                        exit_price = tp_price
                        exit_time  = future.index[j]
                        trade_closed = True
                        break
                    if candle['high'] >= trailing_sl:
                        if partial_tp_hit:
                            pnl_pct = ((entry_price - trailing_sl) / entry_price) * 100 * LEVERAGE
                            result  = "TRAIL STOP"
                        else:
                            pnl_pct = -sl_percent * LEVERAGE
                            result  = "SL HIT"
                        exit_price = trailing_sl
                        exit_time  = future.index[j]
                        trade_closed = True
                        break

            if not trade_closed:
                timeout_candle = df.iloc[min(i + hold_candles - 1, len(df) - 1)]
                exit_price = timeout_candle['close']
                exit_time  = timeout_candle.name
                if direction == "LONG":
                    pnl_pct = ((exit_price - entry_price) / entry_price) * 100 * LEVERAGE
                else:  # SHORT
                    pnl_pct = ((entry_price - exit_price) / entry_price) * 100 * LEVERAGE
                result     = "TIMEOUT"

            position_open = False

            hold_hours       = (exit_time - current_time).total_seconds() / 3600
            fee_pct          = FEE_PERCENT * 2
            pnl_after_fee    = pnl_pct - fee_pct
            pnl_usd          = (BUY_AMOUNT * LEVERAGE) * (pnl_pct / 100)
            pnl_usd_after_fee = (BUY_AMOUNT * LEVERAGE) * (pnl_after_fee / 100)

            if "SL" in result or ("TIMEOUT" in result and pnl_pct < 0):
                if day_date not in trades_per_day:
                    trades_per_day[day_date] = {"count": 0, "losses": 0}
                trades_per_day[day_date]["losses"] += 1
                consecutive_sl_hits += 1
            else:
                consecutive_sl_hits = 0

            # ── FILTER: consecutive SL auto-stop ─────────────────────────────────
            if consecutive_sl_hits >= CONSECUTIVE_SL_STOP:
                logger.warning(f"Auto-stop: {consecutive_sl_hits} consecutive SL hits, pausing bot")
                break

            logger.info(
                f"TRADE #{signals_found} | {symbol} {day_date} {str(current_time)[11:16]} | "
                f"score={score} | {result} | "
                f"entry={entry_price:.2f} exit={exit_price:.2f} | "
                f"PnL={pnl_pct:.2f}% (${pnl_usd_after_fee:.2f}) | "
                f"hold={hold_hours:.1f}h | reason: {reason}"
            )

            results.append({
                "date":             day_date,
                "entry_time":       str(current_time)[11:16],
                "exit_time":        str(exit_time)[11:16],
                "score":            score,
                "reason":           reason,
                "entry":            round(entry_price, 2),
                "exit":             round(exit_price, 2),
                "tp":               round(tp_price, 2),
                "sl":               round(sl_price, 2),
                "result":           result,
                "pnl_pct":          round(pnl_pct, 2),
                "pnl_after_fee":    round(pnl_after_fee, 2),
                "pnl_usd":          round(pnl_usd, 2),
                "pnl_usd_after_fee": round(pnl_usd_after_fee, 2),
                "leverage":         LEVERAGE,
                "buy_amount":       BUY_AMOUNT,
                "hold_hours":       round(hold_hours, 1),
                "mfe_pct":          round(mfe_pct, 2),
                "mae_pct":          round(mae_pct, 2),
            })

        logger.info(
            f"=== SCAN COMPLETE: {total_scanned} candles scanned, "
            f"{signals_found} signals fired, {len(results)} trades recorded ===\n"
            f"  Filter breakdown: hours={dbg_hours_filtered} "
            f"pos_open={dbg_position_skipped} "
            f"score<{WEAK_SIGNAL_THRESHOLD}={dbg_score_rejected} "
            f"cooldown={dbg_cooldown_skipped} "
            f"loss_cap={dbg_loss_cap_skipped} "
            f"days={dbg_day_filtered} neutral={dbg_neutral_filtered}"
        )
        return results

    except Exception as e:
        logger.error(f"Error in scan: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return []
