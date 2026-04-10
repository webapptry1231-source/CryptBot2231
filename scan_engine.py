import logging
import pandas as pd
from data_fetcher import fetch_historical_ohlcv
from indicators import compute_indicators
from scorer import calculate_score
from config import (TIMEFRAME, TIMEFRAME_4H, MAX_HOLD_CANDLES, WEAK_SIGNAL_THRESHOLD,
                    TP_PERCENT, SL_PERCENT, LEVERAGE, BUY_AMOUNT,
                    DAILY_TRADE_CAP, SIGNAL_COOLDOWN_HOURS, TRADE_HOURS_START, TRADE_HOURS_END,
                    SL_OVERRIDES, MAX_CONCURRENT_TRADES,
                    PARTIAL_TP_PERCENT, PARTIAL_TP_SIZE, TRAILING_STOP_PERCENT, FEE_PERCENT,
                    DAILY_LOSS_CAP)

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

        logger.info(
            f"Scan config: WEAK_THRESHOLD={WEAK_SIGNAL_THRESHOLD}, "
            f"COOLDOWN={SIGNAL_COOLDOWN_HOURS}h, "
            f"TRADE_HOURS={TRADE_HOURS_START}-{TRADE_HOURS_END}UTC, "
            f"DAILY_CAP={DAILY_TRADE_CAP}, LOSS_CAP={DAILY_LOSS_CAP}"
        )

        for i in range(24, total_candles - MAX_HOLD_CANDLES):
            current_time = df.index[i]

            # refresh 4h trend every 4 hours
            if last_check_time is None or \
               (current_time - last_check_time).total_seconds() / 3600 >= 4:
                trend_bullish   = check_4h_trend(symbol, current_time)
                last_check_time = current_time

            # ── FILTER: trade hours ───────────────────────────────────────────
            if current_time.hour < TRADE_HOURS_START or current_time.hour >= TRADE_HOURS_END:
                dbg_hours_filtered += 1
                total_scanned += 1
                continue

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

            trend_bonus = 10 if trend_bullish else 0

            window = df.iloc[:i]
            if len(window) < 50:
                continue

            score, reason = calculate_score(window, trend_bonus=trend_bonus)

            # detailed debug on first 10 in-hours candles
            if total_scanned <= 10:
                logger.info(
                    f"[DEBUG] candle {i} [{current_time}]: "
                    f"score={score}, trend={'bull' if trend_bullish else 'bear'}, "
                    f"reason='{reason}'"
                )

            # ── FILTER: score threshold ───────────────────────────────────────
            if score < WEAK_SIGNAL_THRESHOLD:
                dbg_score_rejected += 1
                continue

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

            entry_price = window.iloc[-1]['close'] * 1.001   # +0.1% slippage

            hold_candles = min(MAX_HOLD_CANDLES, total_candles - i - 1)
            if hold_candles < 1:
                position_open = False
                continue

            future = df.iloc[i:i + hold_candles]
            if len(future) == 0:
                position_open = False
                continue

            mfe_pct = ((future['high'].max() - entry_price) / entry_price) * 100
            mae_pct = ((entry_price - future['low'].min()) / entry_price) * 100

            sl_percent      = SL_OVERRIDES.get(symbol, SL_PERCENT)
            tp_price        = entry_price * (1 + TP_PERCENT / 100)
            sl_price        = entry_price * (1 - sl_percent / 100)
            partial_tp_price = entry_price * (1 + PARTIAL_TP_PERCENT / 100)

            partial_tp_hit = False
            trailing_sl    = sl_price
            running_high   = entry_price
            trade_closed   = False
            exit_price     = entry_price
            exit_time      = current_time
            pnl_pct        = 0.0
            result         = "PENDING"

            for j in range(len(future)):
                candle = future.iloc[j]
                running_high = max(running_high, candle['high'])

                if not partial_tp_hit and candle['high'] >= partial_tp_price:
                    partial_tp_hit = True
                    trailing_sl = entry_price  # move SL to breakeven

                if partial_tp_hit:
                    new_trail = running_high * (1 - TRAILING_STOP_PERCENT / 100)
                    trailing_sl = max(trailing_sl, new_trail)

                if partial_tp_hit and candle['high'] >= tp_price:
                    pnl_pct    = TP_PERCENT * LEVERAGE
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

            if not trade_closed:
                timeout_candle = df.iloc[min(i + hold_candles - 1, len(df) - 1)]
                exit_price = timeout_candle['close']
                exit_time  = timeout_candle.name
                pnl_pct    = ((exit_price - entry_price) / entry_price) * 100 * LEVERAGE
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
            f"loss_cap={dbg_loss_cap_skipped}"
        )
        return results

    except Exception as e:
        logger.error(f"Error in scan: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return []
