"""
AI/Tech Momentum -- Equity-Base Sizing Variant  (backtest-only TEST STRATEGY, not a real strategy)
--------------------------------------------------------------------------------------------------
This exists purely to compare ledger.plan_position()'s two sizing bases against
hold_never.py, which it is otherwise an exact copy of: universe, entry rule,
sizing constants (ATR_MULT, RS_THRESHOLD, RSI_MAX_ENTRY), and "never exit" behavior
are all identical. The only difference is SIZING_BASE = "equity" instead of the
default "cash", so risk_amount and the position cap are computed against total
equity (cash + open positions' market value) rather than remaining cash --
this is meant to isolate whether cash-base sizing's shrinking position sizes
across consecutive entries (each one sized off what's left after the last) is
actually the effect the equity base is meant to correct.

Entry (all must hold) -- same as ai_momentum/hold_never:
  - price > SMA20 > SMA50            trend is up on both horizons
  - 20-day return beats SPY by >2%   relative strength
  - RSI(14) < 75                     don't buy a blow-off top

Exit: none. evaluate() never returns a SELL while a position is held.

Regime filter interaction: same caveat as hold_never -- the regime filter only
gates new entries, never force-closes an existing one, so a position opened
risk-on rides through any later risk-off period fully exposed.

ATR_MULT=2.0 is kept only so the BUY signal's stop_price (used by
ledger.plan_position for risk-based position sizing) matches hold_never/ai_momentum
exactly. DISABLE_TRAILING_STOP=True tells backtest.py's centralized position-
management loop to skip both the trailing-stop ratchet and the stop-price sell
check for this strategy, since that loop otherwise runs unconditionally for
every module.
"""
from .indicators import sma, rsi, atr

NAME = "ndx_equity_sizing"
UNIVERSE_KEY = "ndx"
ATR_MULT = 2.0              # entry stop distance for position sizing only -- no trailing use
DISABLE_TRAILING_STOP = True
SIZING_BASE = "equity"      # only difference from hold_never: size off total equity, not cash
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
                f"Momentum girişi (equity-base sizing testi): fiyat SMA20 "
                f"({indicators['sma20']}) ve SMA50 ({indicators['sma50']}) üzerinde, "
                f"SPY'a göre 20 günde %{relative_strength*100:.1f} güçlü, "
                f"RSI={indicators['rsi14']} (<{RSI_MAX_ENTRY}, tepe değil)."
            )
            return {"action": "BUY", "price": price, "stop_price": stop_price,
                    "reasoning": reasoning, "indicators": indicators}
        return None

    # Gerçek bir strateji değil, sizing testidir: pozisyon açıldıktan sonra
    # backtest sonuna kadar hiçbir koşulda kapatılmaz.
    return None
