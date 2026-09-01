"""
AI Hold  (backtest-only, manual universe, never exits)
--------------------------------------------------------------
ai_infra'nın referans alındığı bir varyant: aynı giriş kuralları, farklı olarak
hiçbir çıkış kuralı yok (hold_never.py'deki desen), boyutlandırma equity
tabanlı ve sektör başına isim limiti daha geniş.

Farklar (ai_infra'ya göre):
  - UNIVERSE_KEY = "ai"          canlı/tam AI evreni (ai_infra'nın kullandığı
                                  survivorship-bias-adjusted "ai_backtest" alt
                                  kümesi değil -- bu strateji zaten canlıya
                                  çıkmayacağı için survivorship bias endişesi yok)
  - Çıkış kuralı yok              pozisyon açıldıktan sonra backtest sonuna
                                  kadar hiçbir koşulda kapatılmaz (hold_never)
  - SIZING_BASE = "equity"        risk_amount ve pozisyon tavanı kalan nakit
                                  yerine toplam equity üzerinden hesaplanır
  - MAX_NAMES_PER_SECTOR = 5      sektör başına açık pozisyon limiti 2 yerine 5

Entry (all must hold) -- ai_infra ile aynı:
  - price > SMA20 > SMA50            trend is up on both horizons
  - 20-day return beats SPY by >2%   relative strength
  - RSI(14) < 75                     don't buy a blow-off top

Exit: none. evaluate() never returns a SELL while a position is held.

ATR_MULT=2.0 is kept only so the BUY signal's stop_price (used by
ledger.plan_position for risk-based position sizing) matches ai_infra exactly.
DISABLE_TRAILING_STOP=True tells backtest.py's centralized position-management
loop to skip both the trailing-stop ratchet and the stop-price sell check for
this strategy, since that loop otherwise runs unconditionally for every module.

Sadece backtest.py üzerinden çalışır (bkz. BACKTEST_ONLY, strategies/__init__.py).
"""
from .indicators import sma, rsi, atr

NAME = "ai_hold"
UNIVERSE_KEY = "ai"
ATR_MULT = 2.0              # entry stop distance for position sizing only -- no trailing use
DISABLE_TRAILING_STOP = True
SIZING_BASE = "equity"
MAX_NAMES_PER_SECTOR = 5
RS_THRESHOLD = 0.02         # must beat SPY by this much over 20 sessions
RSI_MAX_ENTRY = 75          # overbought filter


def evaluate(symbol: str, df, spy_df, has_position: bool, position=None):
    if len(df) < 55:
        return None

    close = df["Close"]
    s20 = sma(close, 20)
    s50 = sma(close, 50)
    r14 = rsi(close, 14)
    a14 = atr(df, 14)
    price = float(close.iloc[-1])

    spy_return_20 = float(spy_df["Close"].iloc[-1] / spy_df["Close"].iloc[-21] - 1)
    stock_return_20 = float(price / close.iloc[-21] - 1)
    relative_strength = stock_return_20 - spy_return_20

    indicators = {
        "price": round(price, 2),
        "sma20": round(float(s20.iloc[-1]), 2),
        "sma50": round(float(s50.iloc[-1]), 2),
        "rsi14": round(float(r14.iloc[-1]), 2),
        "atr14": round(float(a14.iloc[-1]), 2),
        "relative_strength_20d": round(relative_strength, 4),
    }

    if not has_position:
        uptrend = price > s20.iloc[-1] > s50.iloc[-1]
        outperforming = relative_strength > RS_THRESHOLD
        not_overbought = r14.iloc[-1] < RSI_MAX_ENTRY
        if uptrend and outperforming and not_overbought:
            stop_price = price - ATR_MULT * float(a14.iloc[-1])
            reasoning = (
                f"Momentum girişi (hold, çıkış kuralı yok): fiyat SMA20 "
                f"({indicators['sma20']}) ve SMA50 ({indicators['sma50']}) üzerinde, "
                f"SPY'a göre 20 günde %{relative_strength*100:.1f} güçlü, "
                f"RSI={indicators['rsi14']} (<{RSI_MAX_ENTRY}, tepe değil)."
            )
            return {"action": "BUY", "price": price, "stop_price": stop_price,
                    "reasoning": reasoning, "indicators": indicators}
        return None

    # Çıkış kuralı yok: pozisyon açıldıktan sonra backtest sonuna kadar
    # hiçbir koşulda kapatılmaz.
    return None
