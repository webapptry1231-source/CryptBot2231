from config import (STRONG_SIGNAL_THRESHOLD, WEAK_SIGNAL_THRESHOLD, 
                    TP_LONG_PERCENT, TP_SHORT_PERCENT, 
                    SL_LONG_PERCENT, SL_SHORT_PERCENT, LEVERAGE)

def format_signal_message(symbol: str, score: int, reason: str, price: float, direction: str = "LONG") -> str | None:
    if direction == "LONG":
        tp = round(price * (1 + TP_LONG_PERCENT / 100), 4)
        sl = round(price * (1 - SL_LONG_PERCENT / 100), 4)
        tp_pct = TP_LONG_PERCENT
    else:
        tp = round(price * (1 - TP_SHORT_PERCENT / 100), 4)
        sl = round(price * (1 + SL_SHORT_PERCENT / 100), 4)
        tp_pct = TP_SHORT_PERCENT

    direction = direction.upper()
    
    if score >= STRONG_SIGNAL_THRESHOLD:
        emoji = "🔥" if direction == "LONG" else "💀"
        label = f"{emoji} STRONG {direction}"
    elif score >= WEAK_SIGNAL_THRESHOLD:
        emoji = "👀" if direction == "LONG" else "⚠️"
        label = f"{emoji} WEAK {direction}"
    else:
        return None

    tv_link = f"https://www.tradingview.com/chart/?symbol=BYBIT:{symbol.replace('/', '')}"

    return (
        f"{label} {symbol} (15m Futures)\n"
        f"Score: {score}/100\n"
        f"Signal: {label} ({LEVERAGE}x)\n"
        f"Reason: {reason}\n"
        f"Entry: ~{price}\n"
        f"TP: {tp} (+{tp_pct}%)\n"
        f"SL: {sl}\n"
        f"Chart: {tv_link}\n"
        f"⚠️ SIMULATION"
    )