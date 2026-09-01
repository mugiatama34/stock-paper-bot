"""
Builds the tradable universe for each strategy, with GICS sector labels.

Sources, in order of preference:
  - S&P 500: a versioned CSV on GitHub that carries the GICS Sector column.
  - Nasdaq-100: Wikipedia at runtime; falls back to a baked-in list if that fails,
    so a blocked request or a table-layout change can never break the bot.

Sector labels matter because trade_bot enforces concentration limits (max N names
and max X% of equity per sector) — without them the momentum book would happily
go 100% semiconductors.
"""
import io
import json
import os

import pandas as pd
import requests
import yfinance as yf

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CACHE_PATH = os.path.join(DATA_DIR, "universe_cache.json")

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; stock-paper-bot/1.0)"}

SP500_CSV = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
NDX_WIKI = "https://en.wikipedia.org/wiki/Nasdaq-100"

MIN_AVG_VOLUME = 2_000_000  # 20-day average shares/day — user-set liquidity floor

# Fallback only. Index membership churns a few names a year; a stale entry just
# means one ticker yfinance can't price, which is handled gracefully upstream.
NDX_FALLBACK = [
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "NVDA", "AVGO", "NFLX",
    "COST", "PEP", "ADBE", "AMD", "CSCO", "TMUS", "INTU", "QCOM", "TXN", "AMGN",
    "ISRG", "CMCSA", "HON", "AMAT", "BKNG", "VRTX", "PANW", "ADP", "GILD", "SBUX",
    "MU", "ADI", "LRCX", "REGN", "MDLZ", "KLAC", "SNPS", "CDNS", "MELI", "CRWD",
    "MAR", "ORLY", "CTAS", "CEG", "PYPL", "ABNB", "FTNT", "DASH", "WDAY", "NXPI",
    "TTD", "ROP", "MRVL", "CPRT", "MNST", "ADSK", "PCAR", "PAYX", "AEP", "ODFL",
    "ROST", "KDP", "FAST", "CHTR", "EA", "DDOG", "VRSK", "EXC", "CSGP", "XEL",
    "CTSH", "IDXX", "TEAM", "ANSS", "ZS", "ON", "DXCM", "CDW", "BIIB", "GFS",
    "MCHP", "CSX", "FANG", "MRNA", "WBD", "ILMN", "SIRI", "LULU", "PDD", "ARM",
    "PLTR", "APP", "MSTR", "AXON", "GEHC", "KHC", "BKR", "SMCI", "TTWO", "MPWR",
]

# Sectors for Nasdaq-100 names that aren't in the S&P 500 CSV (foreign issuers etc.)
NDX_ONLY_SECTORS = {
    "MELI": "Consumer Discretionary",
    "PDD": "Consumer Discretionary",
    "ARM": "Information Technology",
    "GFS": "Information Technology",
    "MSTR": "Information Technology",
    "APP": "Information Technology",
    "GOOG": "Communication Services",
    "EA": "Communication Services",
    "SIRI": "Communication Services",
    "TEAM": "Information Technology",
    "ANSS": "Information Technology",
    "ZS": "Information Technology",
    "ILMN": "Health Care",
}

# Third universe: "ai" key — a manually curated, fixed symbol list (not pulled
# from any index). Edit this list directly to add/remove names.
AI_UNIVERSE: list[str] = [
    # TODO: semboller buraya eklenecek
]

# Manual sector overrides for AI_UNIVERSE names that aren't in the S&P 500 /
# Nasdaq-100 sector maps above. Only needed for symbols outside both indexes.
AI_SECTOR_OVERRIDES: dict[str, str] = {
    # "SEMBOL": "Sektör Adı",
}


def _fetch_sp500() -> pd.DataFrame:
    r = requests.get(SP500_CSV, headers=HEADERS, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df = df.rename(columns={"Symbol": "symbol", "GICS Sector": "sector"})
    # yfinance uses dashes where the index uses dots (BRK.B -> BRK-B)
    df["symbol"] = df["symbol"].str.replace(".", "-", regex=False)
    return df[["symbol", "sector"]]


def _fetch_sector_live(symbol: str) -> str | None:
    """Last-resort sector lookup for names NDX_ONLY_SECTORS hasn't caught up with yet
    (index membership churns; the hardcoded dict will occasionally lag behind it)."""
    try:
        sector = yf.Ticker(symbol).info.get("sector")
        return sector or None
    except Exception:
        return None


def _fetch_ndx_symbols() -> list:
    try:
        r = requests.get(NDX_WIKI, headers=HEADERS, timeout=30)
        r.raise_for_status()
        tables = pd.read_html(io.StringIO(r.text))
        for t in tables:
            cols = [str(c) for c in t.columns]
            sym_col = next((c for c in cols if c in ("Ticker", "Symbol")), None)
            if sym_col and len(t) > 50:  # the constituents table, not a small sidebar
                syms = t[sym_col].astype(str).str.strip().str.replace(".", "-", regex=False)
                return [s for s in syms.tolist() if s and s.upper() != "NAN"]
    except Exception as e:
        print(f"[universe] Nasdaq-100 live fetch failed ({e}); using fallback list")
    return list(NDX_FALLBACK)


def build_universe() -> dict:
    """Returns {'sp500': {symbol: sector}, 'ndx': {symbol: sector}, 'ai': {symbol: sector}},
    cached to disk."""
    try:
        sp = _fetch_sp500()
        sp_map = dict(zip(sp["symbol"], sp["sector"]))
    except Exception as e:
        print(f"[universe] S&P 500 fetch failed ({e}); falling back to cache")
        if os.path.exists(CACHE_PATH):
            with open(CACHE_PATH) as f:
                return json.load(f)
        raise

    ndx_syms = _fetch_ndx_symbols()
    ndx_map = {}
    for s in ndx_syms:
        sector = sp_map.get(s) or NDX_ONLY_SECTORS.get(s) or _fetch_sector_live(s)
        if sector:
            ndx_map[s] = sector
        else:
            # Unknown sector: still tradable, but flagged so concentration limits
            # treat it as its own bucket rather than silently grouping it.
            ndx_map[s] = "Unknown"

    ai_map = {}
    for s in AI_UNIVERSE:
        sector = sp_map.get(s) or ndx_map.get(s) or AI_SECTOR_OVERRIDES.get(s) or _fetch_sector_live(s)
        ai_map[s] = sector or "Unknown"

    result = {"sp500": sp_map, "ndx": ndx_map, "ai": ai_map}
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(result, f, indent=2)
    print(f"[universe] S&P 500: {len(sp_map)} names, Nasdaq-100: {len(ndx_map)} names, "
          f"AI: {len(ai_map)} names")
    return result


def passes_liquidity(df) -> bool:
    """20-day average volume must clear MIN_AVG_VOLUME."""
    if df is None or len(df) < 20 or "Volume" not in df:
        return False
    avg_vol = df["Volume"].tail(20).mean()
    return bool(avg_vol >= MIN_AVG_VOLUME)
