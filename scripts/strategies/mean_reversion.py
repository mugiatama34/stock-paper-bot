"""
Mean-Reversion Strategy  (moderate-low risk)
---------------------------------------------
Universe: S&P 500, filtered to names with >=2M average daily volume.

Entry (all must hold):
  - price > SMA200                   long-term trend intact
  - RSI(14) crossed back ABOVE 32    reversal confirmed, not a falling knife

The cross-up is the key change. Buying the moment RSI dips under 32 catches every
stock that is simply in free-fall -- RSI can sit under 32 for days while price keeps
sliding. Requiring the cross means we wait for the bounce to actually start.

Exit:
  - RSI(14) > 55                     reversion played out
  - price closes below SMA200        trend failed
  - trailing stop hit                handled centrally (peak - 2.5*ATR)

Signal score (BUY only): RSI_OVERSOLD - rsi_prev (how far below the oversold
threshold RSI dipped before crossing back up -- depth of the dip). Deeper dips
rank first when cash is limited across candidates.
"""
from .indicators import sma, rsi, atr

NAME = "mean_reversion"
UNIVERSE_KEY = "sp500"
ATR_MULT = 2.5
RSI_OVERSOLD = 32
RSI_EXIT = 55


def evaluate(symbol: str, df, spy_df, has_position: bool, position=None):
    if len(df) < 210:
        return None

    close = df["Close"]
    s200 = sma(close, 200)
    r14 = rsi(close, 14)
    a14 = atr(df, 14)
    price = float(close.iloc[-1])
    rsi_now = float(r14.iloc[-1])
    rsi_prev = float(r14.iloc[-2])

    indicators = {
        "price": round(price, 2),
        "sma200": round(float(s200.iloc[-1]), 2),
        "rsi14": round(rsi_now, 2),
        "rsi14_prev": round(rsi_prev, 2),
        "atr14": round(float(a14.iloc[-1]), 2),
    }

    if not has_position:
        long_term_uptrend = price > s200.iloc[-1]
        # Reversal: was oversold yesterday, has crossed back above the threshold today.
        crossed_up = rsi_prev < RSI_OVERSOLD <= rsi_now
        if long_term_uptrend and crossed_up:
            stop_price = price - ATR_MULT * float(a14.iloc[-1])
            reasoning = (
                f"Dönüş teyitli giriş: RSI14 {indicators['rsi14_prev']} → {indicators['rsi14']} "
                f"({RSI_OVERSOLD} eşiğini yukarı kırdı), fiyat uzun vadeli trend "
                f"(SMA200={indicators['sma200']}) üzerinde. İzleyen stop: {stop_price:.2f}."
            )
            return {"action": "BUY", "price": price, "stop_price": stop_price,
                    "reasoning": reasoning, "indicators": indicators,
                    "score": RSI_OVERSOLD - rsi_prev}
        return None

    reverted = rsi_now > RSI_EXIT
    trend_failed = price < s200.iloc[-1]
    if reverted or trend_failed:
        reason_txt = (f"RSI toparlandı (>{RSI_EXIT}), dönüş tamamlandı" if reverted
                      else "Uzun vadeli trend (SMA200) kırıldı, çıkış")
        reasoning = f"{reason_txt}. RSI14={indicators['rsi14']}, fiyat={indicators['price']}."
        return {"action": "SELL", "price": price, "reasoning": reasoning, "indicators": indicators}
    return None
