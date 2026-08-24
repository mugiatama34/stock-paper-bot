"""
AI/Tech Momentum Strategy
--------------------------
Universe: large-cap AI/tech names.
Entry: price above SMA50, SMA20 > SMA50 (uptrend confirmed), and stock outperforming
       SPY over the last 20 sessions (relative strength).
Exit:  trend break (price closes below SMA50) or ATR-based trailing stop hit.
This is the higher-risk / higher-reward book of the three.
"""
from .indicators import sma, rsi, atr

UNIVERSE = [
    "NVDA", "MSFT", "GOOGL", "META", "AMD", "AVGO", "CRM", "PLTR", "SNOW", "SMCI",
]

NAME = "ai_momentum"


def evaluate(symbol: str, df, spy_df, has_position: bool):
    if len(df) < 55:
        return None

    close = df["Close"]
    s20 = sma(close, 20)
    s50 = sma(close, 50)
    a14 = atr(df, 14)
    price = close.iloc[-1]

    spy_return_20 = spy_df["Close"].iloc[-1] / spy_df["Close"].iloc[-21] - 1
    stock_return_20 = price / close.iloc[-21] - 1
    relative_strength = stock_return_20 - spy_return_20

    indicators = {
        "price": round(float(price), 2),
        "sma20": round(float(s20.iloc[-1]), 2),
        "sma50": round(float(s50.iloc[-1]), 2),
        "atr14": round(float(a14.iloc[-1]), 2),
        "relative_strength_20d": round(float(relative_strength), 4),
    }

    if not has_position:
        uptrend = price > s20.iloc[-1] > s50.iloc[-1]
        outperforming = relative_strength > 0.02  # beating SPY by 2%+ over 20 sessions
        if uptrend and outperforming:
            stop_price = float(price - 2 * a14.iloc[-1])
            reasoning = (
                f"Momentum girişi: fiyat SMA20 ({indicators['sma20']}) ve SMA50 "
                f"({indicators['sma50']}) üzerinde, SPY'a göre 20 günde %"
                f"{relative_strength*100:.1f} güçlü. Stop: {stop_price:.2f} (2xATR)."
            )
            return {"action": "BUY", "price": float(price), "stop_price": stop_price,
                     "reasoning": reasoning, "indicators": indicators}
        return None
    else:
        trend_broken = price < s50.iloc[-1]
        if trend_broken:
            reasoning = f"Trend kırıldı: fiyat ({indicators['price']}) SMA50 ({indicators['sma50']}) altına indi."
            return {"action": "SELL", "price": float(price),
                     "reasoning": reasoning, "indicators": indicators}
        return None
