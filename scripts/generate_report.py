"""
Generates period reports (daily/weekly/monthly/yearly):
- equity-curve comparison chart across the 3 strategies (PNG, sent to Telegram)
- per-strategy summary stats (return, win rate, trade count in period)
- regenerates docs/index.html -- the live dashboard showing current holdings with
  entry date/price/reasoning, current price and open P&L per position.

Everything is valued at live market prices. Valuing open positions at cost makes a
fully-invested book read as exactly its starting capital no matter what the market
did, which is why the header figure has to come from fetched prices rather than
from the position's average cost.
"""
import argparse
import os
import shutil
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter

import ledger as ledger_mod
import telegram_notify

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")
ARCHIVE_DIR = os.path.join(REPORTS_DIR, "archive")
DOCS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")

PERIOD_DAYS = {"daily": 1, "weekly": 7, "monthly": 30, "yearly": 365}

# Smallest y-range the equity chart will show, in percentage points.
# Without a floor, matplotlib zooms onto a 0.03% move and draws it as a cliff.
MIN_Y_SPAN_PCTPOINTS = 1.0

NY_TZ = ZoneInfo("America/New_York")


def parse_dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def exchange_day_start(now_utc: datetime) -> datetime:
    """Start (midnight) of the current exchange calendar day, in UTC.

    Used only for the daily chart's time window -- a plain `now - 24h` rolling
    window has its boundary drift with run time, so it can slice a trading day
    in half instead of showing one full session.
    """
    local_midnight = now_utc.astimezone(NY_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(timezone.utc)


def filter_since(items, since_dt, date_key="date"):
    return [x for x in items if parse_dt(x[date_key]) >= since_dt]


def fetch_current_prices(symbols) -> dict:
    """Last close for each held symbol. Empty dict on failure -- callers fall back to cost."""
    symbols = sorted(set(symbols))
    if not symbols:
        return {}
    try:
        import yfinance as yf
        raw = yf.download(symbols, period="5d", group_by="ticker",
                          auto_adjust=True, threads=True, progress=False)
    except Exception as e:
        print(f"[warn] price fetch failed: {e}")
        return {}

    prices = {}
    for sym in symbols:
        try:
            df = raw[sym] if len(symbols) > 1 else raw
            df = df.dropna(how="all")
            if not df.empty:
                prices[sym] = float(df["Close"].iloc[-1])
        except Exception:
            pass
    print(f"[data] priced {len(prices)}/{len(symbols)} held symbols")
    return prices


def summarize(lg, since_dt, prices):
    trades_in_period = filter_since(lg["trades"], since_dt)
    sells = [t for t in trades_in_period if t["action"] == "SELL"]
    wins = [t for t in sells if t.get("pnl", 0) > 0]
    win_rate = (len(wins) / len(sells) * 100) if sells else None

    current_equity = ledger_mod.total_equity(lg, prices)

    hist = lg["equity_history"]
    past = [h for h in hist if parse_dt(h["date"]) <= since_dt]
    start_equity = past[-1]["equity"] if past else lg["starting_cash"]

    period_return = (current_equity / start_equity - 1) * 100 if start_equity else 0
    total_return = (current_equity / lg["starting_cash"] - 1) * 100

    return {
        "current_equity": current_equity,
        "period_pnl": current_equity - start_equity,
        "total_pnl": current_equity - lg["starting_cash"],
        "period_return_pct": period_return,
        "total_return_pct": total_return,
        "trades_in_period": len(trades_in_period),
        "win_rate": win_rate,
    }


def _daily_points(hist):
    """Collapse multiple same-day snapshots to that day's last value."""
    by_day = OrderedDict()
    for h in hist:
        d = parse_dt(h["date"])
        by_day[d.date()] = (d, h["equity"])
    return [v for v in by_day.values()]


def plot_comparison(ledgers: dict, since_dt, mode: str, prices: dict) -> str:
    """
    Percent-change-from-start equity curves, drawn as step lines so a flat segment
    between two snapshots reads as "no new data" instead of implying a smooth move.
    Intraday points are collapsed to one per day for every period except `daily`,
    where the intraday detail is the whole point.
    """
    fig, ax = plt.subplots(figsize=(9, 5))
    all_pcts = [0.0]

    for name, lg in ledgers.items():
        starting_cash = lg["starting_cash"]
        hist = filter_since(lg["equity_history"], since_dt) or lg["equity_history"][-2:]
        if not hist:
            continue

        # Append a live-priced point so the curve ends at today's real value.
        live = ledger_mod.total_equity(lg, prices)
        points = [(parse_dt(h["date"]), h["equity"]) for h in hist]
        points.append((datetime.now(timezone.utc), live))

        if mode != "daily":
            points = _daily_points([{"date": d.isoformat(), "equity": e} for d, e in points])

        dates = [p[0] for p in points]
        pcts = [(equity / starting_cash - 1) * 100 for _, equity in points]
        all_pcts.extend(pcts)
        ax.plot(dates, pcts, marker="o", markersize=3, drawstyle="steps-post", label=name)

    ax.axhline(y=0, color="gray", linestyle="--", linewidth=1, label="Başlangıç (%0)")

    # Symmetric around 0% so a +2%/-1% spread doesn't look lopsided, with a floor
    # span so matplotlib doesn't zoom a 0.03% move into a cliff.
    bound = max(abs(min(all_pcts)), abs(max(all_pcts)), MIN_Y_SPAN_PCTPOINTS / 2)
    pad = bound * 0.15
    ax.set_ylim(-(bound + pad), bound + pad)

    ax.set_title(f"Strateji Karşılaştırması — {mode} (UTC)")
    ax.set_xlabel("Zaman (UTC)")
    ax.set_ylabel("Başlangıca Göre Değişim (%)")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, p: f"{v:+.1f}%"))

    ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=10))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))

    ax.grid(alpha=0.25)
    ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1), borderaxespad=0)
    fig.autofmt_xdate()

    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, f"comparison_{mode}.png")
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)

    # Dated copy so past reports survive the next run overwriting comparison_{mode}.png.
    # Same-day reruns (e.g. workflow_dispatch) just overwrite that day's archive file.
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    date_tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    archive_path = os.path.join(ARCHIVE_DIR, f"comparison_{mode}_{date_tag}.png")
    shutil.copyfile(path, archive_path)

    return path


def build_dashboard_html(ledgers: dict, prices: dict) -> None:
    os.makedirs(DOCS_DIR, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    sections = []
    for name, lg in ledgers.items():
        equity = ledger_mod.total_equity(lg, prices)
        total_pnl = equity - lg["starting_cash"]
        total_return = (equity / lg["starting_cash"] - 1) * 100
        cls = "pos" if total_pnl >= 0 else "neg"

        rows = ""
        for symbol, pos in lg["positions"].items():
            entry_trade = next((t for t in reversed(lg["trades"])
                                if t["symbol"] == symbol and t["action"] == "BUY"), None)
            entry_date = entry_trade["date"][:10] if entry_trade else "-"
            reasoning = entry_trade["reasoning"] if entry_trade else "-"

            avg = pos["avg_price"]
            cur = prices.get(symbol)
            if cur is None:
                cur_txt, chg_txt, chg_cls = "—", "—", ""
            else:
                chg = (cur / avg - 1) * 100
                cur_txt = f"${cur:,.2f}"
                chg_txt = f"{chg:+.2f}%"
                chg_cls = "pos" if chg >= 0 else "neg"

            rows += (
                f"<tr><td><b>{symbol}</b></td><td>{pos['qty']}</td>"
                f"<td>${avg:,.2f}</td><td>{cur_txt}</td>"
                f"<td class='{chg_cls}'>{chg_txt}</td>"
                f"<td>{entry_date}</td><td class='why'>{reasoning}</td></tr>"
            )
        if not rows:
            rows = "<tr><td colspan='7'>Açık pozisyon yok</td></tr>"

        sections.append(f"""
        <section>
          <h2>{name}</h2>
          <p class="equity">Güncel değer: <b>${equity:,.2f}</b>
             <span class="{cls}">({total_pnl:+,.2f} $ / {total_return:+.2f}%)</span>
             &nbsp;·&nbsp; Nakit: ${lg['cash']:,.2f}</p>
          <table>
            <thead><tr><th>Sembol</th><th>Adet</th><th>Ort. Maliyet</th>
            <th>Güncel Fiyat</th><th>Değişim</th><th>Giriş</th><th>Gerekçe</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </section>""")

    html = f"""<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stock Paper Bot Dashboard</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       max-width: 1000px; margin: 2rem auto; padding: 0 1rem; color: #222; }}
h1 {{ font-size: 1.4rem; }}
h2 {{ font-size: 1.1rem; border-bottom: 2px solid #ddd; padding-bottom: .3rem; margin-top: 2rem; }}
table {{ width: 100%; border-collapse: collapse; margin: 1rem 0; font-size: .88rem; }}
th, td {{ text-align: left; padding: .45rem .5rem; border-bottom: 1px solid #eee; vertical-align: top; }}
th {{ background: #fafafa; font-weight: 600; }}
td.why {{ font-size: .8rem; color: #555; max-width: 380px; }}
.equity {{ font-size: 1rem; }}
.pos {{ color: #0a7d33; font-weight: 600; }}
.neg {{ color: #c62828; font-weight: 600; }}
img {{ max-width: 100%; }}
.updated {{ color: #888; font-size: .85rem; }}
@media (max-width: 640px) {{ td.why {{ display: none; }} th:last-child {{ display: none; }} }}
</style></head>
<body>
<h1>📊 Stock Paper Bot — Canlı Portföy Durumu</h1>
<p class="updated">Son güncelleme: {now} &nbsp;·&nbsp; <a href="backtest.html">🔬 Backtest raporu</a></p>
<img src="../reports/comparison_daily.png" alt="Equity comparison" onerror="this.style.display='none'">
{''.join(sections)}
</body></html>"""

    with open(os.path.join(DOCS_DIR, "index.html"), "w") as f:
        f.write(html)


def held_symbols(ledgers) -> set:
    out = set()
    for lg in ledgers.values():
        out |= set(lg["positions"].keys())
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=list(PERIOD_DAYS.keys()), default="daily")
    args = parser.parse_args()

    since_dt = datetime.now(timezone.utc) - timedelta(days=PERIOD_DAYS[args.mode])
    ledgers = {name: ledger_mod.load_ledger(name) for name in ledger_mod.STRATEGIES}
    prices = fetch_current_prices(held_symbols(ledgers))

    lines = [f"📈 *{args.mode.upper()} RAPOR*\n"]
    for name, lg in ledgers.items():
        s = summarize(lg, since_dt, prices)
        wr = f"{s['win_rate']:.0f}%" if s["win_rate"] is not None else "n/a"
        lines.append(
            f"*{name}*: ${s['current_equity']:,.2f} "
            f"({s['total_pnl']:+,.2f}$ / {s['total_return_pct']:+.2f}%) "
            f"— dönem {s['period_return_pct']:+.2f}%, "
            f"{s['trades_in_period']} işlem, kazanma oranı {wr}"
        )

    # The daily chart uses its own window aligned to the exchange's calendar day
    # boundary; the rolling `since_dt` above keeps driving the text summary stats
    # unchanged (period_return_pct, trades_in_period, ...).
    chart_since_dt = exchange_day_start(datetime.now(timezone.utc)) if args.mode == "daily" else since_dt
    chart_path = plot_comparison(ledgers, chart_since_dt, args.mode, prices)
    build_dashboard_html(ledgers, prices)
    telegram_notify.send_photo(chart_path, caption="\n".join(lines))


if __name__ == "__main__":
    main()
