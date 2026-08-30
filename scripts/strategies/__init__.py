from . import (
    ai_momentum, mean_reversion, balanced,
    mr_bollinger, mr_zscore, trend_donchian,
)

STRATEGY_MODULES = {
    "ai_momentum": ai_momentum,
    "mean_reversion": mean_reversion,
    "balanced": balanced,
    "mr_bollinger": mr_bollinger,
    "mr_zscore": mr_zscore,
    "trend_donchian": trend_donchian,
}

# Yalnızca backtest.py üzerinden çalıştırılır. Canlı bot (trade_bot.py) ve
# raporlama (generate_report.py) ledger.STRATEGIES sabit listesini kullanır
# (bkz. ledger.py) ve bu isimleri hiç görmez -- bilerek buraya bağlanmıyor.
BACKTEST_ONLY = {"mr_bollinger", "mr_zscore", "trend_donchian"}
