"""
AI/Tech Momentum -- No Trailing Stop, SMA200 Exit Variant  (backtest-only exit-rule benchmark)
--------------------------------------------------------------------------------------------
Universe, entry, sizing, sector limits and regime filter are identical to
ai_momentum.py. The trailing stop is removed entirely; the only exit condition
is a break of the long-term (SMA200) trend.

Entry (all must hold) -- same as ai_momentum:
  - price > SMA20 > SMA50            trend is up on both horizons
  - 20-day return beats SPY by >2%   relative strength
  - RSI(14) < 75                     don't buy a blow-off top

Exit:
  - price closes below SMA200        long-term trend break -- the ONLY exit rule.
                                      SMA50 break and the trailing stop are NOT
                                      checked at all for this strategy.

ATR_MULT=2.0 is kept only so the BUY signal's stop_price (used by
ledger.plan_position for risk-based position sizing) matches ai_momentum exactly.
DISABLE_TRAILING_STOP=True tells backtest.py's centralized position-management
loop to skip both the trailing-stop ratchet and the stop-price sell check for
this strategy, since that loop otherwise runs unconditionally for every module.
"""
from .indicators import sma, rsi, atr

NAME = "hold_sma200"
UNIVERSE_KEY = "ndx"
ATR_MULT = 2.0              # entry stop distance for position sizing only -- no trailing use
DISABLE_TRAILING_STOP = True
RS_THRESHOLD = 0.02         # must beat SPY by this much over 20 sessions
RSI_MAX_ENTRY = 75          # overbought filter


def evaluate(symbol: str, df, spy_df, has_position: bool, position=None):
    if len(df) < 205:      # SMA200 needs 200 bars; margin matches ai_momentum's 55-for-SMA50 style
        return None

    close = df["Close"]
    s20 = sma(close, 20)
    s50 = sma(close, 50)
    s200 = sma(close, 200)
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
        "sma200": round(float(s200.iloc[-1]), 2),
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
                f"Momentum girişi (SMA200 çıkışlı, iz-süren stop yok): fiyat SMA20 "
                f"({indicators['sma20']}) ve SMA50 ({indicators['sma50']}) üzerinde, "
                f"SPY'a göre 20 günde %{relative_strength*100:.1f} güçlü, "
                f"RSI={indicators['rsi14']} (<{RSI_MAX_ENTRY}, tepe değil)."
            )
            return {"action": "BUY", "price": price, "stop_price": stop_price,
                    "reasoning": reasoning, "indicators": indicators}
        return None

    if price < s200.iloc[-1]:
        reasoning = f"Uzun vadeli trend kırıldı: fiyat ({indicators['price']}) SMA200 ({indicators['sma200']}) altına indi."
        return {"action": "SELL", "price": price, "reasoning": reasoning, "indicators": indicators}
    return None
