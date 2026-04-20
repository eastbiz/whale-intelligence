"""
Whale Intelligence — Personal Options Trading Scanner
v5 — Full framework implementation:
     Earnings blackout, pullback filter, 200MA, PMCC detection,
     Bull Call Spread, tiered position sizing, IVP, deep ITM LEAPS,
     deal quality checklist, Peter Lynch discovery
"""

import os, json, re, math, time
from datetime import timezone as tz
from zoneinfo import ZoneInfo
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Optional

# ── API Keys ────────────────────────────────────────────────
# Pacific Time helper (your local timezone)
PT = ZoneInfo("America/Los_Angeles")

def now_et():
    """Current time in Pacific Time (handles DST automatically)."""
    return datetime.now(tz.utc).astimezone(PT)

UNUSUAL_WHALES_API_KEY = ""  # Removed — no longer used
TELEGRAM_BOT_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID       = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY      = os.environ.get("ANTHROPIC_API_KEY", "")
IBKR_FLEX_TOKEN        = os.environ.get("IBKR_FLEX_TOKEN", "")
IBKR_FLEX_QUERY_ID     = os.environ.get("IBKR_FLEX_QUERY_ID", "")

PORTFOLIO_SIZE = 7_000_000  # fallback — overridden at runtime by live account data

# ── Sizing limits (configurable) ─────────────────────────────
MAX_CSP_ALLOCATION_PCT = 0.25   # max 25% of portfolio in CSP obligations
MAX_CC_COVERAGE_PCT    = 0.50   # max 50% of owned shares covered by calls

# ── Schwab account number → label mapping ────────────────────
# Format: last 4 digits or full number (dashes optional)
# Update CRT number once confirmed
SCHWAB_ACCOUNT_LABELS = {
    "17860185": "IRA",       # Schwab IRA      1786-0185
    "52644501": "CRT",       # Schwab CRT      5264-4501
    "62969383": "Personal",  # Schwab Personal 6296-9383
}

# ── Canonical tier target ranges (used everywhere) ───────────
# Single source of truth — do not duplicate below
TARGET_RANGES = {
    "Core":          (5.0, 10.0),
    "Growth":        (3.0,  6.0),
    "Cyclical":      (2.0,  5.0),
    "Opportunistic": (1.0,  3.0),
}
# Canonical tier allocations: (normal_max, hard_max)
# normal_max = On Target ceiling, hard_max = Overweight trigger
TIER_ALLOCATIONS = {
    "Core":          (0.08, 0.12),
    "Growth":        (0.05, 0.08),
    "Cyclical":      (0.04, 0.06),
    "Opportunistic": (0.02, 0.04),
}
# Keep TIER_MAX_PCT as alias for backward compat
TIER_MAX_PCT = {k: v[1] for k, v in TIER_ALLOCATIONS.items()}

# ── Per-ticker allocation targets (updated Apr 17 2026) ──────
# target_pct: desired portfolio weight
# On Target zone: 80%–120% of target_pct (±20% tolerance band)
# Speculative tickers at 0% show "Not Held" rather than "Underweight"
TICKER_TARGETS = {
    # Core
    "AAPL":  {"target_pct":  8.0, "speculative": False},
    "AMZN":  {"target_pct": 10.0, "speculative": False},
    "GOOGL": {"target_pct": 10.0, "speculative": False},
    "IBKR":  {"target_pct":  5.0, "speculative": False},
    "MELI":  {"target_pct":  5.0, "speculative": False},
    "MSFT":  {"target_pct":  6.0, "speculative": False},
    "NOW":   {"target_pct":  4.0, "speculative": False},
    "NVDA":  {"target_pct": 12.0, "speculative": False},
    "TSM":   {"target_pct":  6.0, "speculative": False},
    # Trading
    "CRDO":  {"target_pct":  3.0, "speculative": False},
    "FIX":   {"target_pct":  3.0, "speculative": False},
    "MU":    {"target_pct":  4.0, "speculative": False},
    "NFLX":  {"target_pct":  4.0, "speculative": False},
    "PLTR":  {"target_pct":  4.0, "speculative": False},
    "TSLA":  {"target_pct":  3.0, "speculative": False},
    # Speculative — "Not Held" when at 0%, no BUY pressure
    "CLS":   {"target_pct":  1.0, "speculative": True},
    "GRBK":  {"target_pct":  1.0, "speculative": True},
    "IBIT":  {"target_pct":  1.0, "speculative": True},
    "KNX":   {"target_pct":  2.0, "speculative": True},
    "LULU":  {"target_pct":  2.0, "speculative": True},
    "NBIS":  {"target_pct":  2.0, "speculative": True},
    "NVO":   {"target_pct":  2.0, "speculative": True},
    "POWL":  {"target_pct":  2.0, "speculative": True},
}
TICKER_TOLERANCE = 0.20  # ±20% relative band around target_pct

# ── Strategy parameters (from framework doc) ────────────────
CSP_DTE_MIN           = 30;   CSP_DTE_MAX     = 45
CSP_MIN_DTE           = 30;   CSP_MAX_DTE     = 45   # aliases
CSP_DELTA_PLTR_MIN    = 0.20; CSP_DELTA_PLTR_MAX = 0.25  # PLTR stricter
CSP_DELTA_HIGH_IVP_MAX= 0.35  # allowed when IVP > 50
CC_DTE_MIN            = 30;   CC_DTE_MAX      = 45
LEAPS_DTE_MIN         = 500   # 2+ years
# CSP: default delta 0.25-0.30, up to 0.35 only when IVP > 50
CSP_DELTA_MIN         = 0.25; CSP_DELTA_MAX   = 0.35  # target 0.25-0.30, hard max 0.35
CSP_DELTA_MAX_HIGH_IV = 0.35  # allowed when IVP > 50
CC_DELTA_MIN          = 0.20; CC_DELTA_MAX    = 0.30  # target range per doc
CC_DELTA_HARD_MAX     = 0.35  # absolute ceiling for income CC
CC_DELTA_OW_MAX       = 0.50  # overweight positions — allow closer strikes
LEAPS_DELTA_MIN       = 0.75; LEAPS_DELTA_MAX = 0.99  # max removed — BE% and extrinsic% are the real gates
CSP_MIN_ANNUALIZED    = 20.0  # preferred minimum (high vol stocks)
CC_MIN_ANNUALIZED     = 8.0   # lowered — stable core names rarely give 15%
MAX_ANNUALIZED        = 120.0 # cap bad data
IVP_MIN_SELL          = 30    # floor — skip below 30
IVP_ELEVATED          = 50    # "elevated" — allow wider delta, flag as excellent
IVP_MAX_BUY           = 50    # max IVP to buy LEAPS
LEAPS_EXTRINSIC_MAX   = 20.0  # 20% max extrinsic — primary cost filter for LEAPS — target <20%
# Earnings filter: <14 days = hard stop, 14-21 = warning, >21 = normal
EARNINGS_HARD_STOP    = 14    # hard stop
EARNINGS_WARNING      = 21    # warning label
# Price location: >15% below 52w high = preferred, 8-15% = caution, <8% = skip CSP
NEAR_HIGH_SKIP        = 0.08  # skip CSP if within 8% of 52w high
NEAR_HIGH_CAUTION     = 0.15  # caution if 8-15% below high
PULLBACK_MIN          = 0.15  # preferred zone starts here
PULLBACK_MAX          = 0.65  # not >65% (company may be broken)
# MA filters
MA50_EXTENDED         = 0.08  # skip CSP if >8% above 50-day MA
# ── Gap / spike / drop thresholds ───────────────────────
GAP_RISK_PCT          = 0.08  # Income mode: skip if moved >8% today
GAP_RISK_PCT_OPP      = 0.20  # Opportunistic mode: allow up to 20% move

# Mode 2: Post-Spike CC — triggered BY upward gaps
OPP_SPIKE_MIN         = 0.08  # minimum upward spike to trigger
OPP_SPIKE_DAYS        = 3
OPP_CC_DTE_MIN        = 14
OPP_CC_DTE_MAX        = 30
OPP_CC_DELTA_MIN      = 0.25  # post-spike: slightly aggressive ok
OPP_CC_DELTA_MAX      = 0.40  # post-spike hard max per doc
OPP_IVP_MIN           = 40
OPP_EARNINGS_MIN      = 7

# ── Per-symbol income trade settings ────────────────────────────────────────
# Source: Positions_Buy_Sell_Delta.xlsx (Income Trades sheet) — updated Apr 17 2026
# buy_under:  max effective entry for CSP (effective_entry <= buy_under * 1.03 hard gate)
# sell_above: min effective exit for CC  (strike + premium >= sell_above hard gate)
# Delta ranges are hard filters applied per strategy.
SYMBOL_SETTINGS = {
    # ── CORE ─────────────────────────────────────────────────────────────────
    "AAPL": {"buy_under":  200, "sell_above":  330, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.28, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "AMZN": {"buy_under":  200, "sell_above":  300, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.28, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "GOOGL":{"buy_under":  250, "sell_above":  395, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.28, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "IBKR": {"buy_under":   50, "sell_above":   95, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.28, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "MELI": {"buy_under": 1480, "sell_above": 2100, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "MSFT": {"buy_under":  355, "sell_above":  500, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.28, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "NOW":  {"buy_under":   75, "sell_above":  130, "csp_delta_min": 0.20, "csp_delta_max": 0.25, "cc_delta_min": 0.20, "cc_delta_max": 0.25, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "NVDA": {"buy_under":  155, "sell_above":  240, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.28, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "TSM":  {"buy_under":  270, "sell_above":  440, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    # ── TRADING ──────────────────────────────────────────────────────────────
    "CRDO": {"buy_under":   80, "sell_above":  200, "csp_delta_min": 0.20, "csp_delta_max": 0.28, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "FIX":  {"buy_under": 1000, "sell_above": 1900, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "MU":   {"buy_under":  290, "sell_above":  550, "csp_delta_min": 0.20, "csp_delta_max": 0.28, "cc_delta_min": 0.20, "cc_delta_max": 0.28, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "NFLX": {"buy_under":   65, "sell_above":  125, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.32, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "PLTR": {"buy_under":   80, "sell_above":  175, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "TSLA": {"buy_under":  335, "sell_above":  490, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    # ── SPECULATIVE ──────────────────────────────────────────────────────────
    "CLS":  {"buy_under":  280, "sell_above":  440, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "GRBK": {"buy_under":   55, "sell_above":   80, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "IBIT": {"buy_under":   34, "sell_above":   47, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "KNX":  {"buy_under":   52, "sell_above":   75, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "LULU": {"buy_under":  145, "sell_above":  195, "csp_delta_min": 0.20, "csp_delta_max": 0.28, "cc_delta_min": 0.20, "cc_delta_max": 0.28, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "NBIS": {"buy_under":   90, "sell_above":  190, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "NVO":  {"buy_under":   33, "sell_above":   48, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.28, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "POWL": {"buy_under":  163, "sell_above":  275, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
}

# Speculative tickers — smaller position sizing, wider OTM buffers.
# Suppressed from Telegram CSP entry alerts (entries only on deliberate decision).
# CC alerts still sent when approaching sell_above (useful for existing positions).
SPECULATIVE_TICKERS = {"CLS", "GRBK", "IBIT", "KNX", "LULU", "NBIS", "NVO", "POWL"}

# Mode 3: Post-Drop CSP — triggered BY downward drops
DROP_TRIGGER_MIN      = 0.08  # minimum drop to trigger (8-12%+)
DROP_CSP_DTE_MIN      = 25    # DTE range
DROP_CSP_DTE_MAX      = 45
DROP_CSP_DELTA_MIN    = 0.20  # post-drop: conservative
DROP_CSP_DELTA_MAX    = 0.25  # tighter range — post-drop is higher risk
DROP_IVP_MIN          = 40    # preferred >50
DROP_EARNINGS_MIN     = 7     # hard stop
DROP_SIZE_FACTOR      = 0.60  # 50-70% of normal position size

# Quality tiers allowed for post-drop CSP
DROP_CSP_ALLOWED_TIERS = {"Core", "Growth"}
# Liquidity requirements
MIN_OPEN_INTEREST     = 1000
MIN_DAILY_VOLUME      = 100
MAX_BID_ASK_SPREAD    = 0.05  # 5%
# Premium efficiency: minimum premium as % of strike
MIN_PREMIUM_PCT_30_45 = 0.015 # 1.5% for 30-45 DTE
MIN_PREMIUM_PCT_45_60 = 0.020 # 2.0% for 45-60 DTE
# Sector exposure cap
MAX_SECTOR_PCT        = 0.25  # 25% max per sector
BCS_MIN_ROR           = 0.80  # Bull Call Spread min return on risk

# ── Stock universe defined below at canonical location ──────
# (see CORE_STOCKS / GROWTH_STOCKS / CYCLICAL_STOCKS / OPPORTUNISTIC_STOCKS)


# ── Schwab API ───────────────────────────────────────────
SCHWAB_APP_KEY       = os.environ.get("SCHWAB_APP_KEY", "")
SCHWAB_APP_SECRET    = os.environ.get("SCHWAB_APP_SECRET", "")
SCHWAB_REFRESH_TOKEN = os.environ.get("SCHWAB_REFRESH_TOKEN", "")
SCHWAB_ACCESS_TOKEN  = os.environ.get("SCHWAB_ACCESS_TOKEN", "")
SCHWAB_TOKEN_PATH    = os.environ.get("SCHWAB_TOKEN_PATH", "schwab_token.json")
SCHWAB_BASE          = "https://api.schwabapi.com"
SCHWAB_MARKET_BASE   = f"{SCHWAB_BASE}/marketdata/v1"
SCHWAB_TRADER_BASE   = f"{SCHWAB_BASE}/trader/v1"

# Use schwab-py for token management when token file exists (Windows local mode)
_schwab_py_client = None
def get_schwab_py_client():
    """Get schwab-py client if token file exists — handles refresh automatically."""
    global _schwab_py_client
    if _schwab_py_client:
        return _schwab_py_client
    if not os.path.exists(SCHWAB_TOKEN_PATH):
        return None
    try:
        import schwab
        _schwab_py_client = schwab.auth.easy_client(
            api_key=SCHWAB_APP_KEY,
            app_secret=SCHWAB_APP_SECRET,
            callback_url="https://127.0.0.1:8182",
            token_path=SCHWAB_TOKEN_PATH,
            interactive=False
        )
        print("   ✅ schwab-py client loaded from token file")
        return _schwab_py_client
    except Exception as e:
        print(f"   ⚠️ schwab-py client error: {e}")
        return None

_schwab_cache = {"access_token": "", "expires_at": 0}


def schwab_get_token() -> str:
    """Auto-refresh Schwab access token using refresh token."""
    import time as _time, base64
    c = _schwab_cache
    # Use cached token if still valid
    if c["access_token"] and _time.time() < c["expires_at"] - 60:
        return c["access_token"]
    # Try to refresh
    if not SCHWAB_REFRESH_TOKEN:
        return SCHWAB_ACCESS_TOKEN  # fall back to stored
    try:
        creds = base64.b64encode(f"{SCHWAB_APP_KEY}:{SCHWAB_APP_SECRET}".encode()).decode()
        r = requests.post(
            f"{SCHWAB_BASE}/v1/oauth/token",
            headers={"Authorization": f"Basic {creds}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "refresh_token", "refresh_token": SCHWAB_REFRESH_TOKEN},
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            c["access_token"] = data["access_token"]
            c["expires_at"]   = _time.time() + data.get("expires_in", 1800)
            print("   ✅ Schwab token refreshed")
            return c["access_token"]
        else:
            if r.status_code == 400:
                print(f"   ⚠️ Schwab refresh token expired (400)")
                print(f"   ℹ️  Run schwab_test.py on Windows, then update SCHWAB_REFRESH_TOKEN in GitHub Secrets")
            else:
                print(f"   ⚠️ Schwab token refresh failed ({r.status_code}) — using stored token")
            return SCHWAB_ACCESS_TOKEN
    except Exception as e:
        print(f"   ⚠️ Schwab token error: {e}")
        return SCHWAB_ACCESS_TOKEN


def schwab_headers() -> dict:
    """Get auth headers — uses schwab-py client if available, else manual refresh."""
    py_client = get_schwab_py_client()
    if py_client:
        # schwab-py manages token internally — extract the token
        try:
            token = py_client.session.token["access_token"]
            return {"Authorization": f"Bearer {token}"}
        except:
            pass
    token = schwab_get_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def schwab_get_quotes(tickers: list) -> dict:
    """Real-time quotes: price, 52w range, volume, prev close."""
    if not SCHWAB_APP_KEY:
        return {}
    try:
        r = requests.get(
            f"{SCHWAB_MARKET_BASE}/quotes",
            headers=schwab_headers(),
            params={"symbols": ",".join(tickers), "fields": "quote,fundamental"},
            timeout=15
        )
        if r.status_code != 200:
            print(f"   Schwab quotes error: {r.status_code}")
            return {}
        result = {}
        for ticker, info in r.json().items():
            q = info.get("quote", {})
            f = info.get("fundamental", {})
            # ivPercentile is in the quote object for equities
            _ivp_raw = (q.get("volatility", 0) or
                        q.get("impliedYield", 0) or 0)
            # Print raw fundamental keys on first ticker to debug
            if not result:
                print(f"   Schwab quote keys for {ticker}: q={list(q.keys())[:10]} f={list(f.keys())[:10]}")
            result[ticker] = {
                "price":       float(q.get("lastPrice",  q.get("mark", 0)) or 0),
                "week52_high": float(q.get("52WeekHigh", 0) or 0),
                "week52_low":  float(q.get("52WeekLow",  0) or 0),
                "avg_volume":  int(q.get("totalVolume",  0) or 0),
                "prev_close":  float(q.get("closePrice", 0) or 0),
                "pe_ratio":    float(f.get("peRatio",    0) or 0),
                "_raw_quote":  q,   # keep raw so we can find IVP field
            }
        print(f"   Schwab quotes: {len(result)}/{len(tickers)} ✓")
        return result
    except Exception as e:
        print(f"   Schwab quotes exception: {e}")
        return {}


def schwab_get_option_chain(ticker: str, from_date: str, to_date: str) -> list:
    """
    Real option chain with TRUE delta, IV, OI, theta from Schwab.
    Replaces Yahoo Finance + Black-Scholes approximation entirely.
    """
    if not SCHWAB_APP_KEY:
        return []
    try:
        r = requests.get(
            f"{SCHWAB_MARKET_BASE}/chains",
            headers=schwab_headers(),
            params={
                "symbol":       ticker,
                "contractType": "ALL",
                "strikeCount":  20,
                "includeUnderlyingQuote": True,
                "fromDate":     from_date,
                "toDate":       to_date,
                "optionType":   "S",
            },
            timeout=15
        )
        if r.status_code != 200:
            print(f"   Schwab chain {ticker}: {r.status_code}")
            return []
        data      = r.json()
        contracts = []
        price     = float(data.get("underlyingPrice", 0) or 0)
        # Schwab chain-level fields:
        # "volatility" = current ATM IV of the chain (annualized %) — THIS is what Schwab returns
        # "ivPercentile" = Schwab does NOT reliably return this field
        # We store the current IV and compute IVP from per-contract IVs in calculate_ivp
        _ivp  = float(data.get("ivPercentile", 0) or 0)  # only set if Schwab returns it
        _iv   = float(data.get("volatility", 0) or 0) / 100  # current ATM