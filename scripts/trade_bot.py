"""
Runs all live strategies (ledger.STRATEGIES) against their universes.

Order of operations each run:
  1. Build the universe (Nasdaq-100 / S&P 500 with GICS sectors).
  2. Bulk-download daily bars once, shared across strategies.
  3. Check the market regime: SPY below its SMA200 means risk-off -- exits and
     stops still run, but no new positions are opened by any strategy.
  4. Per strategy: ratchet trailing stops, process exits, then process entries
     subject to liquidity and sector-concentration limits. A strategy module
     may opt out of the trailing-stop ratchet/sell check entirely by setting
     DISABLE_TRAILING_STOP = True (e.g. hold_never, which never exits).
"""
import math
import os
import sys
import time
from datetime import datetime, timezone

import yfinance as yf

import ledger as ledger_mod
import universe as universe_mod
import telegram_notify
from strategies import STRATEGY_MODULES

SPY_TICKER = "SPY"
CHUNK_SIZE = 100    # tickers per yfinance request
HISTORY = "1y"      # enough for SMA200 plus warm-up


def _position_opened_at(trades, symbol):
    """Bildirim amaçlı: pozisyonun en son sıfırdan açıldığı ALIM tarihini, mevcut
    trade geçmişinden (salt okunur) çıkarır. Ledger'a yeni bir alan eklemez.
    Bulunamazsa None döner."""
    running_qty = 0
    episode_start = None
    for t in trades:
        if t.get("symbol") != symbol:
            continue
        if t.get("action") == "BUY":
            if running_qty == 0:
                episode_start = t.get("date")
            running_qty += t.get("qty", 0)
        elif t.get("action") == "SELL":
            running_qty -= t.get("qty", 0)
            if running_qty <= 0:
                running_qty = 0
    return episode_start


def _held_days(trades, symbol):
    opened_at = _position_opened_at(trades, symbol)
    if not opened_at:
        return None
    try:
        opened_dt = datetime.fromisoformat(opened_at)
        return (datetime.now(timezone.utc) - opened_dt).days
    except (ValueError, TypeError):
        return None


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
    """SPY above its 200-day average = risk-on. Returns (bool, human-readable note).

    SPY verisi hiç çekilemediyse (spy_df None) güvenli tarafa geçilir: yeni alım
    yapılmaz. Bu yalnızca yeni pozisyon açma kararını etkiler; mevcut pozisyonların
    Pass 1 çıkış/stop kontrolü SPY'den bağımsız olarak her koşulda çalışır.
    """
    if spy_df is None:
        return False, "SPY verisi alınamadı — rejim filtresi güvenli tarafa geçti (yeni alım yok)"
    if len(spy_df) < 200:
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

        # DISABLE_TRAILING_STOP=True (hold_never) skips both the ratchet and the
        # stop-price sell check -- same guard as backtest.py's centralized loop,
        # otherwise this would force-sell a strategy defined to never exit.
        if not getattr(module, "DISABLE_TRAILING_STOP", False):
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
                avg_price = lg["positions"][symbol]["avg_price"]
                held_days = _held_days(lg["trades"], symbol)
                if ledger_mod.sell(lg, symbol, price, reasoning, {"stop_price": round(stop, 2)}):
                    ledger_mod.save_ledger(strategy_name, lg)
                    pnl = lg["trades"][-1].get("pnl")
                    pnl_pct = (pnl / (avg_price * qty) * 100) if avg_price else None
                    telegram_notify.notify_sell(strategy_name, symbol, qty, price, reasoning,
                                                pnl=pnl, pnl_pct=pnl_pct, held_days=held_days)
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
            avg_price = lg["positions"][symbol]["avg_price"]
            held_days = _held_days(lg["trades"], symbol)
            if ledger_mod.sell(lg, symbol, price, signal["reasoning"], signal["indicators"]):
                ledger_mod.save_ledger(strategy_name, lg)
                pnl = lg["trades"][-1].get("pnl")
                pnl_pct = (pnl / (avg_price * qty) * 100) if avg_price else None
                telegram_notify.notify_sell(strategy_name, symbol, qty, price,
                                            signal["reasoning"], pnl=pnl, pnl_pct=pnl_pct,
                                            held_days=held_days)
        elif signal["action"] == "SELL_PARTIAL":
            qty = signal["qty"]
            avg_price = lg["positions"][symbol]["avg_price"]
            held_days = _held_days(lg["trades"], symbol)
            if ledger_mod.sell(lg, symbol, price, signal["reasoning"], signal["indicators"], qty=qty):
                if symbol in lg["positions"]:
                    lg["positions"][symbol]["partial_taken"] = True
                ledger_mod.save_ledger(strategy_name, lg)
                pnl = lg["trades"][-1].get("pnl")
                pnl_pct = (pnl / (avg_price * qty) * 100) if avg_price else None
                telegram_notify.notify_sell(strategy_name, symbol, qty, price,
                                            signal["reasoning"], pnl=pnl, pnl_pct=pnl_pct,
                                            held_days=held_days)

    # --- Pass 2: new entries (skipped entirely when the market regime is risk-off) ---
    if risk_on:
        # Pass 2a: evaluate every non-held candidate once, collect BUY signals with score.
        buy_signals = []
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

            buy_signals.append({"symbol": symbol, "signal": signal, "score": signal.get("score")})

        # Pass 2b: strongest signal first, so a cash-starved late entry loses to a
        # stronger candidate rather than to alphabetical position.
        for candidate in ledger_mod.rank_buy_candidates(buy_signals):
            symbol = candidate["symbol"]
            signal = candidate["signal"]
            price = signal["price"]
            stop_price = signal["stop_price"]

            current_prices[symbol] = price
            sector = sector_map.get(symbol, "Unknown")

            sizing_base = getattr(module, "SIZING_BASE", "cash")
            qty, cost = ledger_mod.plan_position(lg, price, stop_price,
                                                  sizing_base=sizing_base, prices=current_prices)
            if qty <= 0:
                print(f"[skip] {strategy_name} {symbol}: pozisyon büyüklüğü sıfır (qty<=0)")
                continue

            max_names_per_sector = getattr(module, "MAX_NAMES_PER_SECTOR", ledger_mod.MAX_NAMES_PER_SECTOR)
            allowed, reason = ledger_mod.sector_allows_entry(lg, current_prices, sector, cost,
                                                               max_names_per_sector=max_names_per_sector)
            if not allowed:
                print(f"[skip] {strategy_name} {symbol}: {reason}")
                continue

            if ledger_mod.buy(lg, symbol, price, signal["stop_price"], signal["reasoning"],
                              signal["indicators"], sector=sector,
                              sizing_base=sizing_base, prices=current_prices):
                ledger_mod.save_ledger(strategy_name, lg)
                telegram_notify.notify_buy(strategy_name, symbol,
                                           lg["positions"][symbol]["qty"], price,
                                           signal["stop_price"], signal["reasoning"])
            else:
                print(f"[skip] {strategy_name} {symbol}: yetersiz nakit (cost={cost:.2f}, cash={lg['cash']:.2f})")
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

        # Yeni eklenen bir stratejinin (ör. hold_never) ilk ledger dosyası henüz
        # yoksa burada $10.000 ile üretilir. Dosyası zaten var olan stratejiler
        # için bu blok hiç tetiklenmez -- onların hata/okuma yolu değişmedi.
        if not os.path.exists(ledger_mod.ledger_path(name)):
            ledger_mod.init_ledger(name)
            print(f"[init] {name}: ilk ledger ${ledger_mod.DEFAULT_STARTING_CASH:,.2f} ile oluşturuldu")

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
        print("[warn] SPY verisi yok — yeni alım devre dışı, Pass 1 (stop/trailing) her strateji için normal çalışacak")

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
        gr.build_dashboard(ledgers, prices)
        print("[ok] dashboard yenilendi")
    except Exception as e:
        print(f"[warn] dashboard yenilenemedi: {e}")


if __name__ == "__main__":
    main()
