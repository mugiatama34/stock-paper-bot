"""
Shared portfolio ledger utilities.
Each strategy owns one JSON file under /data holding its virtual $10,000 account:
cash, open positions, full trade history (with reasoning), and equity snapshots.

Positions carry a trailing stop: `peak_price` ratchets up as the trade works, and
`stop_price` follows it, so a winner that reverses gives back a bounded amount
instead of round-tripping all the way to a moving-average exit.
"""
import json
import math
import os
import tempfile
from datetime import datetime, timezone

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

STRATEGIES = ["ai_momentum", "mean_reversion", "balanced", "hold_never"]

# Kullanıcıya gösterilen strateji adları. Strateji anahtarı (ör. "ai_momentum")
# kod içinde, dosya adlarında ve ledger'da değişmez; yalnızca burası dashboard
# ve Telegram bildirimlerinde görünen etiketi belirler.
STRATEGY_LABELS = {
    "ai_momentum": "Nasdaq-100 Momentum",
    "mean_reversion": "Mean Reversion",
    "balanced": "Balanced",
    "hold_never": "Momentum — Hiç Satma",
}

# hold_never gibi sonradan eklenen bir stratejinin ilk ledger dosyası henüz
# yoksa, canlı akış (trade_bot.main) bunu bu başlangıç sermayesiyle üretir.
# Halihazırda dosyası olan stratejiler bu yoldan hiç geçmez -- bkz. init_ledger.
DEFAULT_STARTING_CASH = 10_000.0

RISK_PER_TRADE = 0.02      # equity fraction risked per trade (via stop distance)
MAX_POSITION_PCT = 0.25    # one name can't exceed this share of cash at entry
MAX_NAMES_PER_SECTOR = 2   # concentration cap: count of open names in one sector
MAX_SECTOR_PCT = 0.30      # concentration cap: share of equity in one sector

# Strateji modülleri isteğe bağlı olarak SIZING_BASE = "cash" (varsayılan, mevcut
# davranış) veya SIZING_BASE = "equity" tanımlayabilir. plan_position()/buy() bu
# değere göre RISK_PER_TRADE ve MAX_POSITION_PCT'yi kalan nakit yerine toplam
# equity (cash + açık pozisyonların piyasa değeri) üzerinden hesaplar. Formülün
# kendisi değişmez, yalnızca tabanı değişir; nakit yetersizse buy() zaten reddeder.
#
# Aynı şekilde strateji modülleri isteğe bağlı olarak MAX_NAMES_PER_SECTOR
# tanımlayarak sektör başına izin verilen açık pozisyon sayısını override
# edebilir; tanımlanmazsa aşağıdaki varsayılan (2) kullanılır.


class LedgerError(Exception):
    """Ledger dosyası okunamadı veya bozuk -- boş ledger'a sessizce düşülmez."""


def ledger_path(strategy: str) -> str:
    return os.path.join(DATA_DIR, f"portfolio_{strategy}.json")


def load_ledger(strategy: str) -> dict:
    path = ledger_path(strategy)
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        raise LedgerError(f"Ledger dosyası bulunamadı: {path}") from None
    except json.JSONDecodeError as e:
        raise LedgerError(f"Ledger dosyası bozuk (JSON parse hatası): {path} ({e})") from e


def init_ledger(strategy: str, starting_cash: float = DEFAULT_STARTING_CASH) -> dict:
    """Yeni eklenen bir stratejinin ilk ledger'ını starting_cash ile diske yazar.

    Yalnızca dosya hiç yokken çağrılmalı (bkz. trade_bot.main) -- var olan bir
    ledger'ı asla ezmez, bozuk bir dosyayı da asla sessizce sıfırlamaz; onun
    hata yolu load_ledger üzerinden LedgerError olarak kalır."""
    fresh = {
        "strategy": strategy,
        "starting_cash": starting_cash,
        "cash": starting_cash,
        "positions": {},
        "trades": [],
        "equity_history": [],
    }
    save_ledger(strategy, fresh)
    return fresh


def save_ledger(strategy: str, ledger: dict) -> None:
    """Atomik yazma: geçici dosyaya yazıp fsync sonrası yerine taşır, böylece
    yazma sırasında kesilen bir işlem portföy dosyasını yarım bırakmaz."""
    path = ledger_path(strategy)
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(path), prefix=f".{os.path.basename(path)}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(ledger, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


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


def sector_allows_entry(ledger: dict, prices: dict, sector: str, intended_cost: float,
                         max_names_per_sector: int = None) -> tuple:
    """
    Returns (allowed: bool, reason: str). Blocks an entry that would push a sector
    past either the name-count cap or the equity-share cap.

    max_names_per_sector=None (default): uses module-level MAX_NAMES_PER_SECTOR (2) --
    unchanged behavior. Pass a strategy's own MAX_NAMES_PER_SECTOR override to use that
    instead, same pattern as SIZING_BASE above.
    """
    names_cap = MAX_NAMES_PER_SECTOR if max_names_per_sector is None else max_names_per_sector
    exposure = sector_exposure(ledger, prices).get(sector, {"names": 0, "value": 0.0})
    if exposure["names"] >= names_cap:
        return False, f"{sector} sektöründe zaten {exposure['names']} pozisyon var (limit {names_cap})"

    equity = total_equity(ledger, prices)
    if equity > 0 and (exposure["value"] + intended_cost) / equity > MAX_SECTOR_PCT:
        pct = (exposure["value"] + intended_cost) / equity * 100
        return False, f"{sector} sektörü portföyün %{pct:.0f}'ini geçerdi (limit %{MAX_SECTOR_PCT*100:.0f})"

    return True, ""


def rank_buy_candidates(candidates: list) -> list:
    """
    Sorts BUY adaylarını sinyal skoruna göre güçlüden zayıfa sıralar. Her öge en az
    {'symbol': str, 'score': float|None} içermeli.

    Skor tanımlamayan (score=None) adaylar sona düşer ve kendi aralarında alfabetik
    kalır -- bir strateji hiç skor döndürmüyorsa (tüm adaylarda score=None) bu,
    eskisiyle birebir aynı alfabetik sıralamaya denk gelir (geriye dönük uyumluluk).
    """
    return sorted(
        candidates,
        key=lambda c: (0, -c["score"]) if c.get("score") is not None else (1, c["symbol"]),
    )


def plan_position(ledger: dict, price: float, stop_price: float,
                   sizing_base: str = "cash", prices: dict = None) -> tuple:
    """
    (qty, cost) this trade would take, using the same sizing rule as buy().
    Split out so sector-concentration checks can run before anything is committed.

    sizing_base="cash" (default): risk_amount and the position cap are a fraction
    of remaining cash -- unchanged behavior. sizing_base="equity": the same
    formula runs against total_equity(ledger, prices) instead, so the base
    doesn't shrink as cash gets committed to earlier positions in the same run.
    """
    if price is None or (isinstance(price, float) and math.isnan(price)) or price <= 0:
        print(f"[skip] plan_position: geçersiz price ({price!r}), pozisyon planlanmadı")
        return 0, 0.0

    base = total_equity(ledger, prices or {}) if sizing_base == "equity" else ledger["cash"]
    risk_amount = base * RISK_PER_TRADE
    per_share_risk = max(price - stop_price, price * 0.01)  # guard against ~0 distance
    qty = int(risk_amount / per_share_risk)
    max_qty_by_base = int((base * MAX_POSITION_PCT) / price)
    qty = max(0, min(qty, max_qty_by_base))
    return qty, qty * price


def buy(ledger: dict, symbol: str, price: float, stop_price: float, reasoning: str,
        indicators: dict, sector: str = "Unknown", sizing_base: str = "cash",
        prices: dict = None) -> bool:
    """Size by risk: risk_amount = base * RISK_PER_TRADE, qty = risk_amount / stop distance."""
    qty, cost = plan_position(ledger, price, stop_price, sizing_base=sizing_base, prices=prices)
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
