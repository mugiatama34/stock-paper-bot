"""
Runs all three strategies: fetches data, evaluates entry/exit signals,
executes trades against each strategy's ledger, and sends Telegram alerts.
Intended to run periodically via GitHub Actions during US market hours.
"""
import sys
import time
import yfinance as yf

import ledger as ledger_mod
from strategies import STRATEGY_MODULES
import telegram_notify

SPY_TICKER = "SPY"


def fetch_history(symbols, period="1y"):
    data = {}
    for sym in symbols:
        try:
            df = yf.Ticker(sym).history(period=period)
            if not df.empty:
                data[sym] = df
        except Exception as e:
            print(f"[warn] failed to fetch {sym}: {e}")
        time.sleep(0.3)  # be polite to the API
    return data


def run_strategy(strategy_name: str):
    module = STRATEGY_MODULES[strategy_name]
    lg = ledger_mod.load_ledger(strategy_name)

    universe = list(set(module.UNIVERSE) | set(lg["positions"].keys()))
    price_data = fetch_history(universe + [SPY_TICKER])
    spy_df = price_data.get(SPY_TICKER)
    if spy_df is None:
        print(f"[error] no SPY data, skipping {strategy_name}")
        return

    current_prices = {}

    for symbol in universe:
        df = price_data.get(symbol)
        if df is None:
            continue
        current_prices[symbol] = float(df["Close"].iloc[-1])
        has_position = symbol in lg["positions"]

        # hard stop-loss check first, independent of strategy logic
        if has_position:
            stop = lg["positions"][symbol].get("stop_price")
            last_price = float(df["Close"].iloc[-1])
            if stop and last_price <= stop:
                reasoning = f"Stop-loss tetiklendi ({stop:.2f})."
                if ledger_mod.sell(lg, symbol, last_price, reasoning, {"stop_price": stop}):
                    telegram_notify.notify_trade(strategy_name, "SELL", symbol,
                                                  0, last_price, reasoning)
                continue

        signal = module.evaluate(symbol, df, spy_df, has_position)
        if not signal:
            continue

        if signal["action"] == "BUY" and not has_position:
            success = ledger_mod.buy(lg, symbol, signal["price"], signal["stop_price"],
                                      signal["reasoning"], signal["indicators"])
            if success:
                qty = lg["positions"][symbol]["qty"]
                telegram_notify.notify_trade(strategy_name, "BUY", symbol, qty,
                                              signal["price"], signal["reasoning"])
        elif signal["action"] == "SELL" and has_position:
            qty = lg["positions"][symbol]["qty"]
            success = ledger_mod.sell(lg, symbol, signal["price"], signal["reasoning"],
                                       signal["indicators"])
            if success:
                telegram_notify.notify_trade(strategy_name, "SELL", symbol, qty,
                                              signal["price"], signal["reasoning"])

    ledger_mod.record_equity_snapshot(lg, current_prices)
    ledger_mod.save_ledger(strategy_name, lg)
    print(f"[ok] {strategy_name}: equity={ledger_mod.total_equity(lg, current_prices):.2f}")


def main():
    strategies = sys.argv[1:] or ledger_mod.STRATEGIES
    for name in strategies:
        run_strategy(name)


if __name__ == "__main__":
    main()
