"""
Trend (Donchian Breakout) Strategy  — BACKTEST-ONLY, canlıda çalışmaz
------------------------------------------------------------------------
ai_momentum.py'nin varyasyonu: yalnızca giriş sinyali farklı. Çıkış kuralı,
stop mantığı ve pozisyon boyutlandırma ai_momentum.py ile birebir aynı.

Universe: Nasdaq-100, filtered to names with >=2M average daily volume.

Entry:
  - price bugün 20 günlük Donchian üst kanalını (bugünü hariç tutan önceki
    20 günün en yükseği) yukarı kırdı

ai_momentum.py'deki SMA20>SMA50 + relative-strength + RSI filtresi tamamen
kaldırılıp yerine tek koşul olarak Donchian kanal kırılımı konulmuştur.

Exit:
  - price closes below SMA50         trend break
  - trailing stop hit                handled centrally in trade_bot (peak - 2*ATR)
"""
import pandas as pd

from .indicators import sma, atr, donchian

NAME = "trend_donchian"
UNIVERSE_KEY = "ndx"
ATR_MULT = 2.0          # trailing stop distance
DONCHIAN_WINDOW = 20


def evaluate(symbol: str, df, spy_df, has_position: bool, position=None):
    if len(df) < 55:
        return None

    close = df["Close"]
    s50 = sma(close, 50)
    a14 = atr(df, 14)
    upper, lower = donchian(df, DONCHIAN_WINDOW)
    price = float(close.iloc[-1])
    upper_now = upper.iloc[-1]

    indicators = {
        "price": round(price, 2),
        "sma50": round(float(s50.iloc[-1]), 2),
        "atr14": round(float(a14.iloc[-1]), 2),
        "donchian_upper": round(float(upper_now), 2) if not pd.isna(upper_now) else None,
    }

    if not has_position:
        breakout = (not pd.isna(upper_now)) and price > float(upper_now)
        if breakout:
            stop_price = price - ATR_MULT * float(a14.iloc[-1])
            reasoning = (
                f"Donchian kırılımı: fiyat ({indicators['price']}) önceki "
                f"{DONCHIAN_WINDOW} günün en yükseğini ({indicators['donchian_upper']}) "
                f"yukarı kırdı. İzleyen stop: {stop_price:.2f} (2xATR)."
            )
            return {"action": "BUY", "price": price, "stop_price": stop_price,
                    "reasoning": reasoning, "indicators": indicators}
        return None

    if price < s50.iloc[-1]:
        reasoning = f"Trend kırıldı: fiyat ({indicators['price']}) SMA50 ({indicators['sma50']}) altına indi."
        return {"action": "SELL", "price": price, "reasoning": reasoning, "indicators": indicators}
    return None
