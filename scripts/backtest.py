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
COST_BPS = {"value": 0.0}   # per-side cost in basis points; set from --cost-bps

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
def download(symbols, start, end):
    symbols = sorted(set(symbols))
    out = {}
    for i in range(0, len(symbols), 100):
        chunk = symbols[i:i + 100]
        try:
            raw = yf.download(chunk, start=start, end=end, group_by="ticker",
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
def _fill(price, side):
    """
    Shade the fill against us to stand in for commission + spread + slippage.
    A strategy that trades hundreds of times looks very different once this is
    non-zero, which is exactly why it is worth being able to switch on.
    """
    bps = COST_BPS["value"]
    if not bps:
        return price
    return price * (1 + bps / 10000.0) if side == "buy" else price * (1 - bps / 10000.0)


def new_ledger(name):
    return {"strategy": name, "starting_cash": STARTING_CASH, "cash": STARTING_CASH,
            "positions": {}, "trades": [], "equity_history": []}


def run(strategy_name, symbols, data, spy_df, sector_map, dates):
    module = STRATEGY_MODULES[strategy_name]
    lg = new_ledger(strategy_name)
    curve = []
    invested = [0]

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
                ledger_mod.sell(lg, sym, _fill(price, "sell"), "İzleyen stop tetiklendi.", {})
                continue

            spy_win = spy_df.loc[:day]
            try:
                sig = module.evaluate(sym, window, spy_win, True, lg["positions"][sym])
            except Exception as e:
                print(f"[warn] {strategy_name} {sym}: evaluate() backtest'te hata verdi, atlanıyor: {e}")
                sig = None
            if not sig:
                continue
            if sig["action"] == "SELL":
                ledger_mod.sell(lg, sym, _fill(price, "sell"), sig["reasoning"], sig["indicators"])
            elif sig["action"] == "SELL_PARTIAL":
                if ledger_mod.sell(lg, sym, _fill(price, "sell"), sig["reasoning"],
                                   sig["indicators"], qty=sig["qty"]):
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
                stop_price = sig["stop_price"]
                if np.isnan(price) or np.isnan(stop_price):
                    print(f"[skip] {strategy_name} {sym} {day.date()}: price/stop_price NaN, pozisyon açılmıyor")
                    continue

                fill_price = _fill(price, "buy")
                prices[sym] = price
                sector = sector_map.get(sym, "Unknown")
                qty, cost = ledger_mod.plan_position(lg, fill_price, stop_price)
                if qty <= 0:
                    continue
                allowed, _ = ledger_mod.sector_allows_entry(lg, prices, sector, cost)
                if not allowed:
                    continue
                ledger_mod.buy(lg, sym, fill_price, sig["stop_price"],
                               sig["reasoning"], sig["indicators"], sector=sector)

        for sym in lg["positions"]:
            if sym not in prices:
                df = data.get(sym)
                if df is not None and day in df.index:
                    prices[sym] = float(df["Close"].loc[day])
        curve.append((day, ledger_mod.total_equity(lg, prices)))
        if lg["positions"]:
            invested[0] += 1

    invested_pct = invested[0] / len(curve) * 100 if curve else 0.0
    return lg, curve, invested_pct


# ---------------------------------------------------------------- metrics
def metrics(curve, lg, years, invested_pct=0.0):
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

    pnls = [t.get("pnl", 0) for t in sells]
    profit_factor = (sum(p for p in pnls if p > 0) / abs(sum(p for p in pnls if p < 0))
                     if any(p < 0 for p in pnls) else None)

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
        "best_trade": round(float(max(pnls)), 2) if pnls else 0.0,
        "worst_trade": round(float(min(pnls)), 2) if pnls else 0.0,
        "profit_factor": round(float(profit_factor), 2) if profit_factor else None,
        "invested_days_pct": round(invested_pct, 1),
    }


def benchmark(spy_df, dates, years):
    s = spy_df["Close"].reindex(dates).dropna()
    if s.empty:
        return {}
    eq = STARTING_CASH * (s / s.iloc[0])
    arr = eq.values
    peak = np.maximum.accumulate(arr)
    bdaily = np.diff(arr) / arr[:-1]
    bsharpe = float(np.mean(bdaily) / np.std(bdaily) * np.sqrt(252)) if np.std(bdaily) > 0 else 0.0
    return {
        "sharpe": round(bsharpe, 2),
        "final_equity": round(float(arr[-1]), 2),
        "total_return_pct": round(float(arr[-1] / STARTING_CASH - 1) * 100, 2),
        "cagr_pct": round((float(arr[-1] / STARTING_CASH) ** (1 / years) - 1) * 100, 2),
        "max_drawdown_pct": round(float(((arr - peak) / peak).min() * 100), 2),
        "curve": list(zip(eq.index, arr)),
    }


def plot(curves, bench, period_title, path):
    fig, ax = plt.subplots(figsize=(11, 6))
    for name, curve in curves.items():
        ax.plot([d for d, _ in curve], [e for _, e in curve], label=name, linewidth=1.4)
    if bench.get("curve"):
        ax.plot([d for d, _ in bench["curve"]], [e for _, e in bench["curve"]],
                label="SPY al-tut", color="gray", linestyle="--", linewidth=1.2)
    ax.axhline(STARTING_CASH, color="black", linewidth=.8, alpha=.4)
    ax.set_title(f"Backtest — {period_title}")
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



DOCS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")

METRIC_HELP = [
    ("Getiri", "3 yılın sonunda toplam yüzde değişim. Tek başına yanıltıcıdır — "
               "ne kadar risk alınarak elde edildiğini söylemez."),
    ("CAGR", "Yıllık bileşik getiri. Farklı uzunluktaki dönemleri karşılaştırmak için."),
    ("Max DD", "Maksimum düşüş: zirveden dibe en büyük kayıp. Gerçek hayatta "
               "stratejiyi terk edip etmeyeceğini belirleyen sayı budur."),
    ("Sharpe", "Birim risk başına getiri. Kabaca: 1'in altı zayıf, 1-2 iyi, 2 üstü çok iyi. "
               "İki strateji aynı getiriyi verdiyse Sharpe'ı yüksek olan daha az sarsıntıyla vermiştir."),
    ("Profit Factor", "Toplam kâr / toplam zarar. 1'in altı zarar eden sistem demek. "
                      "1.5+ sağlam sayılır."),
    ("Kazanma %", "Kârla kapanan işlemlerin oranı. Tek başına anlamsızdır — küçük kârlar "
                  "ve büyük zararlarla %70 kazanma oranı da mümkündür. Ortalama kâr/zarar ile birlikte oku."),
    ("Piyasada kalma", "Portföyün en az bir pozisyon taşıdığı günlerin oranı. Düşükse strateji "
                       "çok nakitte bekliyor demektir; boğa piyasasında endeksin gerisinde kalmasının sebebi budur."),
]


def build_html(results, bench, period_title, universe_size, cost_bps, path):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def cell(v, good_high=True, suffix="", fmt="{:+.1f}"):
        if v is None:
            return "<td>—</td>"
        cls = ""
        if isinstance(v, (int, float)):
            cls = "pos" if (v > 0) == good_high else "neg"
        return f"<td class='{cls}'>{fmt.format(v)}{suffix}</td>"

    rows = ""
    for name, r in results.items():
        rows += (
            f"<tr><td><b>{name}</b></td>"
            f"{cell(r['total_return_pct'], True, '%')}"
            f"{cell(r['cagr_pct'], True, '%')}"
            f"{cell(r['max_drawdown_pct'], False, '%')}"
            f"<td>{r['sharpe']:.2f}</td>"
            f"<td>{r['profit_factor'] if r['profit_factor'] else '—'}</td>"
            f"<td>{r['closed_trades']}</td>"
            f"<td>{r['win_rate_pct']}%</td>"
            f"<td>{r['invested_days_pct']:.0f}%</td></tr>"
        )
    rows += (
        f"<tr class='bench'><td><b>SPY al-tut</b></td>"
        f"<td class='pos'>{bench['total_return_pct']:+.1f}%</td>"
        f"<td class='pos'>{bench['cagr_pct']:+.1f}%</td>"
        f"<td class='neg'>{bench['max_drawdown_pct']:.1f}%</td>"
        f"<td>{bench.get('sharpe', 0):.2f}</td><td>—</td><td>—</td><td>—</td><td>100%</td></tr>"
    )

    detail = ""
    for name, r in results.items():
        detail += f"""
        <div class="card">
          <h3>{name}</h3>
          <ul>
            <li>Son değer: <b>${r['final_equity']:,.2f}</b></li>
            <li>Ortalama kâr: ${r['avg_win']:,.2f} &nbsp;·&nbsp; Ortalama zarar: ${r['avg_loss']:,.2f}</li>
            <li>En iyi işlem: ${r['best_trade']:,.2f} &nbsp;·&nbsp; En kötü: ${r['worst_trade']:,.2f}</li>
            <li>Evren: {r['universe_size']} hisse</li>
          </ul>
        </div>"""

    helps = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in METRIC_HELP)
    cost_note = (f"işlem başına %{cost_bps/100:.3f} maliyet uygulandı" if cost_bps
                 else "<b>işlem maliyeti uygulanmadı (0)</b>")

    html = f"""<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Backtest Raporu</title>
<style>
body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
       max-width:1000px;margin:2rem auto;padding:0 1rem;color:#222;line-height:1.5; }}
h1 {{ font-size:1.5rem; }} h2 {{ font-size:1.15rem;border-bottom:2px solid #ddd;
       padding-bottom:.3rem;margin-top:2.2rem; }}
h3 {{ font-size:1rem;margin:.2rem 0 .5rem; }}
table {{ width:100%;border-collapse:collapse;margin:1rem 0;font-size:.9rem; }}
th,td {{ padding:.5rem .5rem;border-bottom:1px solid #eee;text-align:right; }}
th:first-child,td:first-child {{ text-align:left; }}
th {{ background:#fafafa;font-weight:600; }}
tr.bench td {{ background:#f6f8ff;border-top:2px solid #ccd; }}
.pos {{ color:#0a7d33;font-weight:600; }} .neg {{ color:#c62828;font-weight:600; }}
img {{ max-width:100%;margin:1rem 0; }}
.cards {{ display:flex;flex-wrap:wrap;gap:1rem; }}
.card {{ flex:1 1 260px;border:1px solid #e5e5e5;border-radius:8px;padding:.8rem 1rem;font-size:.85rem; }}
.card ul {{ margin:0;padding-left:1.1rem; }}
dl dt {{ font-weight:600;margin-top:.7rem; }} dl dd {{ margin:.15rem 0 0 0;color:#444;font-size:.9rem; }}
.warn {{ background:#fff8e1;border-left:4px solid #f0ad4e;padding:.8rem 1rem;font-size:.88rem;border-radius:4px; }}
.meta {{ color:#888;font-size:.85rem; }}
a.back {{ display:inline-block;margin-bottom:1rem;font-size:.9rem; }}
@media(max-width:640px) {{ table {{ font-size:.78rem; }} th,td {{ padding:.35rem .25rem; }} }}
</style></head><body>
<a class="back" href="index.html">← Portföy paneline dön</a>
<h1>🔬 Backtest Raporu — {period_title}</h1>
<p class="meta">Üretim: {now} &nbsp;·&nbsp; strateji başına {universe_size} hisse &nbsp;·&nbsp; {cost_note}</p>

<img src="../reports/backtest.png" alt="Backtest karşılaştırması" onerror="this.style.display='none'">

<h2>Sonuçlar</h2>
<table>
<thead><tr><th>Strateji</th><th>Getiri</th><th>CAGR</th><th>Max DD</th><th>Sharpe</th>
<th>Profit Factor</th><th>İşlem</th><th>Kazanma</th><th>Piyasada</th></tr></thead>
<tbody>{rows}</tbody>
</table>

<h2>Strateji detayları</h2>
<div class="cards">{detail}</div>

<h2>Sütunlar ne anlama geliyor?</h2>
<dl>{helps}</dl>

<h2>Bu sonuçlar neden olduğundan iyi görünür</h2>
<div class="warn">
<p><b>Survivorship bias (en büyük çarpıtma):</b> Evren <i>bugünün</i> endeks üyeliği.
İflas eden veya endeksten çıkarılan şirketler listede yok, dolayısıyla backtest onları
hiç satın almıyor. Gerçek geçmişte bu şirketler alınabilirdi.</p>
<p><b>Maliyet:</b> {cost_note}. Yüzlerce işlem yapan bir strateji, gerçek komisyon ve
makas altında burada göründüğünden belirgin biçimde kötü performans gösterir.</p>
<p><b>Emir dolumu:</b> Sinyalin oluştuğu günün kapanışından alım/satım yapılıyor.
Canlı bot da kapanışa yakın çalıştığı için buna yakın, ama yine de iyimser.</p>
<p><b>Dönem seçimi:</b> Tek bir geçmiş dönem tek bir senaryodur. Farklı yıl aralıkları
farklı sıralama üretebilir; sonuçları bir tahmin değil, kuralların davranış testi olarak oku.</p>
</div>
</body></html>"""
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(path, "w") as f:
        f.write(html)


def _date_arg(v):
    try:
        return datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{v}' YYYY-MM-DD formatında olmalı")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=3,
                    help="Son N yıl test edilir. --start/--end verilirse yok sayılır.")
    ap.add_argument("--start", type=_date_arg, default=None,
                    help="Test aralığının başlangıcı (YYYY-MM-DD). --end ile birlikte verilmeli.")
    ap.add_argument("--end", type=_date_arg, default=None,
                    help="Test aralığının bitişi (YYYY-MM-DD). --start ile birlikte verilmeli.")
    ap.add_argument("--cost-bps", type=float, default=0.0,
                    help="Per-side transaction cost in basis points (10 = 0.10%%).")
    ap.add_argument("--universe-size", type=int, default=80,
                    help="Names per strategy universe, ranked by dollar volume. "
                         "Larger is more faithful but much slower.")
    args = ap.parse_args()

    if bool(args.start) != bool(args.end):
        ap.error("--start ve --end birlikte verilmeli")

    custom_range = args.start is not None
    if custom_range:
        if args.start >= args.end:
            ap.error("--start, --end tarihinden önce olmalı")
        range_start, range_end = args.start, args.end
        fetch_start = range_start - pd.DateOffset(years=2)   # indicator warm-up buffer
        fetch_end = range_end + pd.DateOffset(days=1)         # yfinance end is exclusive
        years = (range_end - range_start).days / 365.25
        period_title = f"{range_start.date()} → {range_end.date()}"
    else:
        range_start, range_end = None, None
        fetch_start = datetime.now() - pd.DateOffset(years=args.years + 2)
        fetch_end = datetime.now() + pd.DateOffset(days=1)
        years = args.years
        period_title = f"son {years} yıl"

    COST_BPS["value"] = args.cost_bps
    install_fast_indicators()
    universes = universe_mod.build_universe()

    wanted = {SPY}
    for m in STRATEGY_MODULES.values():
        wanted |= set(universes[m.UNIVERSE_KEY].keys())

    data = download(wanted, fetch_start, fetch_end)
    global _FULL
    _FULL = data
    spy_df = data.get(SPY)
    if spy_df is None:
        print("[error] SPY verisi yok")
        return

    if custom_range:
        dates = spy_df.loc[range_start:range_end].index
    else:
        dates = spy_df.index[-(args.years * 252):]
    print(f"[run] {dates[0].date()} → {dates[-1].date()} ({len(dates)} işlem günü)")

    curves, results = {}, {}
    for name, module in STRATEGY_MODULES.items():
        sector_map = universes[module.UNIVERSE_KEY]
        syms = rank_by_dollar_volume(data, sector_map, args.universe_size)
        print(f"[run] {name}: {len(syms)} hisse")
        _CACHE.clear()
        lg, curve, invested_pct = run(name, syms, data, spy_df, sector_map, dates)
        curves[name] = curve
        results[name] = metrics(curve, lg, years, invested_pct)
        results[name]["universe_size"] = len(syms)

    bench = benchmark(spy_df, dates, years)
    chart = os.path.join(REPORTS_DIR, "backtest.png")
    plot(curves, bench, period_title, chart)

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

    out = {"generated": datetime.now(timezone.utc).isoformat(), "years": years,
           "universe_size": args.universe_size, "strategies": results, "spy_benchmark":
           {k: v for k, v in bench.items() if k != "curve"}}
    if custom_range:
        out["start"] = range_start.strftime("%Y-%m-%d")
        out["end"] = range_end.strftime("%Y-%m-%d")
    with open(os.path.join(REPORTS_DIR, "backtest.json"), "w") as f:
        json.dump(out, f, indent=2)

    build_html(results, bench, period_title, args.universe_size, args.cost_bps,
               os.path.join(DOCS_DIR, "backtest.html"))
    print("[ok] docs/backtest.html yazıldı")

    try:
        import telegram_notify
        telegram_caption = period_title if custom_range else f"{years} yıl"
        telegram_notify.send_photo(chart, caption=f"🔬 BACKTEST ({telegram_caption})\n\n```\n{table}\n```")
    except Exception as e:
        print(f"[warn] telegram: {e}")


if __name__ == "__main__":
    main()
