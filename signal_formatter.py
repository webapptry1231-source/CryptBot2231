from config import STRONG_SIGNAL_THRESHOLD, WEAK_SIGNAL_THRESHOLD, TP_PERCENT, SL_PERCENT, LEVERAGE

def format_signal_message(symbol: str, score: int, reason: str, price: float) -> str | None:
    tp = round(price * (1 + TP_PERCENT / 100), 4)
    sl = round(price * (1 - SL_PERCENT / 100), 4)

    if score >= STRONG_SIGNAL_THRESHOLD:
        emoji = "🔥"
        label = "STRONG LONG"
    elif score >= WEAK_SIGNAL_THRESHOLD:
        emoji = "👀"
        label = "WEAK LONG"
    else:
        return None

    tv_link = f"https://www.tradingview.com/chart/?symbol=BYBIT:{symbol.replace('/', '')}"

    return (
        f"{emoji} {symbol} (15m Futures)\n"
        f"Score: {score}/100\n"
        f"Signal: {label} ({LEVERAGE}x)\n"
        f"Reason: {reason}\n"
        f"Entry: ~{price}\n"
        f"TP: {tp} (+{TP_PERCENT}%)\n"
        f"SL: {sl} (-{SL_PERCENT}%)\n"
        f"Chart: {tv_link}\n"
        f"⚠️ SIMULATION"
    )