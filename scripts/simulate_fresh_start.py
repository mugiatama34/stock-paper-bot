"""
Bir stratejinin, bugün $10.000 sermaye ile sıfırdan başlasaydı hangi hisselerden
kaç adet alacağını hesaplar ve raporlar.

Salt okunur simülasyon: hiçbir ledger dosyasına yazmaz, hiçbir işlem yapmaz,
Telegram bildirimi göndermez. trade_bot.run_strategy'nin Pass 2 (yeni giriş)
mantığını, boş bir ledger üzerinde bellek-içi kopyasıyla uygular -- pozisyon
boyutlandırma, sektör limiti, rejim filtresi ve giriş kuralları canlı bottakiyle
aynı fonksiyonlardan (ledger.py, universe.py, strategies/*, trade_bot.py) çağrılır,
yeniden yazılmaz.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

import ledger as ledger_mod
import universe as universe_mod
from strategies import STRATEGY_MODULES
from trade_bot import SPY_TICKER, fetch_bulk, market_is_risk_on

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")
STARTING_CASH = 10_000.0


def simulate(strategy_name: str) -> dict:
    if strategy_name not in ledger_mod.STRATEGIES:
        raise ValueError(
            f"Bilinmeyen strateji: {strategy_name!r}. Geçerli: {ledger_mod.STRATEGIES}"
        )

    module = STRATEGY_MODULES[strategy_name]
    universes = universe_mod.build_universe()
    sector_map = universes[module.UNIVERSE_KEY]

    needed = set(sector_map.keys()) | {SPY_TICKER}
    price_data = fetch_bulk(needed)
    spy_df = price_data.get(SPY_TICKER)

    risk_on, regime_note = market_is_risk_on(spy_df)
    print(f"[regime] {regime_note}")

    # Boş, dosyaya asla yazılmayacak sahte ledger. buy()/sell()/save_ledger()
    # hiç çağrılmaz; cash ve positions yalnızca sıradaki sembolün sektör/nakit
    # hesaplaması doğru taban üzerinden ilerlesin diye bellek-içi güncellenir.
    fake_ledger = {"cash": STARTING_CASH, "positions": {}, "trades": [], "equity_history": []}

    buys = []
    rejected = []

    if not risk_on:
        print(f"[regime] {strategy_name}: rejim risk-off, yeni alım simüle edilmiyor — {regime_note}")
    else:
        # Evaluate every candidate once, collect BUY signals with score.
        buy_signals = []
        for symbol in sorted(sector_map.keys()):
            df = price_data.get(symbol)
            if df is None or df.empty:
                rejected.append({"symbol": symbol, "reason": "fiyat verisi alınamadı"})
                continue
            if not universe_mod.passes_liquidity(df):
                rejected.append({"symbol": symbol, "reason": "likidite filtresini geçemedi (20g ort. hacim)"})
                continue

            try:
                signal = module.evaluate(symbol, df, spy_df, False, None)
            except Exception as e:
                rejected.append({"symbol": symbol, "reason": f"evaluate() hata verdi: {e}"})
                continue
            if not signal or signal["action"] != "BUY":
                continue

            price = signal["price"]
            stop_price = signal["stop_price"]
            if price != price or stop_price != stop_price:  # NaN kontrolü
                rejected.append({"symbol": symbol, "reason": "price/stop_price NaN"})
                continue

            buy_signals.append({"symbol": symbol, "signal": signal, "score": signal.get("score")})

        # Strongest signal first, so a cash-starved late entry loses to a stronger
        # candidate rather than to alphabetical position.
        for candidate in ledger_mod.rank_buy_candidates(buy_signals):
            symbol = candidate["symbol"]
            signal = candidate["signal"]
            price = signal["price"]
            stop_price = signal["stop_price"]

            sector = sector_map.get(symbol, "Unknown")
            current_prices = {s: p["avg_price"] for s, p in fake_ledger["positions"].items()}

            sizing_base = getattr(module, "SIZING_BASE", "cash")
            qty, cost = ledger_mod.plan_position(fake_ledger, price, stop_price,
                                                  sizing_base=sizing_base, prices=current_prices)
            if qty <= 0:
                rejected.append({"symbol": symbol, "reason": "yetersiz nakit / pozisyon büyüklüğü sıfır"})
                continue

            max_names_per_sector = getattr(module, "MAX_NAMES_PER_SECTOR",
                                            ledger_mod.MAX_NAMES_PER_SECTOR)
            allowed, reason = ledger_mod.sector_allows_entry(
                fake_ledger, current_prices, sector, cost,
                max_names_per_sector=max_names_per_sector,
            )
            if not allowed:
                rejected.append({"symbol": symbol, "reason": reason})
                continue

            fake_ledger["cash"] -= cost
            fake_ledger["positions"][symbol] = {
                "qty": qty, "avg_price": price, "sector": sector,
            }

            buys.append({
                "symbol": symbol,
                "qty": qty,
                "price": round(price, 2),
                "cost": round(cost, 2),
                "stop_price": round(stop_price, 2),
                "sector": sector,
                "reasoning": signal["reasoning"],
                "indicators": signal["indicators"],
            })

    return {
        "strategy": strategy_name,
        "date": datetime.now(timezone.utc).date().isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "regime": regime_note,
        "risk_on": risk_on,
        "starting_cash": STARTING_CASH,
        "buys": buys,
        "rejected": rejected,
        "remaining_cash": round(fake_ledger["cash"], 2),
    }


def print_report(result: dict) -> None:
    print(f"\n=== Sıfırdan başlangıç simülasyonu: {result['strategy']} ({result['date']}) ===")
    print(f"Rejim: {result['regime']}")
    print(f"Başlangıç sermayesi: ${result['starting_cash']:,.2f}\n")

    if result["buys"]:
        print(f"Alınacak pozisyonlar ({len(result['buys'])}):")
        for b in result["buys"]:
            print(f"  {b['symbol']:6s} {b['qty']:5d} adet @ ${b['price']:.2f}  "
                  f"(tutar ${b['cost']:.2f}, stop ${b['stop_price']:.2f}, sektör: {b['sector']})")
            print(f"         gerekçe: {b['reasoning']}")
    else:
        print("Alınacak pozisyon yok.")

    if result["rejected"]:
        print(f"\nAlınamayan sinyaller ({len(result['rejected'])}):")
        for r in result["rejected"]:
            print(f"  {r['symbol']:6s} -- {r['reason']}")

    print(f"\nKalan nakit: ${result['remaining_cash']:,.2f}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", default="ai_momentum",
                         choices=ledger_mod.STRATEGIES,
                         help="Simüle edilecek strateji (varsayılan: ai_momentum)")
    args = parser.parse_args()

    result = simulate(args.strategy)
    print_report(result)

    os.makedirs(REPORTS_DIR, exist_ok=True)
    out_path = os.path.join(REPORTS_DIR, f"fresh_start_{args.strategy}_{result['date']}.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\n[ok] rapor yazıldı: {out_path}")


if __name__ == "__main__":
    sys.exit(main())
