import pandas as pd
import numpy as np


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    """
    Wilder-style RSI.

    The zero-loss case matters: a stock with no down-days in the window has an
    average loss of 0, and a naive gain/loss ratio yields NaN there. NaN then
    makes every comparison silently False, so an "RSI < 75" filter would let a
    genuinely overbought runner straight through. Those bars are pinned to 100
    (all gain) or 50 (perfectly flat) instead.
    """
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window).mean()
    avg_loss = loss.rolling(window).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))

    no_loss = (avg_loss == 0) & avg_gain.notna()
    out = out.mask(no_loss & (avg_gain > 0), 100.0)
    out = out.mask(no_loss & (avg_gain == 0), 50.0)
    return out


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window).mean()
