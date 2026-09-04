from . import (
    ai_momentum, mean_reversion, balanced,
    mr_bollinger, mr_zscore, trend_donchian, ai_infra, ai_hold,
    hold_wide, hold_sma200, hold_never, ndx_equity_sizing,
)

STRATEGY_MODULES = {
    "ai_momentum": ai_momentum,
    "mean_reversion": mean_reversion,
    "balanced": balanced,
    "mr_bollinger": mr_bollinger,
    "mr_zscore": mr_zscore,
    "trend_donchian": trend_donchian,
    "ai_infra": ai_infra,
    "ai_hold": ai_hold,
    "hold_wide": hold_wide,
    "hold_sma200": hold_sma200,
    "hold_never": hold_never,
    "ndx_equity_sizing": ndx_equity_sizing,
}

# Yalnızca backtest.py üzerinden çalıştırılır. Canlı bot (trade_bot.py) ve
# raporlama (generate_report.py) ledger.STRATEGIES sabit listesini kullanır
# (bkz. ledger.py) ve bu isimleri hiç görmez -- bilerek buraya bağlanmıyor.
#
# hold_never burada YOK: ledger.STRATEGIES'e eklendi, artık trade_bot.py
# üzerinden gerçek sermayeyle de çalışıyor (bkz. strategies/hold_never.py
# başlığı ve trade_bot.py'deki DISABLE_TRAILING_STOP guard'ı).
BACKTEST_ONLY = {
    "mr_bollinger", "mr_zscore", "trend_donchian", "ai_infra", "ai_hold",
    "hold_wide", "hold_sma200", "ndx_equity_sizing",
}
