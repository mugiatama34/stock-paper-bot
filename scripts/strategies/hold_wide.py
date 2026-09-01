"""
AI/Tech Momentum -- Wide Trailing Stop Variant  (backtest-only exit-rule benchmark)
------------------------------------------------------------------------------------
Universe, entry, sizing, sector limits and regime filter are identical to
ai_momentum.py. The only change is how far the trailing stop trails.

Entry (all must hold) -- same as ai_momentum:
  - price > SMA20 > SMA50            trend is up on both horizons
  - 20-day return beats SPY by >2%   relative strength
  - RSI(14) < 75                     don't buy a blow-off top

Exit:
  - price closes below SMA50         trend break (unchanged from ai_momentum)
  - trailing stop hit                peak - 8*ATR instead of peak - 2*ATR

ATR_MULT vs ENTRY_ATR_MULT: the entry-time stop_price (used only for risk-based
position sizing in ledger.plan_position) is computed with ENTRY_ATR_MULT=2.0, the
same value ai_momentum uses -- so this strategy opens the exact same size position
ai_momentum would. ATR_MULT=8.0 is a separate constant read by the centralized
trailing-stop ratchet in backtest.py (getattr(module, "ATR_MULT", 2.0)); it only
widens how far the stop is allowed to trail behind the peak, it never touches sizing.
"""
from .indicators import sma, rsi, atr

NAME = "hold_wide"
UNIVERSE_KEY = "ndx"
ENTRY_ATR_MULT = 2.0    # entry stop distance for position sizing -- matches ai_momentum
ATR_MULT = 8.0          # trailing stop distance used by the centralized ratchet
RS_THRESHOLD = 0.02     # must beat SPY by this much over 20 sessions
RSI_MAX_ENTRY = 75      # overbought filter


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
            stop_price = price - ENTRY_ATR_MULT * float(a14.iloc[-1])
            reasoning = (
                f"Momentum girişi (geniş iz-süren stop varyantı): fiyat SMA20 "
                f"({indicators['sma20']}) ve SMA50 ({indicators['sma50']}) üzerinde, "
                f"SPY'a göre 20 günde %{relative_strength*100:.1f} güçlü, "
                f"RSI={indicators['rsi14']} (<{RSI_MAX_ENTRY}, tepe değil). "
                f"İzleyen stop: {stop_price:.2f} (2xATR giriş, {ATR_MULT:.0f}xATR iz sürme)."
            )
            return {"action": "BUY", "price": price, "stop_price": stop_price,
                    "reasoning": reasoning, "indicators": indicators}
        return None

    if price < s50.iloc[-1]:
        reasoning = f"Trend kırıldı: fiyat ({indicators['price']}) SMA50 ({indicators['sma50']}) altına indi."
        return {"action": "SELL", "price": price, "reasoning": reasoning, "indicators": indicators}
    return None
