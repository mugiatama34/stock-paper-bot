"""
Runs all three strategies against their universes.

Order of operations each run:
  1. Build the universe (Nasdaq-100 / S&P 500 with GICS sectors).
  2. Bulk-download daily bars once, shared across strategies.
  3. Check the market regime: SPY below its SMA200 means risk-off -- exits and
     stops still run, but no new positions are opened by any strategy.
  4. Per strategy: ratchet trailing stops, process exits, then process entries
     subject to liquidity and sector-concentration limits.
"""
import math
import sys
import time

import yfinance as yf

import ledger as ledger_mod
import universe as universe_mod
import telegram_notify
from strategies import STRATEGY_MODULES

SPY_TICKER = "SPY"
CHUNK_SIZE = 100    # tickers per yfinance request
HISTORY = "1y"      # enough for SMA200 plus warm-up


def fetch_bulk(symbols):
    """{symbol: DataFrame} of daily bars, downloaded in chunks."""
    data = {}
    symbols = sorted(set(symbols))
    for i in range(0, len(symbols), CHUNK_SIZE):
        chunk = symbols[i:i + CHUNK_SIZE]
        try:
            raw = yf.download(chunk, period=HISTORY, group_by="ticker",
                              auto_adjust=True, threads=True, progress=False)
        except Exception as e:
            print(f"[warn] chunk download failed: {e}")
            continue

        for sym in chunk:
            try:
                df = raw[sym] if len(chunk) > 1 else raw
                df = df.dropna(how="all")
                if not df.empty and "Close" in df:
                    data[sym] = df
            except Exception:
                pass
        time.sleep(1)
    print(f"[data] fetched {len(data)}/{len(symbols)} symbols")
    return data


def market_is_risk_on(spy_df) -> tuple:
    """SPY above its 200-day average = risk-on. Returns (bool, human-readable note)."""
    if spy_df is None or len(spy_df) < 200:
        return True, "SPY verisi yetersiz, rejim filtresi atlandı"
    close = spy_df["Close"]
    sma200 = float(close.rolling(200).mean().iloc[-1])
    price = float(close.iloc[-1])
    if price >= sma200:
        return True, f"SPY {price:.2f} >= SMA200 {sma200:.2f} (risk-on)"
    return False, f"SPY {price:.2f} < SMA200 {sma200:.2f} (risk-off — yeni alım yok)"


def run_strategy(strategy_name, price_data, spy_df, universes, risk_on, regime_note):
    module = STRATEGY_MODULES[strategy_name]
    lg = ledger_mod.load_ledger(strategy_name)
    sector_map = universes[module.UNIVERSE_KEY]

    candidates = set(sector_map.keys()) | set(lg["positions"].keys())
    current_prices = {}

    # Positions opened before trailing stops / sector caps existed lack these fields.
    # Without backfilling, they'd all bucket into "Unknown" and jam the sector limit.
    for sym, pos in lg["positions"].items():
        pos.setdefault("partial_taken", False)
        pos.setdefault("peak_price", pos["avg_price"])
        if pos.get("sector", "Unknown") == "Unknown" and sym in sector_map:
            pos["sector"] = sector_map[sym]

    # --- Pass 1: manage what we already hold (always runs, regardless of regime) ---
    for symbol in list(lg["positions"].keys()):
        df = price_data.get(symbol)
        if df is None or df.empty:
            continue
        price = float(df["Close"].iloc[-1])
        current_prices[symbol] = price

        from strategies.indicators import atr as atr_fn
        try:
            atr_val = float(atr_fn(df, 14).iloc[-1])
        except Exception:
            atr_val = 0.0

        ledger_mod.update_trailing_stop(lg, symbol, price, atr_val,
                                        atr_mult=getattr(module, "ATR_MULT", 2.0))

        stop = lg["positions"][symbol].get("stop_price")
        if stop and price <= stop:
            reasoning = f"İzleyen stop tetiklendi ({stop:.2f}), zirveden geri çekilme."
            qty = lg["positions"][symbol]["qty"]
            if ledger_mod.sell(lg, symbol, price, reasoning, {"stop_price": round(stop, 2)}):
                ledger_mod.save_ledger(strategy_name, lg)
                telegram_notify.notify_trade(strategy_name, "SELL", symbol, qty, price, reasoning)
            continue

        try:
            signal = module.evaluate(symbol, df, spy_df, True, lg["positions"][symbol])
        except Exception as e:
            print(f"[warn] {strategy_name} {symbol}: evaluate() Pass 1'de hata verdi, atlanıyor: {e}")
            continue
        if not signal:
            continue

        if signal["action"] == "SELL":
            qty = lg["positions"][symbol]["qty"]
            if ledger_mod.sell(lg, symbol, price, signal["reasoning"], signal["indicators"]):
                ledger_mod.save_ledger(strategy_name, lg)
                telegram_notify.notify_trade(strategy_name, "SELL", symbol, qty, price,
                                             signal["reasoning"])
        elif signal["action"] == "SELL_PARTIAL":
            qty = signal["qty"]
            if ledger_mod.sell(lg, symbol, price, signal["reasoning"], signal["indicators"], qty=qty):
                if symbol in lg["positions"]:
                    lg["positions"][symbol]["partial_taken"] = True
                ledger_mod.save_ledger(strategy_name, lg)
                telegram_notify.notify_trade(strategy_name, "SELL", symbol, qty, price,
                                             signal["reasoning"])

    # --- Pass 2: new entries (skipped entirely when the market regime is risk-off) ---
    if risk_on:
        for symbol in sorted(candidates):
            if symbol in lg["positions"]:
                continue
            df = price_data.get(symbol)
            if df is None or df.empty:
                continue
            if not universe_mod.passes_liquidity(df):
                continue

            try:
                signal = module.evaluate(symbol, df, spy_df, False, None)
            except Exception as e:
                print(f"[warn] {strategy_name} {symbol}: evaluate() Pass 2'de hata verdi, atlanıyor: {e}")
                continue
            if not signal or signal["action"] != "BUY":
                continue

            price = signal["price"]
            stop_price = signal["stop_price"]
            if math.isnan(price) or math.isnan(stop_price):
                print(f"[skip] {strategy_name} {symbol}: price/stop_price NaN, pozisyon açılmıyor")
                continue

            current_prices[symbol] = price
            sector = sector_map.get(symbol, "Unknown")

            qty, cost = ledger_mod.plan_position(lg, price, stop_price)
            if qty <= 0:
                continue

            allowed, reason = ledger_mod.sector_allows_entry(lg, current_prices, sector, cost)
            if not allowed:
                print(f"[skip] {strategy_name} {symbol}: {reason}")
                continue

            if ledger_mod.buy(lg, symbol, price, signal["stop_price"], signal["reasoning"],
                              signal["indicators"], sector=sector):
                ledger_mod.save_ledger(strategy_name, lg)
                telegram_notify.notify_trade(strategy_name, "BUY", symbol,
                                             lg["positions"][symbol]["qty"], price,
                                             f"{signal['reasoning']} [{sector}]")
    else:
        print(f"[regime] {strategy_name}: yeni alım yok — {regime_note}")

    # Value the book with fresh prices where available
    for symbol in lg["positions"]:
        if symbol not in current_prices:
            df = price_data.get(symbol)
            if df is not None and not df.empty:
                current_prices[symbol] = float(df["Close"].iloc[-1])

    ledger_mod.record_equity_snapshot(lg, current_prices)
    ledger_mod.save_ledger(strategy_name, lg)
    print(f"[ok] {strategy_name}: equity={ledger_mod.total_equity(lg, current_prices):.2f}, "
          f"{len(lg['positions'])} pozisyon")


def main():
    strategies = sys.argv[1:] or ledger_mod.STRATEGIES
    universes = universe_mod.build_universe()

    needed = {SPY_TICKER}
    healthy_strategies = []
    for name in strategies:
        module = STRATEGY_MODULES[name]
        needed |= set(universes[module.UNIVERSE_KEY].keys())
        try:
            lg = ledger_mod.load_ledger(name)
        except ledger_mod.LedgerError as e:
            print(f"[error] {name}: ledger yüklenemedi, bu turda atlanıyor: {e}")
            continue
        needed |= set(lg["positions"].keys())
        healthy_strategies.append(name)

    price_data = fetch_bulk(needed)
    spy_df = price_data.get(SPY_TICKER)

    risk_on, regime_note = market_is_risk_on(spy_df)
    print(f"[regime] {regime_note}")
    if spy_df is None:
        print("[error] SPY verisi yok, çıkılıyor")
        return

    for name in healthy_strategies:
        try:
            run_strategy(name, price_data, spy_df, universes, risk_on, regime_note)
        except Exception as e:
            print(f"[error] {name}: run_strategy başarısız oldu, diğer stratejilere devam ediliyor: {e}")

    # Refresh the dashboard with the post-trade book, using the prices we already
    # have in hand, so holdings appear without waiting for the next report run.
    try:
        import generate_report as gr
        ledgers = {n: ledger_mod.load_ledger(n) for n in ledger_mod.STRATEGIES}
        held = gr.held_symbols(ledgers)
        prices = {s: float(price_data[s]["Close"].iloc[-1])
                  for s in held if s in price_data and not price_data[s].empty}
        gr.build_dashboard_html(ledgers, prices)
        print("[ok] dashboard yenilendi")
    except Exception as e:
        print(f"[warn] dashboard yenilenemedi: {e}")


if __name__ == "__main__":
    main()
