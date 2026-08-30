"""
Mean-Reversion (Bollinger) Strategy  — BACKTEST-ONLY, canlıda çalışmaz
------------------------------------------------------------------------
mean_reversion.py'nin varyasyonu: yalnızca giriş sinyali farklı. Çıkış kuralı,
stop mantığı, pozisyon boyutlandırma, rejim filtresi ve SMA200 ön koşulu
mean_reversion.py ile birebir aynı.

Universe: S&P 500, filtered to names with >=2M average daily volume.

Entry (all must hold):
  - price > SMA200                        long-term trend intact
  - dün Bollinger alt bandına değindi      aşırı satım
  - bugün fiyat alt bandın üzerine döndü   geri dönüş teyitli

mean_reversion.py'deki RSI cross-up mantığının ("dün eşiğin altında, bugün
üstüne geçti") birebir yapısal analogu: dün alt bant temas edildi, bugün
fiyat bandın üzerine geri döndü.

Exit:
  - RSI(14) > 55                     reversion played out
  - price closes below SMA200        trend failed
  - trailing stop hit                handled centrally (peak - 2.5*ATR)
"""
from .indicators import sma, rsi, atr, bollinger

NAME = "mr_bollinger"
UNIVERSE_KEY = "sp500"
ATR_MULT = 2.5
RSI_EXIT = 55
BB_WINDOW = 20
BB_STD = 2.0


def evaluate(symbol: str, df, spy_df, has_position: bool, position=None):
    if len(df) < 210:
        return None

    close = df["Close"]
    s200 = sma(close, 200)
    r14 = rsi(close, 14)
    a14 = atr(df, 14)
    bb_upper, bb_mid, bb_lower = bollinger(close, BB_WINDOW, BB_STD)
    price = float(close.iloc[-1])
    rsi_now = float(r14.iloc[-1])

    indicators = {
        "price": round(price, 2),
        "sma200": round(float(s200.iloc[-1]), 2),
        "rsi14": round(rsi_now, 2),
        "atr14": round(float(a14.iloc[-1]), 2),
        "bb_lower": round(float(bb_lower.iloc[-1]), 2),
        "bb_mid": round(float(bb_mid.iloc[-1]), 2),
    }

    if not has_position:
        long_term_uptrend = price > s200.iloc[-1]
        touched_lower_prev = float(df["Low"].iloc[-2]) <= float(bb_lower.iloc[-2])
        bounced_back = price > float(bb_lower.iloc[-1])
        if long_term_uptrend and touched_lower_prev and bounced_back:
            stop_price = price - ATR_MULT * float(a14.iloc[-1])
            reasoning = (
                f"Bollinger dönüşü: dün alt bant ({indicators['bb_lower']}) test edildi, "
                f"bugün fiyat ({indicators['price']}) bandın üzerine döndü, fiyat uzun "
                f"vadeli trend (SMA200={indicators['sma200']}) üzerinde. "
                f"İzleyen stop: {stop_price:.2f}."
            )
            return {"action": "BUY", "price": price, "stop_price": stop_price,
                    "reasoning": reasoning, "indicators": indicators}
        return None

    reverted = rsi_now > RSI_EXIT
    trend_failed = price < s200.iloc[-1]
    if reverted or trend_failed:
        reason_txt = (f"RSI toparlandı (>{RSI_EXIT}), dönüş tamamlandı" if reverted
                      else "Uzun vadeli trend (SMA200) kırıldı, çıkış")
        reasoning = f"{reason_txt}. RSI14={indicators['rsi14']}, fiyat={indicators['price']}."
        return {"action": "SELL", "price": price, "reasoning": reasoning, "indicators": indicators}
    return None
