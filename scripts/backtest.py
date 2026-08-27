"""
Historical backtest of the three live strategies.

Fidelity rules this file is built around:

  * It calls the SAME strategy.evaluate() functions the live bot calls, and the
    same ledger.buy/sell/trailing-stop/sector-limit code. Re-implementing the
    rules here would mean measuring something other than what actually trades.
  * No look-ahead: on simulated day i a strategy only ever sees bars[:i+1].
    Indicators are precomputed on the full series for speed, but rolling windows
    only look backwards, and the cached series is re-indexed to the visible slice
    before the strategy reads it -- so day i still sees only day i.
  * Entries and exits fill at the same day's close, which is what the live bot
    effectively does when it runs at 20:45 UTC.

Known biases, stated plainly because they inflate results:
  * SURVIVORSHIP: the universe is today's index membership. Companies that were
    dropped or went bankrupt are absent, so the backtest never buys them.
  * No commissions, no slippage, no bid/ask spread.
  * Ranking the universe by dollar volume uses present-day liquidity.
Treat the output as a sanity check on the rules, not as a return forecast.
"""
import argparse
import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter

import ledger as ledger_mod
import universe as universe_mod
from strategies import STRATEGY_MODULES
import strategies.indicators as IND

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")
SPY = "SPY"
WARMUP = 250          # bars needed before SMA200 + RSI are meaningful
STARTING_CASH = 10000.0

# ---------------------------------------------------------------- indicator cache
_FULL = {}                    # ticker -> full-history DataFrame
_CACHE = {}                   # (ticker, kind, window) -> full-history Series
_CTX = {"ticker": None}

_orig = {"sma": IND.sma, "rsi": IND.rsi, "atr": IND.atr}


def _cached_series(kind, window):
    key = (_CTX["ticker"], kind, window)
    if key not in _CACHE:
        full = _FULL[_CTX["ticker"]]
        if kind == "atr":
            _CACHE[key] = _orig["atr"](full, window)
        else:
            _CACHE[key] = _orig[kind](full["Close"], window)
    return _CACHE[key]


def _sma(series, window):
    return _cached_series("sma", window).loc[series.index]


def _rsi(series, window=14):
    return _cached_series("rsi", window).loc[series.index]


def _atr(df, window=14):
    return _cached_series("atr", window).loc[df.index]


def install_fast_indicators():
    """Strategies did `from .indicators import sma`, so patch their namespaces."""
    for mod in STRATEGY_MODULES.values():
        mod.sma, mod.rsi, mod.atr = _sma, _rsi, _atr


# ---------------------------------------------------------------- data
def download(symbols, years):
    symbols = sorted(set(symbols))
    period = f"{years + 2}y"          # extra years cover the warm-up window
    out = {}
    for i in range(0, len(symbols), 100):
        chunk = symbols[i:i + 100]
        try:
            raw = yf.download(chunk, period=period, group_by="ticker",
                              auto_adjust=True, threads=True, progress=False)
        except Exception as e:
            print(f"[warn] chunk failed: {e}")
            continue
        for s in chunk:
            try:
                df = (raw[s] if len(chunk) > 1 else raw).dropna(how="all")
                if len(df) > WARMUP + 20:
                    out[s] = df
            except Exception:
                pass
    print(f"[data] {len(out)}/{len(symbols)} symbols usable")
    return out


def rank_by_dollar_volume(data, sector_map, top_n):
    """Liquidity proxy: median price x volume over the last year."""
    scored = []
    for sym, df in data.items():
        if sym not in sector_map:
            continue
        tail = df.tail(252)
        if tail.empty:
            continue
        dv = float((tail["Close"] * tail["Volume"]).median())
        if np.isfinite(dv):
            scored.append((dv, sym))
    scored.sort(reverse=True)
    return [s for _, s in scored[:top_n]]


# ---------------------------------------------------------------- engine
def new_ledger(name):
    return {"strategy": name, "starting_cash": STARTING_CASH, "cash": STARTING_CASH,
            "positions": {}, "trades": [], "equity_history": []}


def run(strategy_name, symbols, data, spy_df, sector_map, dates):
    module = STRATEGY_MODULES[strategy_name]
    lg = new_ledger(strategy_name)
    curve = []

    spy_close = spy_df["Close"]
    spy_sma200 = spy_close.rolling(200).mean()

    for day in dates:
        # Freeze the simulated clock so trade timestamps are historical, not "now".
        ledger_mod.now_iso = lambda d=day: pd.Timestamp(d).isoformat()

        if day not in spy_close.index:
            continue
        risk_on = bool(spy_close.loc[day] >= spy_sma200.loc[day]) if not pd.isna(spy_sma200.loc[day]) else True

        prices = {}

        # --- manage open positions ---
        for sym in list(lg["positions"].keys()):
            df = data.get(sym)
            if df is None or day not in df.index:
                continue
            window = df.loc[:day]
            if len(window) < WARMUP:
                continue
            price = float(window["Close"].iloc[-1])
            prices[sym] = price
            _CTX["ticker"] = sym

            try:
                atr_val = float(_atr(window, 14).iloc[-1])
            except Exception:
                atr_val = 0.0
            ledger_mod.update_trailing_stop(lg, sym, price, atr_val,
                                            atr_mult=getattr(module, "ATR_MULT", 2.0))

            stop = lg["positions"][sym].get("stop_price")
            if stop and price <= stop:
                ledger_mod.sell(lg, sym, price, "İzleyen stop tetiklendi.", {})
                continue

            spy_win = spy_df.loc[:day]
            try:
                sig = module.evaluate(sym, window, spy_win, True, lg["positions"][sym])
            except Exception:
                sig = None
            if not sig:
                continue
            if sig["action"] == "SELL":
                ledger_mod.sell(lg, sym, price, sig["reasoning"], sig["indicators"])
            elif sig["action"] == "SELL_PARTIAL":
                if ledger_mod.sell(lg, sym, price, sig["reasoning"], sig["indicators"], qty=sig["qty"]):
                    if sym in lg["positions"]:
                        lg["positions"][sym]["partial_taken"] = True

        # --- entries ---
        if risk_on:
            for sym in symbols:
                if sym in lg["positions"]:
                    continue
                df = data.get(sym)
                if df is None or day not in df.index:
                    continue
                window = df.loc[:day]
                if len(window) < WARMUP:
                    continue
                if not universe_mod.passes_liquidity(window):
                    continue

                _CTX["ticker"] = sym
                spy_win = spy_df.loc[:day]
                try:
                    sig = module.evaluate(sym, window, spy_win, False, None)
                except Exception:
                    continue
                if not sig or sig["action"] != "BUY":
                    continue

                price = sig["price"]
                prices[sym] = price
                sector = sector_map.get(sym, "Unknown")
                qty, cost = ledger_mod.plan_position(lg, price, sig["stop_price"])
                if qty <= 0:
                    continue
                allowed, _ = ledger_mod.sector_allows_entry(lg, prices, sector, cost)
                if not allowed:
                    continue
                ledger_mod.buy(lg, sym, price, sig["stop_price"], sig["reasoning"],
                               sig["indicators"], sector=sector)

        for sym in lg["positions"]:
            if sym not in prices:
                df = data.get(sym)
                if df is not None and day in df.index:
                    prices[sym] = float(df["Close"].loc[day])
        curve.append((day, ledger_mod.total_equity(lg, prices)))

    return lg, curve


# ---------------------------------------------------------------- metrics
def metrics(curve, lg, years):
    equities = np.array([e for _, e in curve], dtype=float)
    if len(equities) < 2:
        return {}
    final = equities[-1]
    total_ret = (final / STARTING_CASH - 1) * 100
    cagr = ((final / STARTING_CASH) ** (1 / years) - 1) * 100 if years > 0 else 0.0

    peak = np.maximum.accumulate(equities)
    max_dd = float(((equities - peak) / peak).min() * 100)

    daily = np.diff(equities) / equities[:-1]
    sharpe = float(np.mean(daily) / np.std(daily) * np.sqrt(252)) if np.std(daily) > 0 else 0.0

    sells = [t for t in lg["trades"] if t["action"] == "SELL"]
    wins = [t for t in sells if t.get("pnl", 0) > 0]
    losses = [t for t in sells if t.get("pnl", 0) <= 0]
    win_rate = len(wins) / len(sells) * 100 if sells else None
    avg_win = np.mean([t["pnl"] for t in wins]) if wins else 0.0
    avg_loss = np.mean([t["pnl"] for t in losses]) if losses else 0.0

    return {
        "final_equity": round(final, 2),
        "total_return_pct": round(total_ret, 2),
        "cagr_pct": round(cagr, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe": round(sharpe, 2),
        "closed_trades": len(sells),
        "win_rate_pct": round(win_rate, 1) if win_rate is not None else None,
        "avg_win": round(float(avg_win), 2),
        "avg_loss": round(float(avg_loss), 2),
    }


def benchmark(spy_df, dates, years):
    s = spy_df["Close"].reindex(dates).dropna()
    if s.empty:
        return {}
    eq = STARTING_CASH * (s / s.iloc[0])
    arr = eq.values
    peak = np.maximum.accumulate(arr)
    return {
        "final_equity": round(float(arr[-1]), 2),
        "total_return_pct": round(float(arr[-1] / STARTING_CASH - 1) * 100, 2),
        "cagr_pct": round((float(arr[-1] / STARTING_CASH) ** (1 / years) - 1) * 100, 2),
        "max_drawdown_pct": round(float(((arr - peak) / peak).min() * 100), 2),
        "curve": list(zip(eq.index, arr)),
    }


def plot(curves, bench, years, path):
    fig, ax = plt.subplots(figsize=(11, 6))
    for name, curve in curves.items():
        ax.plot([d for d, _ in curve], [e for _, e in curve], label=name, linewidth=1.4)
    if bench.get("curve"):
        ax.plot([d for d, _ in bench["curve"]], [e for _, e in bench["curve"]],
                label="SPY al-tut", color="gray", linestyle="--", linewidth=1.2)
    ax.axhline(STARTING_CASH, color="black", linewidth=.8, alpha=.4)
    ax.set_title(f"Backtest — son {years} yıl")
    ax.set_ylabel("Portföy Değeri ($)")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, p: f"${v:,.0f}"))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m.%y"))
    ax.grid(alpha=.25)
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    os.makedirs(REPORTS_DIR, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--universe-size", type=int, default=80,
                    help="Names per strategy universe, ranked by dollar volume. "
                         "Larger is more faithful but much slower.")
    args = ap.parse_args()

    install_fast_indicators()
    universes = universe_mod.build_universe()

    wanted = {SPY}
    for m in STRATEGY_MODULES.values():
        wanted |= set(universes[m.UNIVERSE_KEY].keys())

    data = download(wanted, args.years)
    global _FULL
    _FULL = data
    spy_df = data.get(SPY)
    if spy_df is None:
        print("[error] SPY verisi yok")
        return

    dates = spy_df.index[-(args.years * 252):]
    print(f"[run] {dates[0].date()} → {dates[-1].date()} ({len(dates)} işlem günü)")

    curves, results = {}, {}
    for name, module in STRATEGY_MODULES.items():
        sector_map = universes[module.UNIVERSE_KEY]
        syms = rank_by_dollar_volume(data, sector_map, args.universe_size)
        print(f"[run] {name}: {len(syms)} hisse")
        _CACHE.clear()
        lg, curve = run(name, syms, data, spy_df, sector_map, dates)
        curves[name] = curve
        results[name] = metrics(curve, lg, args.years)
        results[name]["universe_size"] = len(syms)

    bench = benchmark(spy_df, dates, args.years)
    chart = os.path.join(REPORTS_DIR, "backtest.png")
    plot(curves, bench, args.years, chart)

    rows = [("Strateji", "Getiri", "CAGR", "Max DD", "Sharpe", "İşlem", "Kazanma")]
    for n, r in results.items():
        rows.append((n, f"{r['total_return_pct']:+.1f}%", f"{r['cagr_pct']:+.1f}%",
                     f"{r['max_drawdown_pct']:.1f}%", f"{r['sharpe']:.2f}",
                     str(r["closed_trades"]),
                     f"{r['win_rate_pct']}%" if r["win_rate_pct"] is not None else "n/a"))
    rows.append(("SPY al-tut", f"{bench['total_return_pct']:+.1f}%", f"{bench['cagr_pct']:+.1f}%",
                 f"{bench['max_drawdown_pct']:.1f}%", "-", "-", "-"))

    w = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    table = "\n".join("  ".join(c.ljust(w[i]) for i, c in enumerate(r)) for r in rows)
    print("\n" + table + "\n")

    out = {"generated": datetime.now(timezone.utc).isoformat(), "years": args.years,
           "universe_size": args.universe_size, "strategies": results, "spy_benchmark":
           {k: v for k, v in bench.items() if k != "curve"}}
    with open(os.path.join(REPORTS_DIR, "backtest.json"), "w") as f:
        json.dump(out, f, indent=2)

    try:
        import telegram_notify
        telegram_notify.send_photo(chart, caption=f"🔬 BACKTEST ({args.years} yıl)\n\n```\n{table}\n```")
    except Exception as e:
        print(f"[warn] telegram: {e}")


if __name__ == "__main__":
    main()
