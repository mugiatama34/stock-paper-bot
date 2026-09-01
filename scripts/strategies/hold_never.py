"""
AI/Tech Momentum -- Never Exit Variant  (backtest-only REFERENCE BENCHMARK, not a real strategy)
--------------------------------------------------------------------------------------------------
This is not a strategy meant to be traded -- it exists purely to answer "what would
ai_momentum's exact entries have returned with zero exit rules at all?" so the value
of ai_momentum's SMA50 exit and trailing stop can be isolated and measured against it.

Universe, entry, sizing, sector limits and regime filter are identical to
ai_momentum.py. Once a position is opened it is held through the end of the
backtest, no matter what price does -- no SMA break, no trailing stop, no
regime change ever closes it.

Entry (all must hold) -- same as ai_momentum:
  - price > SMA20 > SMA50            trend is up on both horizons
  - 20-day return beats SPY by >2%   relative strength
  - RSI(14) < 75                     don't buy a blow-off top

Exit: none. evaluate() never returns a SELL while a position is held.

Regime filter interaction: the regime filter (SPY below its own SMA200 -> risk-off)
only gates NEW entries in both backtest.py and trade_bot.py -- it never force-closes
an existing position. So for this strategy, once a position opens on a risk-on day,
it rides through any later risk-off period (even a full bear market) fully exposed,
since nothing else here can close it either. That makes its drawdowns not directly
comparable to ai_momentum's regime-protected numbers -- this benchmark isolates
entry/selection quality only, not the contribution of risk management.

ATR_MULT=2.0 is kept only so the BUY signal's stop_price (used by
ledger.plan_position for risk-based position sizing) matches ai_momentum exactly.
DISABLE_TRAILING_STOP=True tells backtest.py's centralized position-management
loop to skip both the trailing-stop ratchet and the stop-price sell check for
this strategy, since that loop otherwise runs unconditionally for every module.
"""
from .indicators import sma, rsi, atr

NAME = "hold_never"
UNIVERSE_KEY = "ndx"
ATR_MULT = 2.0              # entry stop distance for position sizing only -- no trailing use
DISABLE_TRAILING_STOP = True
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
                f"Momentum girişi (referans ölçüt, çıkış kuralı yok): fiyat SMA20 "
                f"({indicators['sma20']}) ve SMA50 ({indicators['sma50']}) üzerinde, "
                f"SPY'a göre 20 günde %{relative_strength*100:.1f} güçlü, "
                f"RSI={indicators['rsi14']} (<{RSI_MAX_ENTRY}, tepe değil)."
            )
            return {"action": "BUY", "price": price, "stop_price": stop_price,
                    "reasoning": reasoning, "indicators": indicators}
        return None

    # Gerçek bir strateji değil, referans ölçüttür: pozisyon açıldıktan sonra
    # backtest sonuna kadar hiçbir koşulda kapatılmaz.
    return None
