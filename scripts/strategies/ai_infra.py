"""
AI Infra Momentum Strategy  (backtest-only, manual universe)
--------------------------------------------------------------
Universe: manually curated AI_UNIVERSE_BACKTEST list (see universe.py),
filtered to names with >=2M average daily volume. Same rules as ai_momentum,
applied to a hand-picked "ai" universe instead of Nasdaq-100.

Uses the "ai_backtest" universe key (not "ai"): the live AI universe includes
five names that IPO'd/started separate trading after 2022 (ALAB, CRWV, NBIS,
GEV, CRDO), which would create survivorship bias in a multi-year backtest.
"ai_backtest" is the same list with those five excluded. This strategy is
backtest-only for now (see BACKTEST_ONLY in strategies/__init__.py); if it's
ever promoted to live trading, its UNIVERSE_KEY should switch to "ai" so it
trades the full, current universe.

Entry (all must hold):
  - price > SMA20 > SMA50            trend is up on both horizons
  - 20-day return beats SPY by >2%   relative strength
  - RSI(14) < 75                     don't buy a blow-off top

Exit:
  - price closes below SMA50         trend break
  - trailing stop hit                handled centrally in trade_bot (peak - 2*ATR)

The trailing stop is the important change here: a parabolic mover can run 30% and
give it all back before it ever touches SMA50, so the stop follows the peak up.
"""
from .indicators import sma, rsi, atr

NAME = "ai_infra"
UNIVERSE_KEY = "ai_backtest"
ATR_MULT = 2.0          # trailing stop distance
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
            stop_price = price - ATR_MULT * float(a14.iloc[-1])
            reasoning = (
                f"Momentum girişi: fiyat SMA20 ({indicators['sma20']}) ve SMA50 "
                f"({indicators['sma50']}) üzerinde, SPY'a göre 20 günde %"
                f"{relative_strength*100:.1f} güçlü, RSI={indicators['rsi14']} (<{RSI_MAX_ENTRY}, "
                f"tepe değil). İzleyen stop: {stop_price:.2f} (2xATR)."
            )
            return {"action": "BUY", "price": price, "stop_price": stop_price,
                    "reasoning": reasoning, "indicators": indicators}
        return None

    if price < s50.iloc[-1]:
        reasoning = f"Trend kırıldı: fiyat ({indicators['price']}) SMA50 ({indicators['sma50']}) altına indi."
        return {"action": "SELL", "price": price, "reasoning": reasoning, "indicators": indicators}
    return None
