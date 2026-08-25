"""
Balanced Strategy  (moderate risk)
------------------------------------
Universe: S&P 500, filtered to names with >=2M average daily volume.

Entry (all must hold):
  - price > SMA200                   long-term uptrend
  - RSI(14) between 40 and 55        healthy pullback, not panic, not froth
  - volume >= 20-day average         participation confirms the move

Exit:
  - RSI(14) > 70   -> SELL HALF once, then let the rest run on the trailing stop
  - price < SMA200 -> exit whatever remains
  - trailing stop hit                handled centrally (peak - 2*ATR)

Scaling out rather than closing at RSI 70 is the change: in a strong bull leg a
quality name can hold RSI above 70 for weeks while it keeps climbing, so a full
exit there leaves most of the move on the table. Half locks in the gain, half stays
exposed with a stop underneath it.
"""
from .indicators import sma, rsi, atr

NAME = "balanced"
UNIVERSE_KEY = "sp500"
ATR_MULT = 2.0
RSI_ENTRY_LOW = 40
RSI_ENTRY_HIGH = 55
RSI_SCALE_OUT = 70


def evaluate(symbol: str, df, spy_df, has_position: bool, position=None):
    if len(df) < 210:
        return None

    close = df["Close"]
    volume = df["Volume"]
    s200 = sma(close, 200)
    r14 = rsi(close, 14)
    a14 = atr(df, 14)
    vol_avg20 = volume.rolling(20).mean()
    price = float(close.iloc[-1])
    rsi_now = float(r14.iloc[-1])

    indicators = {
        "price": round(price, 2),
        "sma200": round(float(s200.iloc[-1]), 2),
        "rsi14": round(rsi_now, 2),
        "atr14": round(float(a14.iloc[-1]), 2),
        "volume_vs_avg20": round(float(volume.iloc[-1] / vol_avg20.iloc[-1]), 2),
    }

    if not has_position:
        uptrend = price > s200.iloc[-1]
        healthy_zone = RSI_ENTRY_LOW <= rsi_now <= RSI_ENTRY_HIGH
        volume_confirmed = volume.iloc[-1] >= vol_avg20.iloc[-1]
        if uptrend and healthy_zone and volume_confirmed:
            stop_price = price - ATR_MULT * float(a14.iloc[-1])
            reasoning = (
                f"Dengeli giriş: uzun vadeli trend sağlam (SMA200={indicators['sma200']}), "
                f"RSI14={indicators['rsi14']} nötr bölgede, hacim ortalamanın "
                f"{indicators['volume_vs_avg20']}x üzerinde. İzleyen stop: {stop_price:.2f}."
            )
            return {"action": "BUY", "price": price, "stop_price": stop_price,
                    "reasoning": reasoning, "indicators": indicators}
        return None

    # Trend failure closes the whole position, regardless of what was scaled earlier.
    if price < s200.iloc[-1]:
        reasoning = (f"Uzun vadeli trend (SMA200={indicators['sma200']}) kırıldı, "
                     f"kalan pozisyon kapatıldı. RSI14={indicators['rsi14']}.")
        return {"action": "SELL", "price": price, "reasoning": reasoning, "indicators": indicators}

    # Scale out once at RSI 70; the remainder rides the trailing stop.
    already_scaled = bool(position and position.get("partial_taken"))
    if rsi_now > RSI_SCALE_OUT and not already_scaled:
        qty_to_sell = max(1, (position["qty"] // 2) if position else 1)
        reasoning = (
            f"RSI aşırı alımda ({indicators['rsi14']} > {RSI_SCALE_OUT}): pozisyonun "
            f"yarısı satılarak kâr kilitlendi, kalan yarı izleyen stop ile sürüyor."
        )
        return {"action": "SELL_PARTIAL", "price": price, "qty": qty_to_sell,
                "reasoning": reasoning, "indicators": indicators}
    return None
