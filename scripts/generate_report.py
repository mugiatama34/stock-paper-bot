"""
Generates period reports (daily/weekly/monthly/yearly):
- equity-curve comparison chart across the 3 strategies (PNG, sent to Telegram)
- per-strategy equity chart (PNG) for that strategy's own dashboard page
- per-strategy summary stats (return, win rate, trade count in period)
- regenerates the docs/ dashboard: an index page (per-strategy summary cards +
  comparison chart), one detail page per strategy (equity chart, open positions,
  closed trade history with reasoning, summary metrics), and an archive page
  listing past reports/archive/ snapshots by date.

Everything is valued at live market prices. Valuing open positions at cost makes a
fully-invested book read as exactly its starting capital no matter what the market
did, which is why the header figure has to come from fetched prices rather than
from the position's average cost.

All chart PNGs render on a dark background with light axes/text regardless of the
viewer's system theme -- the dashboard HTML itself follows prefers-color-scheme,
but a chart baked as an image can't switch at view time, so it's fixed dark and
sits in a matching dark card in both page themes.
"""
import argparse
import os
import re
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
MODE_LABELS = {"daily": "Günlük", "weekly": "Haftalık", "monthly": "Aylık", "yearly": "Yıllık"}

# Closed-trade history is capped per strategy page so it can't grow without bound.
CLOSED_TRADES_LIMIT = 100

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


def format_dt(iso: str) -> str:
    return parse_dt(iso).strftime("%d.%m.%Y %H:%M UTC")


def format_duration(start_iso: str, end_dt: datetime) -> str:
    delta = end_dt - parse_dt(start_iso)
    days = delta.days
    hours = delta.seconds // 3600
    if days > 0:
        return f"{days} gün" + (f" {hours} saat" if hours else "")
    if hours > 0:
        return f"{hours} saat"
    return f"{max(delta.seconds // 60, 0)} dakika"


def build_episodes(trades: list, symbol: str) -> list:
    """
    Splits one symbol's trade history into buy-to-flat episodes: each spans from
    the BUY that opened a flat position through the SELL(s) that eventually
    brought it back to zero. Positions in the ledger only carry a blended
    avg_price -- no entry date or reasoning survives an add -- so this replay
    of `trades` is how the report recovers "when/why was this opened" for both
    the open-position detail panel and the closed-trade history.
    """
    episodes = []
    current = None
    running_qty = 0
    for t in trades:
        if t["symbol"] != symbol:
            continue
        if t["action"] == "BUY":
            if running_qty <= 0:
                current = {"entry_date": t["date"], "buys": [], "sells": [],
                           "closed": False, "close_date": None}
                episodes.append(current)
            current["buys"].append(t)
            running_qty += t["qty"]
        elif current is not None:
            current["sells"].append(t)
            running_qty -= t["qty"]
            if running_qty <= 0:
                current["closed"] = True
                current["close_date"] = t["date"]
                running_qty = 0
    return episodes


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
    with plt.style.context("dark_background"):
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
            ax.plot(dates, pcts, marker="o", markersize=3, drawstyle="steps-post",
                    label=ledger_mod.STRATEGY_LABELS.get(name, name))

        ax.axhline(y=0, color="#888", linestyle="--", linewidth=1, label="Başlangıç (%0)")

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
    # Archiving is best-effort: a disk/permission failure here must not take down
    # report generation or the Telegram notification.
    try:
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        date_tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        archive_path = os.path.join(ARCHIVE_DIR, f"comparison_{mode}_{date_tag}.png")
        shutil.copyfile(path, archive_path)
    except Exception as e:
        print(f"[warn] archive copy failed: {e}")

    return path


def plot_equity_curve(name: str, lg: dict) -> str:
    """Full-history equity curve (% change from start) for one strategy's own page."""
    hist = lg["equity_history"]
    if not hist:
        return None
    starting_cash = lg["starting_cash"]
    points = _daily_points(hist)
    dates = [p[0] for p in points]
    pcts = [(equity / starting_cash - 1) * 100 for _, equity in points]

    with plt.style.context("dark_background"):
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(dates, pcts, marker="o", markersize=3, drawstyle="steps-post", color="#7ea2ff")
        ax.axhline(y=0, color="#888", linestyle="--", linewidth=1)

        bound = max(abs(min(pcts)), abs(max(pcts)), MIN_Y_SPAN_PCTPOINTS / 2)
        pad = bound * 0.15
        ax.set_ylim(-(bound + pad), bound + pad)

        ax.set_title(f"{ledger_mod.STRATEGY_LABELS.get(name, name)} — Başlangıca Göre Değişim (%)")
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, p: f"{v:+.1f}%"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=8))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m.%y"))

        ax.grid(alpha=0.25)
        fig.autofmt_xdate()

        os.makedirs(REPORTS_DIR, exist_ok=True)
        path = os.path.join(REPORTS_DIR, f"equity_{name}.png")
        fig.savefig(path, dpi=130, bbox_inches="tight")
        plt.close(fig)

    return path


PAGE_CSS = """
:root {
  --bg: #f5f5f7;
  --card-bg: #ffffff;
  --text: #1a1a1a;
  --muted: #68686d;
  --border: #e2e2e5;
  --pos: #0a7d33;
  --neg: #c62828;
  --accent: #2f6fed;
  --chart-bg: #111318;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0e0e10;
    --card-bg: #1b1b1e;
    --text: #eaeaec;
    --muted: #9a9aa0;
    --border: #303034;
    --pos: #4caf6d;
    --neg: #ef5350;
    --accent: #7ea2ff;
    --chart-bg: #111318;
  }
}
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: var(--bg); color: var(--text);
  max-width: 720px; margin: 0 auto; padding: 1.25rem 1rem 3rem;
  line-height: 1.5;
}
h1 { font-size: 1.35rem; margin: .2rem 0 .6rem; }
h2.section-title { font-size: 1rem; margin: 1.75rem 0 .6rem; color: var(--muted);
  text-transform: uppercase; letter-spacing: .04em; }
a { color: var(--accent); text-decoration: none; }
a.back { display: inline-block; margin-bottom: .75rem; font-size: .9rem; }
.muted { color: var(--muted); font-size: .85rem; }
.big { font-size: 1.3rem; }
.pos { color: var(--pos); font-weight: 600; }
.neg { color: var(--neg); font-weight: 600; }

.cards { display: flex; flex-direction: column; gap: .75rem; }
.strategy-card {
  display: block; background: var(--card-bg); border: 1px solid var(--border);
  border-radius: 12px; padding: .9rem 1.1rem; color: inherit;
}
.strategy-card h2 { margin: 0 0 .3rem; font-size: 1rem; color: var(--text); }
.strategy-card p { margin: .15rem 0; }

.chart-card {
  background: var(--chart-bg); border-radius: 12px; padding: .5rem;
  margin: .5rem 0 1rem;
}
.chart-card img { display: block; width: 100%; border-radius: 6px; }

.chart-tabs input { display: none; }
.chart-tabs label {
  display: inline-block; padding: .35rem .85rem; margin: 0 .4rem .6rem 0;
  border: 1px solid var(--border); border-radius: 20px; font-size: .82rem;
  cursor: pointer; color: var(--muted);
}
.chart-tabs input:checked + label { background: var(--accent); color: #fff; border-color: var(--accent); }
.chart-tabs .chart-card { display: none; }
#tab-daily:checked ~ #panel-daily,
#tab-weekly:checked ~ #panel-weekly,
#tab-monthly:checked ~ #panel-monthly,
#tab-yearly:checked ~ #panel-yearly { display: block; }

.metrics {
  display: grid; grid-template-columns: repeat(2, 1fr); gap: .6rem 1rem;
  background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px;
  padding: .9rem 1.1rem; margin: .75rem 0;
}
.metrics div { font-size: .9rem; }

.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: .85rem; }
th, td { text-align: left; padding: .5rem .5rem; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--muted); font-weight: 600; font-size: .78rem; text-transform: uppercase; }
details summary { cursor: pointer; color: var(--accent); font-size: .82rem; }
details[open] summary { margin-bottom: .3rem; }

.archive-list { list-style: none; padding: 0; margin: .3rem 0 1rem;
  display: flex; flex-wrap: wrap; gap: .4rem; }
.archive-list li a {
  display: inline-block; padding: .3rem .7rem; border: 1px solid var(--border);
  border-radius: 8px; font-size: .82rem;
}

@media (min-width: 640px) {
  .cards { flex-direction: row; }
  .strategy-card { flex: 1; }
  .metrics { grid-template-columns: repeat(5, 1fr); }
}

tr.row-click { cursor: pointer; }
tr.row-click:hover { background: rgba(127, 127, 130, .08); }
tr.row-click td.chevron-cell { width: 1.2rem; text-align: right; }
.chevron { display: inline-block; color: var(--muted); font-size: .75rem; transition: transform .15s ease; }
tr.row-click.open .chevron { transform: rotate(90deg); }
tr.detail-row { display: none; }
tr.detail-row.open { display: table-row; }
tr.detail-row td { background: var(--bg); padding: .8rem .9rem 1rem; }
.detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: .5rem 1rem; margin-bottom: .7rem; }
.detail-grid > div { font-size: .82rem; }
.detail-section-title { font-size: .72rem; text-transform: uppercase; letter-spacing: .04em;
  color: var(--muted); margin: .7rem 0 .3rem; }
.detail-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .5rem; }
.detail-list li { font-size: .82rem; border-left: 2px solid var(--border); padding-left: .6rem; }
.reasoning-text { color: var(--text); }
@media (max-width: 480px) {
  .detail-grid { grid-template-columns: 1fr; }
}
"""


APP_ICON_HEAD = """<link rel="icon" type="image/svg+xml" href="icons/icon.svg">
<link rel="alternate icon" href="icons/favicon.ico">
<link rel="icon" type="image/png" sizes="32x32" href="icons/favicon-32.png">
<link rel="icon" type="image/png" sizes="16x16" href="icons/favicon-16.png">
<link rel="apple-touch-icon" sizes="180x180" href="icons/apple-touch-icon.png">
<link rel="manifest" href="manifest.json">
<meta name="theme-color" content="#0e0e10">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Paper Bot">"""


def page_shell(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
{APP_ICON_HEAD}
<style>{PAGE_CSS}</style></head>
<body>
{body}
</body></html>"""


def nav_back() -> str:
    return '<a class="back" href="index.html">← Ana sayfa</a>'


def render_comparison_tabs() -> str:
    available = [m for m in PERIOD_DAYS
                 if os.path.exists(os.path.join(REPORTS_DIR, f"comparison_{m}.png"))]
    if not available:
        return '<p class="muted">Henüz karşılaştırma grafiği yok.</p>'

    if len(available) == 1:
        m = available[0]
        return (f'<div class="chart-card"><img src="../reports/comparison_{m}.png" '
                f'alt="{MODE_LABELS[m]} karşılaştırma"></div>')

    inputs = "".join(
        f'<input type="radio" name="cmp-tab" id="tab-{m}"{" checked" if i == 0 else ""}>'
        f'<label for="tab-{m}">{MODE_LABELS[m]}</label>'
        for i, m in enumerate(available)
    )
    panels = "".join(
        f'<div class="chart-card" id="panel-{m}"><img src="../reports/comparison_{m}.png" '
        f'alt="{MODE_LABELS[m]} karşılaştırma"></div>'
        for m in available
    )
    return f'<div class="chart-tabs">{inputs}{panels}</div>'


def render_index_html(ledgers: dict, prices: dict) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    cards = []
    for name, lg in ledgers.items():
        equity = ledger_mod.total_equity(lg, prices)
        total_return = (equity / lg["starting_cash"] - 1) * 100
        cls = "pos" if total_return >= 0 else "neg"
        cards.append(f"""<a class="strategy-card" href="strategy_{name}.html">
          <h2>{ledger_mod.STRATEGY_LABELS.get(name, name)}</h2>
          <p class="big">${equity:,.2f}</p>
          <p class="{cls}">{total_return:+.2f}%</p>
          <p class="muted">{len(lg['positions'])} açık pozisyon</p>
        </a>""")

    body = f"""<h1>Stock Paper Bot</h1>
<p class="muted">Son güncelleme: {now} &nbsp;·&nbsp;
  <a href="archive.html">Arşiv</a> &nbsp;·&nbsp; <a href="backtest.html">Backtest</a></p>
<div class="cards">{''.join(cards)}</div>
<h2 class="section-title">Karşılaştırma</h2>
{render_comparison_tabs()}"""

    with open(os.path.join(DOCS_DIR, "index.html"), "w") as f:
        f.write(page_shell("Stock Paper Bot Dashboard", body))


def render_strategy_html(name: str, lg: dict, prices: dict) -> None:
    equity = ledger_mod.total_equity(lg, prices)
    total_return = (equity / lg["starting_cash"] - 1) * 100
    total_pnl = equity - lg["starting_cash"]
    sells = [t for t in lg["trades"] if t["action"] == "SELL"]
    wins = [t for t in sells if t.get("pnl", 0) > 0]
    win_rate = (len(wins) / len(sells) * 100) if sells else None
    cls = "pos" if total_pnl >= 0 else "neg"

    metrics = f"""<div class="metrics">
      <div><span class="muted">Güncel Değer</span><br><b class="big">${equity:,.2f}</b></div>
      <div><span class="muted">Toplam Getiri</span><br>
        <b class="{cls}">{total_return:+.2f}% ({total_pnl:+,.2f}$)</b></div>
      <div><span class="muted">Kazanma Oranı</span><br>
        <b>{f"{win_rate:.0f}%" if win_rate is not None else "n/a"}</b></div>
      <div><span class="muted">İşlem Sayısı</span><br><b>{len(sells)}</b></div>
      <div><span class="muted">Nakit</span><br><b>${lg['cash']:,.2f}</b></div>
    </div>"""

    chart_path = os.path.join(REPORTS_DIR, f"equity_{name}.png")
    chart_html = (
        f'<div class="chart-card"><img src="../reports/equity_{name}.png" alt="Equity grafiği"></div>'
        if os.path.exists(chart_path) else ""
    )

    # Positions don't store their own entry date/reasoning (only the blended
    # avg_price survives an add), so both tables below replay `trades` per
    # symbol into buy-to-flat episodes to recover that detail on click.
    symbols_all = {t["symbol"] for t in lg["trades"]}
    episodes_by_symbol = {}
    sell_episode_lookup = {}
    for sym in symbols_all:
        eps = build_episodes(lg["trades"], sym)
        episodes_by_symbol[sym] = eps
        for ep in eps:
            for s in ep["sells"]:
                sell_episode_lookup[id(s)] = ep

    now = datetime.now(timezone.utc)

    open_rows = ""
    for i, (symbol, pos) in enumerate(lg["positions"].items()):
        avg = pos["avg_price"]
        qty = pos["qty"]
        stop = pos["stop_price"]
        cur = prices.get(symbol)
        if cur is None:
            cur_txt, pnl_txt, pnl_cls = "—", "—", ""
            pnl_dollar_txt, stop_dist_txt = "—", "—"
        else:
            pnl_pct = (cur / avg - 1) * 100
            cur_txt = f"${cur:,.2f}"
            pnl_txt = f"{pnl_pct:+.2f}%"
            pnl_cls = "pos" if pnl_pct >= 0 else "neg"
            pnl_dollar_txt = f"{(cur - avg) * qty:+,.2f}$"
            stop_dist_txt = f"{(cur - stop) / cur * 100:.2f}%"

        episodes = episodes_by_symbol.get(symbol, [])
        cur_episode = episodes[-1] if episodes and not episodes[-1]["closed"] else None
        prior_episodes = episodes[:-1] if cur_episode else episodes

        entry_date_txt = format_dt(cur_episode["entry_date"]) if cur_episode else "-"
        open_days_txt = format_duration(cur_episode["entry_date"], now) if cur_episode else "-"

        buys_html = "".join(
            f"<li><span class='muted'>{format_dt(b['date'])}</span> — {b['qty']} adet @ "
            f"${b['price']:,.2f}<br><span class='reasoning-text'>{b.get('reasoning', '-')}</span></li>"
            for b in (cur_episode["buys"] if cur_episode else [])
        ) or "<li class='muted'>Alım kaydı bulunamadı.</li>"

        history_items = []
        for ep in prior_episodes:
            total_pnl = sum(s.get("pnl", 0) for s in ep["sells"])
            hist_cls = "pos" if total_pnl >= 0 else "neg"
            last_reasoning = ep["sells"][-1].get("reasoning", "-") if ep["sells"] else "-"
            history_items.append(
                f"<li>{format_dt(ep['entry_date'])} → {format_dt(ep['close_date'])} "
                f"(<span class='{hist_cls}'>{total_pnl:+,.2f}$</span>)<br>"
                f"<span class='reasoning-text'>{last_reasoning}</span></li>"
            )
        history_html = "".join(history_items) or "<li class='muted'>Bu sembolde daha önce kapanmış işlem yok.</li>"

        detail_id = f"d-open-{i}"
        open_rows += (
            f"<tr class='row-click' data-target='{detail_id}'><td><b>{symbol}</b></td><td>${avg:,.2f}</td>"
            f"<td>{cur_txt}</td><td>${stop:,.2f}</td><td class='{pnl_cls}'>{pnl_txt}</td>"
            f"<td class='chevron-cell'><span class='chevron'>▸</span></td></tr>"
            f"<tr class='detail-row' id='{detail_id}'><td colspan='6'>"
            f"<div class='detail-grid'>"
            f"<div><span class='muted'>Alım tarihi</span><br>{entry_date_txt}</div>"
            f"<div><span class='muted'>Açık süre</span><br>{open_days_txt}</div>"
            f"<div><span class='muted'>Adet</span><br>{qty}</div>"
            f"<div><span class='muted'>Ortalama alım fiyatı</span><br>${avg:,.2f}</div>"
            f"<div><span class='muted'>Pozisyon büyüklüğü (maliyet)</span><br>${qty * avg:,.2f}</div>"
            f"<div><span class='muted'>Güncel fiyat</span><br>{cur_txt}</div>"
            f"<div><span class='muted'>Güncel K/Z</span><br><span class='{pnl_cls}'>{pnl_dollar_txt} ({pnl_txt})</span></div>"
            f"<div><span class='muted'>Stop seviyesi</span><br>${stop:,.2f}</div>"
            f"<div><span class='muted'>Stop'a uzaklık</span><br>{stop_dist_txt}</div>"
            f"</div>"
            f"<div class='detail-section-title'>Alım gerekçesi</div>"
            f"<ul class='detail-list'>{buys_html}</ul>"
            f"<div class='detail-section-title'>Bu sembolde önceki kapanmış işlemler</div>"
            f"<ul class='detail-list'>{history_html}</ul>"
            f"</td></tr>"
        )
    if not open_rows:
        open_rows = "<tr><td colspan='6' class='muted'>Açık pozisyon yok</td></tr>"

    closed_rows = ""
    for i, t in enumerate(list(reversed(sells))[:CLOSED_TRADES_LIMIT]):
        pnl = t.get("pnl", 0)
        pnl_cls = "pos" if pnl >= 0 else "neg"
        owning_episode = sell_episode_lookup.get(id(t))
        entry_date_txt = format_dt(owning_episode["entry_date"]) if owning_episode else "-"
        hold_txt = format_duration(owning_episode["entry_date"], parse_dt(t["date"])) if owning_episode else "-"

        detail_id = f"d-closed-{i}"
        closed_rows += (
            f"<tr class='row-click' data-target='{detail_id}'><td>{t['date'][:10]}</td>"
            f"<td><b>{t['symbol']}</b></td><td>{t['qty']}</td><td>${t['price']:,.2f}</td>"
            f"<td class='{pnl_cls}'>{pnl:+,.2f}$</td>"
            f"<td class='chevron-cell'><span class='chevron'>▸</span></td></tr>"
            f"<tr class='detail-row' id='{detail_id}'><td colspan='6'>"
            f"<div class='detail-grid'>"
            f"<div><span class='muted'>Alım tarihi</span><br>{entry_date_txt}</div>"
            f"<div><span class='muted'>Satım tarihi</span><br>{format_dt(t['date'])}</div>"
            f"<div><span class='muted'>Tutulma süresi</span><br>{hold_txt}</div>"
            f"<div><span class='muted'>K/Z</span><br><span class='{pnl_cls}'>{pnl:+,.2f}$</span></div>"
            f"</div>"
            f"<div class='detail-section-title'>Çıkış sebebi / gerekçe</div>"
            f"<p class='reasoning-text'>{t.get('reasoning', '-')}</p>"
            f"</td></tr>"
        )
    if not closed_rows:
        closed_rows = "<tr><td colspan='6' class='muted'>Henüz kapanmış işlem yok</td></tr>"

    row_toggle_script = """<script>
document.querySelectorAll('tr.row-click').forEach(function (row) {
  row.addEventListener('click', function () {
    var detail = document.getElementById(row.dataset.target);
    if (!detail) return;
    var opening = !detail.classList.contains('open');
    detail.classList.toggle('open', opening);
    row.classList.toggle('open', opening);
  });
});
</script>"""

    body = f"""{nav_back()}
<h1>{ledger_mod.STRATEGY_LABELS.get(name, name)}</h1>
{metrics}
{chart_html}
<h2 class="section-title">Açık Pozisyonlar</h2>
<div class="table-wrap"><table>
<thead><tr><th>Sembol</th><th>Giriş</th><th>Güncel</th><th>Stop</th><th>K/Z</th><th></th></tr></thead>
<tbody>{open_rows}</tbody></table></div>
<h2 class="section-title">Kapanmış İşlemler</h2>
<div class="table-wrap"><table>
<thead><tr><th>Tarih</th><th>Sembol</th><th>Adet</th><th>Fiyat</th><th>K/Z</th><th></th></tr></thead>
<tbody>{closed_rows}</tbody></table></div>
{row_toggle_script}"""

    with open(os.path.join(DOCS_DIR, f"strategy_{name}.html"), "w") as f:
        f.write(page_shell(f"{ledger_mod.STRATEGY_LABELS.get(name, name)} — Stock Paper Bot", body))


_ARCHIVE_FILE_RE = re.compile(r"^comparison_(daily|weekly|monthly|yearly)_(\d{4}-\d{2}-\d{2})\.png$")


def render_archive_html() -> None:
    groups = {m: [] for m in PERIOD_DAYS}
    if os.path.isdir(ARCHIVE_DIR):
        for fname in os.listdir(ARCHIVE_DIR):
            m = _ARCHIVE_FILE_RE.match(fname)
            if m:
                groups[m.group(1)].append((m.group(2), fname))

    sections = []
    for mode in PERIOD_DAYS:
        entries = sorted(groups[mode], reverse=True)
        if not entries:
            continue
        items = "".join(
            f'<li><a href="../reports/archive/{fname}">{date}</a></li>'
            for date, fname in entries
        )
        sections.append(
            f'<details{" open" if mode == "daily" else ""}>'
            f'<summary>{MODE_LABELS[mode]} ({len(entries)})</summary>'
            f'<ul class="archive-list">{items}</ul></details>'
        )

    content = "".join(sections) if sections else '<p class="muted">Henüz arşivlenmiş rapor yok.</p>'
    body = f"""{nav_back()}
<h1>Arşiv</h1>
{content}"""

    with open(os.path.join(DOCS_DIR, "archive.html"), "w") as f:
        f.write(page_shell("Arşiv — Stock Paper Bot", body))


def build_dashboard(ledgers: dict, prices: dict) -> None:
    os.makedirs(DOCS_DIR, exist_ok=True)
    for name, lg in ledgers.items():
        plot_equity_curve(name, lg)
    render_index_html(ledgers, prices)
    for name, lg in ledgers.items():
        render_strategy_html(name, lg, prices)
    render_archive_html()


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
            f"*{ledger_mod.STRATEGY_LABELS.get(name, name)}*: ${s['current_equity']:,.2f} "
            f"({s['total_pnl']:+,.2f}$ / {s['total_return_pct']:+.2f}%) "
            f"— dönem {s['period_return_pct']:+.2f}%, "
            f"{s['trades_in_period']} işlem, kazanma oranı {wr}"
        )

    # The daily chart uses its own window aligned to the exchange's calendar day
    # boundary; the rolling `since_dt` above keeps driving the text summary stats
    # unchanged (period_return_pct, trades_in_period, ...).
    chart_since_dt = exchange_day_start(datetime.now(timezone.utc)) if args.mode == "daily" else since_dt
    chart_path = plot_comparison(ledgers, chart_since_dt, args.mode, prices)
    build_dashboard(ledgers, prices)
    telegram_notify.send_photo(chart_path, caption="\n".join(lines))


if __name__ == "__main__":
    main()
