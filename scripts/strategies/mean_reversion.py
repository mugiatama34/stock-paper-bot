"""
Mean-Reversion Strategy
------------------------
Universe: liquid large-caps in a long-term uptrend (SMA200 filter avoids catching
          falling knives — we only buy dips inside otherwise-healthy stocks).
Entry: price above SMA200 (long-term trend intact) AND RSI(14) < 32 (short-term oversold).
Exit:  RSI(14) > 55 (reversion complete) or price closes back below SMA200 (trend failed).
"""
from .indicators import sma, rsi, atr

UNIVERSE = [
    "AAPL", "JNJ", "PG", "KO", "COST", "V", "MA", "HD", "UNH", "WMT",
]

NAME = "mean_reversion"


def evaluate(symbol: str, df, spy_df, has_position: bool):
    if len(df) < 210:
        return None

    close = df["Close"]
    s200 = sma(close, 200)
    r14 = rsi(close, 14)
    a14 = atr(df, 14)
    price = close.iloc[-1]

    indicators = {
        "price": round(float(price), 2),
        "sma200": round(float(s200.iloc[-1]), 2),
        "rsi14": round(float(r14.iloc[-1]), 2),
        "atr14": round(float(a14.iloc[-1]), 2),
    }

    if not has_position:
        long_term_uptrend = price > s200.iloc[-1]
        oversold = r14.iloc[-1] < 32
        if long_term_uptrend and oversold:
            stop_price = float(price - 2.5 * a14.iloc[-1])
            reasoning = (
                f"Aşırı satım dönüşü: RSI14={indicators['rsi14']} (<32), fiyat uzun vadeli "
                f"trend (SMA200={indicators['sma200']}) üzerinde kalmaya devam ediyor. "
                f"Stop: {stop_price:.2f}."
            )
            return {"action": "BUY", "price": float(price), "stop_price": stop_price,
                     "reasoning": reasoning, "indicators": indicators}
        return None
    else:
        reverted = r14.iloc[-1] > 55
        trend_failed = price < s200.iloc[-1]
        if reverted or trend_failed:
            reason_txt = "RSI toparlandı (>55), pozisyon kapatıldı" if reverted else \
                         "Uzun vadeli trend (SMA200) kırıldı, çıkış"
            reasoning = f"{reason_txt}. RSI14={indicators['rsi14']}, fiyat={indicators['price']}."
            return {"action": "SELL", "price": float(price),
                     "reasoning": reasoning, "indicators": indicators}
        return None
