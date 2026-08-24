"""
Balanced Strategy (moderate risk)
-----------------------------------
Universe: broad S&P 500 blue-chip subset.
Combines a trend filter with a neutral-momentum entry so we buy quality names that
are neither overbought nor in a downtrend — a middle ground between the aggressive
AI-momentum book and the purely mean-reversion book.
Entry: price above SMA200 (long-term uptrend) AND RSI(14) between 40-55 (healthy pullback,
       not oversold panic, not overbought) AND volume >= 20-day average volume (confirmation).
Exit:  RSI(14) > 70 (take profit into strength) or price closes below SMA200 (trend failed).
"""
from .indicators import sma, rsi, atr

UNIVERSE = [
    "BRK-B", "XOM", "JPM", "LLY", "PEP", "MRK", "ABBV", "CSCO", "ADBE", "TXN",
]

NAME = "balanced"


def evaluate(symbol: str, df, spy_df, has_position: bool):
    if len(df) < 210:
        return None

    close = df["Close"]
    volume = df["Volume"]
    s200 = sma(close, 200)
    r14 = rsi(close, 14)
    a14 = atr(df, 14)
    vol_avg20 = volume.rolling(20).mean()
    price = close.iloc[-1]

    indicators = {
        "price": round(float(price), 2),
        "sma200": round(float(s200.iloc[-1]), 2),
        "rsi14": round(float(r14.iloc[-1]), 2),
        "atr14": round(float(a14.iloc[-1]), 2),
        "volume_vs_avg20": round(float(volume.iloc[-1] / vol_avg20.iloc[-1]), 2),
    }

    if not has_position:
        uptrend = price > s200.iloc[-1]
        healthy_zone = 40 <= r14.iloc[-1] <= 55
        volume_confirmed = volume.iloc[-1] >= vol_avg20.iloc[-1]
        if uptrend and healthy_zone and volume_confirmed:
            stop_price = float(price - 2 * a14.iloc[-1])
            reasoning = (
                f"Dengeli giriş: uzun vadeli trend sağlam (SMA200={indicators['sma200']}), "
                f"RSI14={indicators['rsi14']} nötr bölgede, hacim ortalamanın "
                f"{indicators['volume_vs_avg20']}x üzerinde. Stop: {stop_price:.2f}."
            )
            return {"action": "BUY", "price": float(price), "stop_price": stop_price,
                     "reasoning": reasoning, "indicators": indicators}
        return None
    else:
        take_profit = r14.iloc[-1] > 70
        trend_failed = price < s200.iloc[-1]
        if take_profit or trend_failed:
            reason_txt = "RSI aşırı alımda (>70), kâr realizasyonu" if take_profit else \
                         "Uzun vadeli trend (SMA200) kırıldı, çıkış"
            reasoning = f"{reason_txt}. RSI14={indicators['rsi14']}, fiyat={indicators['price']}."
            return {"action": "SELL", "price": float(price),
                     "reasoning": reasoning, "indicators": indicators}
        return None
