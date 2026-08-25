"""
Shared portfolio ledger utilities.
Each strategy owns one JSON file under /data holding its virtual $10,000 account:
cash, open positions, full trade history (with reasoning), and equity snapshots.

Positions carry a trailing stop: `peak_price` ratchets up as the trade works, and
`stop_price` follows it, so a winner that reverses gives back a bounded amount
instead of round-tripping all the way to a moving-average exit.
"""
import json
import os
from datetime import datetime, timezone

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

STRATEGIES = ["ai_momentum", "mean_reversion", "balanced"]

RISK_PER_TRADE = 0.02      # equity fraction risked per trade (via stop distance)
MAX_POSITION_PCT = 0.25    # one name can't exceed this share of cash at entry
MAX_NAMES_PER_SECTOR = 2   # concentration cap: count of open names in one sector
MAX_SECTOR_PCT = 0.30      # concentration cap: share of equity in one sector


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


def sector_exposure(ledger: dict, prices: dict) -> dict:
    """{sector: {'names': int, 'value': float}} across open positions."""
    out = {}
    for symbol, pos in ledger["positions"].items():
        sector = pos.get("sector", "Unknown")
        price = prices.get(symbol, pos["avg_price"])
        bucket = out.setdefault(sector, {"names": 0, "value": 0.0})
        bucket["names"] += 1
        bucket["value"] += pos["qty"] * price
    return out


def sector_allows_entry(ledger: dict, prices: dict, sector: str, intended_cost: float) -> tuple:
    """
    Returns (allowed: bool, reason: str). Blocks an entry that would push a sector
    past either the name-count cap or the equity-share cap.
    """
    exposure = sector_exposure(ledger, prices).get(sector, {"names": 0, "value": 0.0})
    if exposure["names"] >= MAX_NAMES_PER_SECTOR:
        return False, f"{sector} sektöründe zaten {exposure['names']} pozisyon var (limit {MAX_NAMES_PER_SECTOR})"

    equity = total_equity(ledger, prices)
    if equity > 0 and (exposure["value"] + intended_cost) / equity > MAX_SECTOR_PCT:
        pct = (exposure["value"] + intended_cost) / equity * 100
        return False, f"{sector} sektörü portföyün %{pct:.0f}'ini geçerdi (limit %{MAX_SECTOR_PCT*100:.0f})"

    return True, ""


def plan_position(ledger: dict, price: float, stop_price: float) -> tuple:
    """
    (qty, cost) this trade would take, using the same sizing rule as buy().
    Split out so sector-concentration checks can run before anything is committed.
    """
    risk_amount = ledger["cash"] * RISK_PER_TRADE
    per_share_risk = max(price - stop_price, price * 0.01)  # guard against ~0 distance
    qty = int(risk_amount / per_share_risk)
    max_qty_by_cash = int((ledger["cash"] * MAX_POSITION_PCT) / price)
    qty = max(0, min(qty, max_qty_by_cash))
    return qty, qty * price


def buy(ledger: dict, symbol: str, price: float, stop_price: float, reasoning: str,
        indicators: dict, sector: str = "Unknown") -> bool:
    """Size by risk: risk_amount = cash * RISK_PER_TRADE, qty = risk_amount / stop distance."""
    qty, cost = plan_position(ledger, price, stop_price)
    if qty <= 0 or cost > ledger["cash"]:
        return False

    ledger["cash"] -= cost
    if symbol in ledger["positions"]:
        pos = ledger["positions"][symbol]
        new_qty = pos["qty"] + qty
        pos["avg_price"] = (pos["avg_price"] * pos["qty"] + cost) / new_qty
        pos["qty"] = new_qty
        pos["stop_price"] = stop_price
        pos["peak_price"] = max(pos.get("peak_price", price), price)
    else:
        ledger["positions"][symbol] = {
            "qty": qty,
            "avg_price": price,
            "stop_price": stop_price,
            "peak_price": price,
            "sector": sector,
            "partial_taken": False,
        }

    ledger["trades"].append({
        "date": now_iso(),
        "symbol": symbol,
        "action": "BUY",
        "qty": qty,
        "price": round(price, 2),
        "stop_price": round(stop_price, 2),
        "sector": sector,
        "reasoning": reasoning,
        "indicators": indicators,
    })
    return True


def sell(ledger: dict, symbol: str, price: float, reasoning: str, indicators: dict,
         qty: int = None) -> bool:
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
    partial = pos["qty"] > 0
    sector = pos.get("sector", "Unknown")
    if not partial:
        del ledger["positions"][symbol]

    ledger["trades"].append({
        "date": now_iso(),
        "symbol": symbol,
        "action": "SELL",
        "qty": sell_qty,
        "price": round(price, 2),
        "pnl": round(pnl, 2),
        "partial": partial,
        "sector": sector,
        "reasoning": reasoning,
        "indicators": indicators,
    })
    return True


def update_trailing_stop(ledger: dict, symbol: str, price: float, atr: float,
                         atr_mult: float = 2.0) -> bool:
    """
    Ratchet the stop up behind a rising position. Never lowers an existing stop --
    that's what makes it a trailing stop rather than a moving target.
    Returns True if the stop was raised.
    """
    pos = ledger["positions"].get(symbol)
    if not pos or not atr or atr <= 0:
        return False

    pos["peak_price"] = max(pos.get("peak_price", price), price)
    candidate = pos["peak_price"] - atr_mult * atr
    if candidate > pos.get("stop_price", 0):
        pos["stop_price"] = candidate
        return True
    return False


def record_equity_snapshot(ledger: dict, prices: dict) -> None:
    ledger["equity_history"].append({
        "date": now_iso(),
        "equity": round(total_equity(ledger, prices), 2),
        "cash": round(ledger["cash"], 2),
    })
