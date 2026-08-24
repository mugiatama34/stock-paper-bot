"""
Shared portfolio ledger utilities.
Each strategy owns one JSON file under /data holding its virtual $10,000 account:
cash, open positions, full trade history (with reasoning), and equity snapshots.
"""
import json
import os
from datetime import datetime, timezone

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

STRATEGIES = ["ai_momentum", "mean_reversion", "balanced"]

RISK_PER_TRADE = 0.02  # fraction of strategy's total equity risked per trade (stop-loss distance)


def ledger_path(strategy: str) -> str:
    return os.path.join(DATA_DIR, f"portfolio_{strategy}.json")


def load_ledger(strategy: str) -> dict:
    with open(ledger_path(strategy), "r") as f:
        return json.load(f)


def save_ledger(strategy: str, ledger: dict) -> None:
    with open(ledger_path(strategy), "w") as f:
        json.dump(ledger, f, indent=2)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def position_value(ledger: dict, prices: dict) -> float:
    total = 0.0
    for symbol, pos in ledger["positions"].items():
        price = prices.get(symbol, pos["avg_price"])
        total += pos["qty"] * price
    return total


def total_equity(ledger: dict, prices: dict) -> float:
    return ledger["cash"] + position_value(ledger, prices)


def buy(ledger: dict, symbol: str, price: float, stop_price: float, reasoning: str, indicators: dict) -> bool:
    """Size position by risk: risk_amount = equity * RISK_PER_TRADE, qty = risk_amount / (price - stop_price)."""
    equity_now = ledger["cash"]  # conservative: size off available cash, not full equity
    risk_amount = equity_now * RISK_PER_TRADE
    per_share_risk = max(price - stop_price, price * 0.01)  # avoid div by ~0
    qty = int(risk_amount / per_share_risk)
    # cap position at 25% of cash so one name can't dominate the book
    max_qty_by_cash = int((ledger["cash"] * 0.25) / price)
    qty = max(0, min(qty, max_qty_by_cash))
    cost = qty * price
    if qty <= 0 or cost > ledger["cash"]:
        return False

    ledger["cash"] -= cost
    if symbol in ledger["positions"]:
        pos = ledger["positions"][symbol]
        new_qty = pos["qty"] + qty
        pos["avg_price"] = (pos["avg_price"] * pos["qty"] + cost) / new_qty
        pos["qty"] = new_qty
        pos["stop_price"] = stop_price
    else:
        ledger["positions"][symbol] = {"qty": qty, "avg_price": price, "stop_price": stop_price}

    ledger["trades"].append({
        "date": now_iso(),
        "symbol": symbol,
        "action": "BUY",
        "qty": qty,
        "price": round(price, 2),
        "stop_price": round(stop_price, 2),
        "reasoning": reasoning,
        "indicators": indicators,
    })
    return True


def sell(ledger: dict, symbol: str, price: float, reasoning: str, indicators: dict, qty: int = None) -> bool:
    if symbol not in ledger["positions"]:
        return False
    pos = ledger["positions"][symbol]
    sell_qty = pos["qty"] if qty is None else min(qty, pos["qty"])
    if sell_qty <= 0:
        return False

    proceeds = sell_qty * price
    pnl = (price - pos["avg_price"]) * sell_qty
    ledger["cash"] += proceeds
    pos["qty"] -= sell_qty
    if pos["qty"] <= 0:
        del ledger["positions"][symbol]

    ledger["trades"].append({
        "date": now_iso(),
        "symbol": symbol,
        "action": "SELL",
        "qty": sell_qty,
        "price": round(price, 2),
        "pnl": round(pnl, 2),
        "reasoning": reasoning,
        "indicators": indicators,
    })
    return True


def record_equity_snapshot(ledger: dict, prices: dict) -> None:
    ledger["equity_history"].append({
        "date": now_iso(),
        "equity": round(total_equity(ledger, prices), 2),
        "cash": round(ledger["cash"], 2),
    })
