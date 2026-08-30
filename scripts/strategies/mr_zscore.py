"""
Mean-Reversion (Z-Score) Strategy  — BACKTEST-ONLY, canlıda çalışmaz
------------------------------------------------------------------------
mean_reversion.py'nin varyasyonu: yalnızca giriş sinyali farklı. Çıkış kuralı,
stop mantığı, pozisyon boyutlandırma, rejim filtresi ve SMA200 ön koşulu
mean_reversion.py ile birebir aynı.

Universe: S&P 500, filtered to names with >=2M average daily volume.

Entry (all must hold):
  - price > SMA200                        long-term trend intact
  - 20-günlük rolling z-score < -2 iken   aşırı satım
    -2 eşiğini yukarı kesti               reversal confirmed

mean_reversion.py'deki RSI cross-up mantığının ("dün eşiğin altında, bugün
üstüne geçti") birebir aynı yapısı, RSI yerine z-score üzerinden uygulanır.

Exit:
  - RSI(14) > 55                     reversion played out
  - price closes below SMA200        trend failed
  - trailing stop hit                handled centrally (peak - 2.5*ATR)
"""
from .indicators import sma, rsi, atr, zscore

NAME = "mr_zscore"
UNIVERSE_KEY = "sp500"
ATR_MULT = 2.5
RSI_EXIT = 55
ZSCORE_WINDOW = 20
ZSCORE_ENTRY = -2.0


def evaluate(symbol: str, df, spy_df, has_position: bool, position=None):
    if len(df) < 210:
        return None

    close = df["Close"]
    s200 = sma(close, 200)
    r14 = rsi(close, 14)
    a14 = atr(df, 14)
    z = zscore(close, ZSCORE_WINDOW)
    price = float(close.iloc[-1])
    rsi_now = float(r14.iloc[-1])
    z_now = float(z.iloc[-1])
    z_prev = float(z.iloc[-2])

    indicators = {
        "price": round(price, 2),
        "sma200": round(float(s200.iloc[-1]), 2),
        "rsi14": round(rsi_now, 2),
        "atr14": round(float(a14.iloc[-1]), 2),
        "zscore20": round(z_now, 2),
    }

    if not has_position:
        long_term_uptrend = price > s200.iloc[-1]
        # Reversal: was oversold (z < -2) yesterday, crossed back above -2 today.
        crossed_up = z_prev < ZSCORE_ENTRY <= z_now
        if long_term_uptrend and crossed_up:
            stop_price = price - ATR_MULT * float(a14.iloc[-1])
            reasoning = (
                f"Z-score dönüşü: z-score {z_prev:.2f} → {z_now:.2f} "
                f"({ZSCORE_ENTRY} eşiğini yukarı kırdı), fiyat uzun vadeli trend "
                f"(SMA200={indicators['sma200']}) üzerinde. İzleyen stop: {stop_price:.2f}."
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
