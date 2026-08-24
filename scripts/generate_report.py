"""
Generates period reports (daily/weekly/monthly/yearly):
- equity-curve comparison chart across the 3 strategies (PNG, sent to Telegram)
- per-strategy summary stats (return %, win rate, trade count in period)
- regenerates docs/index.html — a static dashboard (for GitHub Pages) showing
  current holdings per strategy with entry date/price/reasoning, viewable anytime.
"""
import argparse
import os
from datetime import datetime, timedelta, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

import ledger as ledger_mod
import telegram_notify

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")
DOCS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")

PERIOD_DAYS = {"daily": 1, "weekly": 7, "monthly": 30, "yearly": 365}


def parse_dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def filter_since(items, since_dt, date_key="date"):
    return [x for x in items if parse_dt(x[date_key]) >= since_dt]


def summarize(lg, since_dt):
    trades_in_period = filter_since(lg["trades"], since_dt)
    sells = [t for t in trades_in_period if t["action"] == "SELL"]
    wins = [t for t in sells if t.get("pnl", 0) > 0]
    win_rate = (len(wins) / len(sells) * 100) if sells else None

    hist = lg["equity_history"]
    if hist:
        current_equity = hist[-1]["equity"]
        past = [h for h in hist if parse_dt(h["date"]) <= since_dt]
        start_equity = past[-1]["equity"] if past else lg["starting_cash"]
    else:
        current_equity = lg["cash"]
        start_equity = lg["starting_cash"]

    period_return = (current_equity / start_equity - 1) * 100 if start_equity else 0
    total_return = (current_equity / lg["starting_cash"] - 1) * 100

    return {
        "current_equity": current_equity,
        "period_return_pct": period_return,
        "total_return_pct": total_return,
        "trades_in_period": len(trades_in_period),
        "win_rate": win_rate,
    }


def plot_comparison(ledgers: dict, since_dt, mode: str) -> str:
    """
    Plots % return (not raw dollars) relative to each strategy's $10K start.
    Raw-dollar equity curves look dramatic with matplotlib's tight auto-scaling
    and "+1e4" offset notation even for a $3 move — percentage return avoids
    that distortion entirely and is the more meaningful comparison anyway.
    """
    fig, ax = plt.subplots(figsize=(9, 5))
    all_returns = [0.0]  # ensure 0% baseline is always in view

    for name, lg in ledgers.items():
        hist = filter_since(lg["equity_history"], since_dt) or lg["equity_history"][-2:]
        if not hist:
            continue
        starting_cash = lg["starting_cash"]
        dates = [parse_dt(h["date"]) for h in hist]
        returns_pct = [(h["equity"] / starting_cash - 1) * 100 for h in hist]
        all_returns.extend(returns_pct)
        ax.plot(dates, returns_pct, marker="o", markersize=3, label=name)

    ax.axhline(y=0, color="gray", linestyle="--", linewidth=1, label="Başlangıç (%0)")

    # Pad the y-range so flat lines near 0% don't look like they're touching an edge,
    # and so a single outlier doesn't crush the others into invisibility.
    span = max(max(all_returns) - min(all_returns), 0.5)
    pad = span * 0.25 + 0.25
    ax.set_ylim(min(all_returns) - pad, max(all_returns) + pad)

    ax.set_title(f"Strateji Karşılaştırması — {mode}")
    ax.set_ylabel("Getiri (%)")
    ax.yaxis.set_major_formatter(lambda val, pos: f"{val:+.1f}%")
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()

    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, f"comparison_{mode}.png")
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def build_dashboard_html(ledgers: dict, current_prices: dict) -> None:
    os.makedirs(DOCS_DIR, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    sections = []
    for name, lg in ledgers.items():
        equity = ledger_mod.total_equity(lg, current_prices.get(name, {}))
        total_return = (equity / lg["starting_cash"] - 1) * 100
        rows = ""
        for symbol, pos in lg["positions"].items():
            entry_trade = next((t for t in reversed(lg["trades"])
                                 if t["symbol"] == symbol and t["action"] == "BUY"), None)
            entry_date = entry_trade["date"][:10] if entry_trade else "-"
            reasoning = entry_trade["reasoning"] if entry_trade else "-"
            rows += f"""<tr>
                <td>{symbol}</td><td>{pos['qty']}</td><td>${pos['avg_price']:.2f}</td>
                <td>{entry_date}</td><td>{reasoning}</td></tr>"""
        if not rows:
            rows = "<tr><td colspan='5'>Açık pozisyon yok</td></tr>"

        sections.append(f"""
        <section>
          <h2>{name}</h2>
          <p>Güncel değer: <b>${equity:,.2f}</b> ({total_return:+.2f}% toplam getiri)</p>
          <table>
            <thead><tr><th>Sembol</th><th>Adet</th><th>Ort. Maliyet</th><th>Giriş Tarihi</th><th>Gerekçe</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </section>""")

    html = f"""<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8">
<title>Stock Paper Bot Dashboard</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; color: #222; }}
h1 {{ font-size: 1.4rem; }} h2 {{ font-size: 1.1rem; border-bottom: 2px solid #ddd; padding-bottom: .3rem; }}
table {{ width: 100%; border-collapse: collapse; margin: 1rem 0; font-size: 0.9rem; }}
th, td {{ text-align: left; padding: .4rem .5rem; border-bottom: 1px solid #eee; }}
img {{ max-width: 100%; }}
.updated {{ color: #888; font-size: 0.85rem; }}
</style></head>
<body>
<h1>📊 Stock Paper Bot — Canlı Portföy Durumu</h1>
<p class="updated">Son güncelleme: {now}</p>
<img src="../reports/comparison_daily.png" alt="Equity comparison" onerror="this.style.display='none'">
{''.join(sections)}
</body></html>"""

    with open(os.path.join(DOCS_DIR, "index.html"), "w") as f:
        f.write(html)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=list(PERIOD_DAYS.keys()), default="daily")
    args = parser.parse_args()

    since_dt = datetime.now(timezone.utc) - timedelta(days=PERIOD_DAYS[args.mode])
    ledgers = {name: ledger_mod.load_ledger(name) for name in ledger_mod.STRATEGIES}

    lines = [f"📈 *{args.mode.upper()} RAPOR*\n"]
    for name, lg in ledgers.items():
        s = summarize(lg, since_dt)
        wr = f"{s['win_rate']:.0f}%" if s["win_rate"] is not None else "n/a"
        lines.append(
            f"*{name}*: ${s['current_equity']:,.2f} "
            f"(dönem: {s['period_return_pct']:+.2f}%, toplam: {s['total_return_pct']:+.2f}%) "
            f"— {s['trades_in_period']} işlem, kazanma oranı {wr}"
        )

    chart_path = plot_comparison(ledgers, since_dt, args.mode)
    build_dashboard_html(ledgers, {})  # current_prices left empty here; trade_bot keeps equity_history fresh

    telegram_notify.send_photo(chart_path, caption="\n".join(lines))


if __name__ == "__main__":
    main()
