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

# ── Position limits by tier ─────────────────────────────────
# Core: 8% | Growth: 5% | Cyclical: 4% | Opportunistic: 2.5%
POSITION_TIERS = {
    # Core Compounders — 8%
    "AAPL":0.08,"AMZN":0.08,"ASML":0.08,"BRK-B":0.08,"GOOGL":0.08,
    "MSFT":0.08,"NVDA":0.08,"TSM":0.08,"IBKR":0.08,"MELI":0.08,
    "CPRT":0.08,"VRTX":0.08,"NVO":0.08,
    # Growth / Semi-Core — 5%
    "NOW":0.05,"DDOG":0.05,"UBER":0.05,"NFLX":0.05,"PLTR":0.05,"META":0.05,
    # Cyclical Compounders — 4%
    "MU":0.04,"KNX":0.04,"POWL":0.04,
    # Opportunistic — 2.5%
    "__DEFAULT__": 0.025
}

# ── Strategy parameters (from framework doc) ────────────────
CSP_DTE_MIN           = 30;   CSP_DTE_MAX     = 45
CSP_MIN_DTE           = 30;   CSP_MAX_DTE     = 45   # aliases
CSP_DELTA_PLTR_MIN    = 0.20; CSP_DELTA_PLTR_MAX = 0.25  # PLTR stricter
CSP_DELTA_HIGH_IVP_MAX= 0.35  # allowed when IVP > 50
CC_DTE_MIN            = 30;   CC_DTE_MAX      = 45
LEAPS_DTE_MIN         = 500   # 2+ years
# CSP: default delta 0.25-0.30, up to 0.35 only when IVP > 50
CSP_DELTA_MIN         = 0.25; CSP_DELTA_MAX   = 0.30
CSP_DELTA_MAX_HIGH_IV = 0.35  # allowed when IVP > 50
CC_DELTA_MIN          = 0.15; CC_DELTA_MAX    = 0.25
LEAPS_DELTA_MIN       = 0.80; LEAPS_DELTA_MAX = 0.90
CSP_MIN_ANNUALIZED    = 20.0  # preferred minimum (high vol stocks)
CC_MIN_ANNUALIZED     = 8.0   # lowered — stable core names rarely give 15%
MAX_ANNUALIZED        = 120.0 # cap bad data
IVP_MIN_SELL          = 30    # floor — skip below 30
IVP_ELEVATED          = 50    # "elevated" — allow wider delta, flag as excellent
IVP_MAX_BUY           = 50    # max IVP to buy LEAPS
LEAPS_EXTRINSIC_MAX   = 25.0  # tightened from 30% — target <20%
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

# Mode 2: Post-Spike CC — triggered BY upward gaps
OPP_SPIKE_MIN         = 0.08  # minimum upward spike to trigger
OPP_SPIKE_DAYS        = 3
OPP_CC_DTE_MIN        = 14
OPP_CC_DTE_MAX        = 30
OPP_CC_DELTA_MIN      = 0.20
OPP_CC_DELTA_MAX      = 0.35
OPP_IVP_MIN           = 40
OPP_EARNINGS_MIN      = 7

# Mode 3: Post-Drop CSP — triggered BY downward drops
DROP_TRIGGER_MIN      = 0.08  # minimum drop to trigger (8-12%+)
DROP_CSP_DTE_MIN      = 25    # DTE range
DROP_CSP_DTE_MAX      = 45
DROP_CSP_DELTA_MIN    = 0.20  # more conservative than income scanner
DROP_CSP_DELTA_MAX    = 0.25
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

CORE_STOCKS = ["AAPL","AMZN","ASML","BRK-B","GOOGL","IBKR","MELI","MU","NVDA","NVO","TSM"]
OPPORTUNISTIC_STOCKS = [
    # Quality opportunistic — scan all strategies
    "CLS","CRDO","FIX","KNX","NFLX","NOW","POWL",
    "UBER","VRT","IBIT","TSLA",
    # Watchlist only — scan but apply extra caution
    "BABA",    # China risk — LEAPS only, no CSP
    "CPRT",    # Downtrend, wait for 200MA stabilization
    "LULU",    # Growth slowdown, unclear if temporary
    "PLTR",    # Extreme valuation, opportunistic only
    "VRTX",    # Biotech, wide strikes required
    # Volatility opportunity — held shares, sell CC on spikes
    "NBIS",    # AI infrastructure, volatile, CC on spikes only
]

# Speculative tickers — require wider OTM buffers, stricter timing
SPECULATIVE = {"VRTX","IBIT","PLTR","BABA","CRDO","LULU","NBIS"}

# LEAPS/CSP only — no systematic CC income
LEAPS_ONLY = {"BABA", "CPRT"}

# Volatility spike CC candidates — only flag CC when stock spikes 8%+ upward
# These are stocks where you hold shares and want to sell calls on spikes
SPIKE_CC_CANDIDATES = {"NBIS", "IBIT", "PLTR"}

# NVO is now a Core holding with valuation awareness

# Unusual Whales removed — using Schwab API for all market data


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
            result[ticker] = {
                "price":       float(q.get("lastPrice",  q.get("mark", 0)) or 0),
                "week52_high": float(q.get("52WeekHigh", 0) or 0),
                "week52_low":  float(q.get("52WeekLow",  0) or 0),
                "avg_volume":  int(q.get("totalVolume",  0) or 0),
                "prev_close":  float(q.get("closePrice", 0) or 0),
                "pe_ratio":    float(f.get("peRatio",    0) or 0),
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
        for opt_type, map_key in [("P","putExpDateMap"),("C","callExpDateMap")]:
            for exp_str, strikes in data.get(map_key, {}).items():
                exp_date = exp_str.split(":")[0]
                for strike_str, opts in strikes.items():
                    for opt in opts:
                        contracts.append({
                            "option_symbol":   opt.get("symbol",""),
                            "strike":          float(strike_str),
                            "expiry":          exp_date,
                            "option_type":     opt_type,
                            "nbbo_bid":        float(opt.get("bid",           0) or 0),
                            "nbbo_ask":        float(opt.get("ask",           0) or 0),
                            "iv":              float(opt.get("volatility",     0) or 0) / 100,
                            "delta":           float(opt.get("delta",         0) or 0),
                            "theta":           float(opt.get("theta",         0) or 0),
                            "gamma":           float(opt.get("gamma",         0) or 0),
                            "open_interest":   int(opt.get("openInterest",    0) or 0),
                            "volume":          int(opt.get("totalVolume",     0) or 0),
                            "underlying_price": price,
                        })
        print(f"   Schwab chain {ticker}: {len(contracts)} contracts ✓")
        return contracts
    except Exception as e:
        print(f"   Schwab chain {ticker} error: {e}")
        return []


def schwab_get_ivp(ticker: str) -> float:
    """
    Calculate real IV Percentile from 1-year Schwab price history.
    Much more accurate than our current ATM contract proxy.
    """
    if not SCHWAB_APP_KEY:
        return 0
    try:
        from datetime import timedelta
        end   = datetime.now()
        start = end - timedelta(days=365)
        r = requests.get(
            f"{SCHWAB_MARKET_BASE}/pricehistory",
            headers=schwab_headers(),
            params={
                "symbol":        ticker,
                "periodType":    "year",
                "period":        1,
                "frequencyType": "daily",
                "frequency":     1,
                "startDate":     int(start.timestamp() * 1000),
                "endDate":       int(end.timestamp()   * 1000),
            },
            timeout=15
        )
        if r.status_code != 200:
            return 0
        candles = r.json().get("candles", [])
        if len(candles) < 30:
            return 0
        closes  = [c["close"] for c in candles if c.get("close")]
        returns = [(closes[i]-closes[i-1])/closes[i-1] for i in range(1,len(closes))]
        vols    = []
        for i in range(20, len(returns)):
            w    = returns[i-20:i]
            mean = sum(w)/20
            var  = sum((x-mean)**2 for x in w)/20
            vols.append((var**0.5)*(252**0.5))
        if not vols:
            return 0
        curr = vols[-1]
        return round(sum(1 for v in vols if v < curr)/len(vols)*100, 1)
    except:
        return 0


def schwab_parse_positions(accounts: list) -> dict:
    """
    Parse Schwab accounts into a flat dict of stock holdings.
    Returns {ticker: {quantity, avg_cost, market_value, account_type}}
    Aggregates across ALL accounts so cross-account exposure is visible.
    """
    holdings = {}
    for acc in accounts:
        acc_type = acc.get("account_type", "")
        for pos in acc.get("positions", []):
            inst = pos.get("instrument", {})
            if inst.get("assetType") != "EQUITY":
                continue
            ticker = inst.get("symbol", "").replace("/", "-")
            qty    = float(pos.get("longQuantity",  0) or 0)
            if qty <= 0:
                continue
            avg    = float(pos.get("averagePrice",   0) or 0)
            mval   = float(pos.get("marketValue",    0) or 0)
            if ticker in holdings:
                # Aggregate across accounts
                holdings[ticker]["quantity"]     += qty
                holdings[ticker]["market_value"] += mval
            else:
                holdings[ticker] = {
                    "quantity":     qty,
                    "avg_cost":     avg,
                    "market_value": mval,
                    "account_type": acc_type,
                    "asset_class":  "STK",
                }
    return holdings


def schwab_get_accounts() -> list:
    """Fetch all Schwab accounts with balances and positions."""
    if not SCHWAB_APP_KEY:
        return []
    try:
        r = requests.get(
            f"{SCHWAB_TRADER_BASE}/accounts",
            headers=schwab_headers(),
            params={"fields": "positions"},
            timeout=15
        )
        if r.status_code != 200:
            print(f"   Schwab accounts: {r.status_code}")
            return []
        result = []
        for acc in r.json():
            sec = acc.get("securitiesAccount", {})
            bal = sec.get("currentBalances", {})
            result.append({
                "account_id":      sec.get("accountNumber",""),
                "account_type":    sec.get("type",""),
                "buying_power":    float(bal.get("buyingPower",       0) or 0),
                "cash":            float(bal.get("cashBalance",        0) or 0),
                "net_liquidation": float(bal.get("liquidationValue",   0) or 0),
                "positions":       sec.get("positions", []),
            })
        print(f"   Schwab accounts: {len(result)} ✓")
        return result
    except Exception as e:
        print(f"   Schwab accounts error: {e}")
        return []


# ════════════════════════════════════════════════════════════
# MARKET DATA
# ════════════════════════════════════════════════════════════

def get_market_data(tickers: list) -> dict:
    """Price, 52w range, volume, earnings date, 200-day MA proxy."""
    data = {}
    for ticker in tickers:
        yf = ticker.replace("BRK.B","BRK-B")
        try:
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{yf}"
                f"?range=1y&interval=1d",
                headers={"User-Agent":"Mozilla/5.0"}, timeout=12
            )
            j    = r.json()["chart"]["result"][0]
            meta = j["meta"]
            closes = j.get("indicators",{}).get("quote",[{}])[0].get("close",[])
            closes = [c for c in closes if c]

            closes_clean = [c for c in closes if c is not None]
            ma200 = sum(closes_clean[-200:]) / min(200, len(closes_clean)) if closes_clean else 0
            ma50  = sum(closes_clean[-50:])  / min(50,  len(closes_clean)) if closes_clean else 0
            price = float(meta.get("regularMarketPrice", 0))
            # Only use regularMarketPreviousClose — chartPreviousClose can be stale
            prev_close = float(meta.get("regularMarketPreviousClose", 0) or 0)
            if prev_close <= 0:
                prev_close = price  # can't calculate change, assume 0%
            day_change_pct = (price - prev_close) / prev_close if prev_close > 0 else 0
            # Sanity check — ignore if change looks like stale data (>50% single day move)
            if abs(day_change_pct) > 0.50:
                day_change_pct = 0.0
                prev_close = price

            data[ticker] = {
                "price":          round(price, 2),
                "week52_high":    round(float(meta.get("fiftyTwoWeekHigh", price)), 2),
                "week52_low":     round(float(meta.get("fiftyTwoWeekLow",  price)), 2),
                "avg_volume":     int(meta.get("averageDailyVolume3Month", 0)),
                "ma200":          round(ma200, 2),
                "ma50":           round(ma50, 2),
                "above_ma200":    price >= ma200 * 0.97,
                "pct_above_ma50": (price - ma50) / ma50 if ma50 > 0 else 0,
                "day_change_pct": round(day_change_pct, 4),
            }
        except Exception as e:
            data[ticker] = {"price":0,"week52_high":0,"week52_low":0,
                            "avg_volume":0,"ma200":0,"above_ma200":False}
    return data


def get_earnings_date(ticker: str) -> Optional[datetime]:
    """Fetch next earnings date from Yahoo Finance."""
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
            f"?modules=calendarEvents",
            headers={"User-Agent":"Mozilla/5.0"}, timeout=8
        )
        j   = r.json().get("quoteSummary",{}).get("result",[{}])[0]
        ts  = j.get("calendarEvents",{}).get("earnings",{}).get("earningsDate",[])
        if ts:
            return datetime.fromtimestamp(ts[0].get("raw",0))
    except: pass
    return None


def get_fundamentals(ticker: str) -> dict:
    """PEG, EPS growth, revenue growth, profit margin for Peter Lynch screen."""
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
            f"?modules=defaultKeyStatistics,financialData",
            headers={"User-Agent":"Mozilla/5.0"}, timeout=8
        )
        j  = r.json().get("quoteSummary",{}).get("result",[{}])[0]
        ks = j.get("defaultKeyStatistics",{})
        fd = j.get("financialData",{})
        return {
            "peg_ratio":      ks.get("pegRatio",{}).get("raw"),
            "forward_pe":     ks.get("forwardPE",{}).get("raw"),
            "eps_growth":     fd.get("earningsGrowth",{}).get("raw"),
            "revenue_growth": fd.get("revenueGrowth",{}).get("raw"),
            "profit_margin":  fd.get("profitMargins",{}).get("raw"),
            "market_cap":     ks.get("enterpriseValue",{}).get("raw"),
        }
    except: return {}


def position_in_range(price, w52_low, w52_high) -> float:
    if w52_high <= w52_low or price <= 0: return 0.5
    return round((price - w52_low) / (w52_high - w52_low), 2)


def pullback_from_high(price, w52_high) -> float:
    """How far is price from 52w high. 0.20 = 20% below high."""
    if w52_high <= 0: return 0
    return round((w52_high - price) / w52_high, 3)


# ════════════════════════════════════════════════════════════
# IBKR
# ════════════════════════════════════════════════════════════

def get_ibkr_positions() -> dict:
    positions = {}
    if not IBKR_FLEX_TOKEN or not IBKR_FLEX_QUERY_ID:
        return positions
    try:
        r = requests.get(
            f"https://gdcdyn.interactivebrokers.com/Universal/servlet/"
            f"FlexStatementService.SendRequest"
            f"?t={IBKR_FLEX_TOKEN}&q={IBKR_FLEX_QUERY_ID}&v=3",
            timeout=15
        )
        root = ET.fromstring(r.text)
        ref  = root.findtext("ReferenceCode")
        if root.findtext("Status") != "Success" or not ref:
            return positions
        time.sleep(5)
        r2    = requests.get(
            f"https://gdcdyn.interactivebrokers.com/Universal/servlet/"
            f"FlexStatementService.GetStatement"
            f"?t={IBKR_FLEX_TOKEN}&q={ref}&v=3",
            timeout=15
        )
        root2 = ET.fromstring(r2.text)
        for pos in root2.iter("OpenPosition"):
            sym = pos.get("symbol","")
            if not sym: continue
            positions[sym] = {
                "market_value": float(pos.get("positionValue",  0) or 0),
                "quantity":     float(pos.get("position",       0) or 0),
                "avg_cost":     float(pos.get("costBasisPrice", 0) or 0),
                "pct_nav":      float(pos.get("percentOfNAV",   0) or 0),
                "asset_class":  pos.get("assetClass",""),
                "strike":       pos.get("strike",""),
                "expiry":       pos.get("expiry",""),
                "put_call":     pos.get("putCall",""),
                "underlying":   pos.get("underlyingSymbol", sym),
            }
        stk = sum(1 for v in positions.values() if v["asset_class"]=="STK")
        opt = sum(1 for v in positions.values() if v["asset_class"]=="OPT")
        print(f"   IBKR: {stk} stocks, {opt} options loaded")
    except Exception as e:
        print(f"   IBKR error: {e}")
    return positions


def find_existing_leaps(ticker: str, ibkr_positions: dict) -> Optional[dict]:
    """Check if we already own a LEAPS call on this ticker (for PMCC)."""
    today = datetime.now()
    for sym, pos in ibkr_positions.items():
        if (pos.get("asset_class") == "OPT"
                and pos.get("underlying","").upper() == ticker.upper()
                and pos.get("put_call","").upper() == "C"
                and pos.get("quantity", 0) > 0):
            try:
                exp = datetime.strptime(pos["expiry"], "%Y%m%d")
                dte = (exp - today).days
                if dte >= 300:  # it's a LEAPS
                    return {
                        "strike":   float(pos.get("strike", 0) or 0),
                        "expiry":   exp.strftime("%Y-%m-%d"),
                        "dte":      dte,
                        "quantity": int(pos.get("quantity", 0)),
                        "avg_cost": float(pos.get("avg_cost", 0) or 0),
                    }
            except: continue
    return None


# ════════════════════════════════════════════════════════════
# UNUSUAL WHALES
# ════════════════════════════════════════════════════════════

def get_option_contracts(ticker: str) -> list:
    """Replaced by schwab_get_option_chain — returns empty fallback."""
    return []


def get_darkpool(ticker: str = "", **kwargs) -> list:
    return []


def get_flow_alerts_market() -> list:
    return []


def get_market_tide() -> dict:
    return {"score": 0, "label": "Market Tide removed", "available": False}


def get_vix() -> dict:
    """Fetch VIX from Yahoo Finance. VIX = market fear gauge."""
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX",
            headers={"User-Agent":"Mozilla/5.0"}, timeout=8
        )
        price = r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"]
        vix = round(float(price), 2)

        if vix < 15:
            label = f"😴 VIX {vix} — Very low fear. Market complacent. Poor time to sell premium."
            regime = "low"
        elif vix < 20:
            label = f"😊 VIX {vix} — Low-moderate fear. Decent but not ideal for premium selling."
            regime = "low_moderate"
        elif vix < 25:
            label = f"😐 VIX {vix} — Moderate fear. Reasonable premium selling conditions."
            regime = "moderate"
        elif vix < 35:
            label = f"😬 VIX {vix} — Elevated fear. Good premium selling opportunity."
            regime = "elevated"
        else:
            label = f"😱 VIX {vix} — Extreme fear. Exceptional CSP/CC premiums but manage size carefully."
            regime = "extreme"

        return {"vix": vix, "label": label, "regime": regime}
    except Exception as e:
        return {"vix": 0, "label": "VIX unavailable", "regime": "unknown"}


def get_spike() -> dict:
    return {"available": False}


def get_oi_change() -> dict:
    return {}


def get_expiry_breakdown(ticker: str) -> dict:
    return {}


def market_go_nogo(tide: dict, vix_data: dict,
                   spy_regime: dict = None) -> dict:
    """
    Simplified go/no-go — VIX and Market Tide are for BRIEFING ONLY.
    Individual trades are filtered by per-stock IVP.
    This function always returns sell_premium=True and buy_leaps=True
    so the scanner always runs — IVP handles the actual filtering.
    The briefing informs the user; it does not block trades.
    """
    vix        = vix_data.get("vix", 20)
    vix_regime = vix_data.get("regime", "moderate")
    tide_score = tide.get("score", 0)
    spy_warning = spy_regime and not spy_regime.get("above_ma200", True)

    # Context labels for briefing only
    if vix >= 25 and tide_score > 0:
        quality = "🔥 EXCELLENT — High VIX + bullish tide. Strong premium selling environment."
    elif vix >= 20 and tide_score > -20:
        quality = "✅ GOOD — Reasonable conditions. IVP filters individual trades."
    elif tide_score < -30:
        quality = "⚠️ BEARISH TIDE — Put premium dominating. Be selective, check IVP per stock."
    elif vix < 15:
        quality = "😴 LOW VOLATILITY — Premiums are thin. Only trade highest IVP setups."
    else:
        quality = "✅ NEUTRAL — Scanner running. IVP determines trade quality per stock."

    if spy_warning:
        quality += " | ⚠️ S&P below 200MA — reduce size."

    return {
        "sell_premium": True,   # always scan — IVP filters per stock
        "buy_leaps":    True,   # always scan — timing filters per stock
        "vix":          vix,
        "tide_score":   tide_score,
        "vix_regime":   vix_regime,
        "quality":      quality,
        "spy_warning":  spy_warning if spy_warning else False,
        "bull_oi_count": 0,
        "bear_oi_count": 0,
    }




# ════════════════════════════════════════════════════════════
# OPTION MATH
# ════════════════════════════════════════════════════════════

def parse_option_symbol(sym: str):
    try:
        m = re.match(r'^([A-Z.\-]+)(\d{6})([CP])(\d{8})$', sym)
        if not m: return None
        return datetime.strptime("20"+m.group(2), "%Y%m%d"), m.group(3), int(m.group(4))/1000
    except: return None


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def estimate_delta(spot, strike, dte, iv, opt_type) -> Optional[float]:
    """Black-Scholes delta using actual spot and strike."""
    try:
        if iv <= 0 or dte <= 0 or iv > 5: return None
        t   = dte / 365
        sst = iv * math.sqrt(t)
        if sst == 0: return None
        d1  = (math.log(spot / strike) + 0.5 * iv**2 * t) / sst
        delta = norm_cdf(d1) if opt_type == "C" else norm_cdf(d1) - 1
        return round(abs(delta), 2)
    except: return None


def calculate_ivp(contracts: list) -> dict:
    """IV percentile from ATM contracts in 25-50 DTE range."""
    today = datetime.now()
    ivs   = []
    for c in contracts:
        try:
            expiry = datetime.strptime(c["expiry"], "%Y-%m-%d")
            dte = (expiry - today).days
            if not (25 <= dte <= 50): continue
            # Schwab uses "iv" field (already divided by 100 in chain parser)
            iv = float(c.get("iv", 0) or c.get("implied_volatility", 0) or 0)
            if iv > 1.0: iv = iv / 100  # normalize if percentage
        except Exception: continue
        if 0.05 < iv < 5.0:
            ivs.append(iv)
    if not ivs:
        return {"iv_current":0.30,"iv_low":0.20,"iv_high":0.60,"ivp":50}
    s       = sorted(ivs)
    iv_med  = s[len(s)//2]
    iv_rng  = s[-1] - s[0]
    ivp     = round((iv_med - s[0]) / iv_rng * 100, 1) if iv_rng > 0 else 50
    return {"iv_current":round(iv_med,3),"iv_low":round(s[0],3),
            "iv_high":round(s[-1],3),"ivp":ivp}


# ── Stock Universe ──────────────────────────────────────────
# Tier 1: Core Compounders — durable moats, long-term holds
# Primary candidates for CSP, CC, PMCC, LEAPS
# Max allocation: 8% ($560K) per position
CORE_STOCKS = [
    "AAPL",   # Ecosystem + buybacks
    "AMZN",   # AWS + logistics moat
    "ASML",   # EUV lithography monopoly
    "BRK-B",  # Capital allocation machine
    "GOOGL",  # Search dominance + AI
    "MSFT",   # AI + enterprise platform (top PMCC/CSP stock)
    "NVDA",   # AI compute backbone
    "TSM",    # Semiconductor manufacturing monopoly
    "IBKR",   # Structural brokerage winner
    "MELI",   # LATAM ecommerce + fintech
    "CPRT",   # Salvage auction network effects (valuation reset, not broken)
    "VRTX",   # Profitable biotech, CF franchise $10B+ revenue, zero debt
    "NVO",    # GLP-1 leadership — Core holding with valuation awareness
]

# Tier 2: Growth / Semi-Core — can compound but more valuation sensitive
# Max allocation: 5% ($350K) per position
GROWTH_STOCKS = [
    "NOW",    # Elite SaaS platform
    "DDOG",   # Observability infrastructure leader
    "UBER",   # Mobility + logistics platform
    "NFLX",   # Global media platform
    "PLTR",   # AI-driven data infrastructure (extreme valuation — stricter delta)
    "META",   # AI + global social graph (top options liquidity, PMCC candidate)
]

# Tier 3: Cyclical Compounders — good companies tied to cycles
# Max allocation: 4% ($280K) per position
CYCLICAL_STOCKS = [
    "MU",     # Memory semiconductor cycle
    "KNX",    # Trucking cycle
    "POWL",   # Electrical equipment cycle
]

# Tier 4: Opportunistic / Tactical — best for LEAPS, CSP harvesting, rotation
# Max allocation: 2.5% ($175K) per position
OPPORTUNISTIC_STOCKS = [
    "CLS",    # AI server demand cycle
    "CRDO",   # Hyperscaler networking cycle
    "FIX",    # Construction cycle
    "VRT",    # Data center power infrastructure
    "LULU",   # Retail cyclicality
    "TSLA",   # High volatility, narrative driven (best options liquidity)
    "BABA",   # Geopolitical risk — LEAPS/CSP only, no CC
    "IBIT",   # Bitcoin proxy — directional/LEAPS only
    "NBIS",   # AI infrastructure — opportunistic CC/volatility spike mode
]

# All tickers for scanning
ALL_TICKERS = CORE_STOCKS + GROWTH_STOCKS + CYCLICAL_STOCKS + OPPORTUNISTIC_STOCKS

# Speculative — wider OTM buffers required
SPECULATIVE = {"IBIT", "BABA", "CRDO", "LULU", "NBIS"}

# LEAPS/CSP only — no CC income generation
LEAPS_ONLY = {"BABA", "IBIT"}

# Stricter delta rules for specific stocks
STRICT_DELTA = {
    "PLTR": (0.20, 0.25),   # Extreme valuation, stricter
    "TSLA": (0.20, 0.28),   # High volatility, be selective
    "BABA": (0.15, 0.25),   # Geopolitical risk
}

# IVP minimums by strategy (overrides global defaults for specific stocks)
STRICT_IVP = {
    "PLTR": 40,    # Needs elevated IV to justify premium selling
    "BABA": 35,
}

# Options income priority — these 6 have best liquidity/volatility structure
# Scanner scores these higher automatically
OPTIONS_INCOME_PRIORITY = {"NVDA", "MSFT", "AMZN", "TSLA", "META", "NFLX"}


def position_check(ticker: str, ibkr: dict) -> dict:
    """
    Check current position size and return sizing info.
    Returns: status, current_pct, max_pct, room_usd, quantity, tier, avg_cost
    """
    TIER_MAX = {"Core": 8.0, "Growth": 5.0, "Cyclical": 4.0, "Opportunistic": 2.5}

    # Determine tier
    tier = "Core"
    if ticker in CORE_STOCKS:        tier = "Core"
    elif ticker in GROWTH_STOCKS:    tier = "Growth"
    elif ticker in CYCLICAL_STOCKS:  tier = "Cyclical"
    else:                            tier = "Opportunistic"

    max_pct     = TIER_MAX.get(tier, 2.5)
    max_usd     = PORTFOLIO_SIZE * max_pct / 100

    # Look up current position from IBKR/Schwab
    pos         = ibkr.get(ticker, {})
    qty         = float(pos.get("qty", 0) or 0)
    avg_cost    = float(pos.get("avg_cost", 0) or 0)
    mkt_val     = float(pos.get("market_value", qty * avg_cost) or 0)

    current_pct = round(mkt_val / PORTFOLIO_SIZE * 100, 2) if PORTFOLIO_SIZE > 0 else 0
    room_usd    = max(0, max_usd - mkt_val)

    if current_pct >= max_pct:
        status = "OVERWEIGHT"
    elif current_pct >= max_pct * 0.8:
        status = "NEAR_MAX"
    elif current_pct >= max_pct * 0.5:
        status = "NORMAL"
    else:
        status = "LIGHT"

    return {
        "ticker":      ticker,
        "tier":        tier,
        "status":      status,
        "current_pct": current_pct,
        "max_pct":     max_pct,
        "max_usd":     round(max_usd, 0),
        "room_usd":    round(room_usd, 0),
        "quantity":    qty,
        "avg_cost":    avg_cost,
        "market_value": mkt_val,
    }


def stock_quality_check(ticker: str, md: dict, earn_date) -> dict:
    """
    Quality gate for all strategies.
    Returns dict with: passes, hard_stop, leaps_hard_stop, quality_score,
    pullback, pullback_pct, warnings, checks, earnings_status, near_high,
    ma50_extended, pct_above_ma50, days_to_earnings.
    """
    price     = md.get("price", 0)
    w52_high  = md.get("w52_high", price * 1.3)
    w52_low   = md.get("w52_low",  price * 0.7)
    ma50      = md.get("ma50",  price)
    ma200     = md.get("ma200", price * 0.95)
    volume    = md.get("volume", 0)
    avg_vol   = md.get("avg_volume", volume)

    # ── Price / volume checks ────────────────────────────
    price_ok  = price >= 10
    volume_ok = volume >= 50_000 or avg_vol >= 50_000

    # ── Gap / spike check ────────────────────────────────
    day_change = md.get("day_change_pct", 0)
    no_gap     = abs(day_change) < 0.08   # >8% move = gap risk

    # ── 52-week high proximity ────────────────────────────
    pir        = position_in_range(price, w52_low, w52_high)
    near_high  = pir > 0.92               # within 8% of 52w high
    pullback   = pullback_from_high(price, w52_high)

    # ── MA50 extension check ──────────────────────────────
    pct_above_ma50 = (price - ma50) / ma50 if ma50 > 0 else 0
    ma50_extended  = pct_above_ma50 > 0.08  # >8% above 50MA

    # ── MA200 check ───────────────────────────────────────
    above_ma200 = price >= ma200 * 0.97   # within 3% tolerance

    # ── Earnings check ────────────────────────────────────
    days_to_earnings = 999
    if earn_date:
        try:
            delta = (earn_date - datetime.now()).days
            days_to_earnings = max(0, delta)
        except:
            pass

    if days_to_earnings < 14:
        earnings_status = "hard_stop"
    elif days_to_earnings < 21:
        earnings_status = "warning"
    else:
        earnings_status = "ok"

    # ── Build checks dict ─────────────────────────────────
    checks = {
        "price_ok":      price_ok,
        "volume_ok":     volume_ok,
        "no_gap":        no_gap,
        "not_near_high": not near_high,
        "ma50_ok":       not ma50_extended,
        "above_ma200":   above_ma200,
    }

    # ── Quality score 0-5 ─────────────────────────────────
    quality_score = sum([
        1 if price_ok else 0,
        1 if volume_ok else 0,
        1 if no_gap else 0,
        1 if not near_high else 0,
        1 if not ma50_extended else 0,
    ])

    # ── Warnings ──────────────────────────────────────────
    warnings = []
    if near_high:          warnings.append("Near 52w high")
    if ma50_extended:      warnings.append(">8% above MA50")
    if not above_ma200:    warnings.append("Below 200MA")
    if earnings_status == "warning": warnings.append(f"Earnings in {days_to_earnings}d")
    if not no_gap:         warnings.append(f"Gap/spike {day_change:.1%}")

    # ── Hard stops ────────────────────────────────────────
    hard_stop = (
        not price_ok or
        not volume_ok or
        not no_gap or
        near_high or
        ma50_extended or
        earnings_status == "hard_stop"
    )

    # LEAPS hard stop is more lenient — only earnings and price matter
    leaps_hard_stop = (
        not price_ok or
        not volume_ok or
        earnings_status == "hard_stop"
    )

    return {
        "checks":           checks,
        "quality_score":    quality_score,
        "pullback":         pullback,
        "pullback_pct":     round(pullback * 100, 1),
        "days_to_earnings": days_to_earnings,
        "earnings_status":  earnings_status,
        "pct_above_ma50":   round(pct_above_ma50 * 100, 1),
        "ma50_extended":    ma50_extended,
        "near_high":        near_high,
        "above_ma200":      above_ma200,
        "warnings":         warnings,
        "hard_stop":        hard_stop,
        "leaps_hard_stop":  leaps_hard_stop,
        "passes":           not hard_stop and quality_score >= 3,
    }


def get_position_status(current_pct: float) -> str:
    """
    Determine position status based on current exposure %.
    Drives delta selection for Position Income Optimization.
    """
    if current_pct < 4:
        return "light"        # aggressive income — 0.30-0.40 delta
    elif current_pct < 8:
        return "normal"       # normal — 0.20-0.25 delta
    elif current_pct < 12:
        return "heavy"        # conservative — 0.10-0.15 delta
    else:
        return "overweight"   # reduce — sell closer calls, willing to assign


def get_pnl_status(avg_cost: float, current_price: float) -> str:
    """Determine P&L status for delta selection."""
    if avg_cost <= 0:
        return "unknown"
    pnl_pct = (current_price - avg_cost) / avg_cost
    if pnl_pct > 0.10:
        return "profit"       # delta 0.30-0.40
    elif pnl_pct > -0.05:
        return "breakeven"    # delta 0.20-0.25
    else:
        return "loss"         # delta 0.10-0.15


def find_position_income_cc(ticker, price, qty, avg_cost, contracts,
                             ivdata, position_status, pnl_status) -> tuple:
    """
    Position Income Optimization — Mode 4.
    Sell calls on existing holdings to generate income.
    Rules from framework:
    - Sell calls ABOVE break-even (cost basis - premiums collected)
    - Delta based on P&L status:
      profit    → 0.30-0.40 (more aggressive, happy to reduce)
      breakeven → 0.20-0.25 (protect position)
      loss      → 0.10-0.15 (very conservative, protect recovery)
    - Ignores: 200MA rule, pullback requirements
    - Focus: income vs risk, not trade perfection
    - Only for stocks where you hold ≥100 shares
    """
    if qty < 100:
        return None, {}

    # Delta range based on P&L status
    if pnl_status == "profit":
        d_min, d_max = 0.30, 0.40
        status_label = "📈 Profit position"
    elif pnl_status == "loss":
        d_min, d_max = 0.10, 0.15
        status_label = "📉 Loss position — conservative"
    else:
        d_min, d_max = 0.20, 0.25
        status_label = "➡️ Near break-even"

    atm_iv     = ivdata.get("atm_iv", 0.3)
    breakeven  = avg_cost  # simplified — would subtract collected premiums
    candidates = []

    for c in contracts:
        if c.get("option_type") != "C":
            continue
        try:
            expiry = datetime.strptime(c["expiry"], "%Y-%m-%d")
            dte    = (expiry - datetime.now()).days
            if not (30 <= dte <= 45):
                continue

            strike = float(c["strike"])
            # Must be above break-even
            if strike <= breakeven:
                continue
            if strike <= price:
                continue  # must be OTM

            bid    = float(c.get("nbbo_bid", 0) or 0)
            ask    = float(c.get("nbbo_ask", 0) or 0)
            mid    = (bid + ask) / 2
            if mid < 0.50: continue

            delta  = float(c.get("delta", 0) or 0)
            if abs(delta) == 0:
                delta = estimate_delta(price, strike, dte, atm_iv, "C")
            if delta is None: continue
            delta = abs(delta)
            if not (d_min <= delta <= d_max):
                continue

            # Liquidity
            oi     = int(c.get("open_interest", 0) or 0)
            vol    = int(c.get("volume", 0) or 0)
            spread = (ask - bid) / ask if ask > 0 else 1
            if oi < MIN_OPEN_INTEREST: continue
            if vol < MIN_DAILY_VOLUME: continue
            if spread > MAX_BID_ASK_SPREAD: continue

            annualized    = (mid / price) * (365 / dte) * 100
            max_contracts = max(1, int(qty / 100))
            prem_pct      = round(mid / strike * 100, 2)

            score = delta * mid * (ivdata["ivp"] / 50)
            candidates.append({
                "strike":            strike,
                "expiry":            expiry.strftime("%Y-%m-%d"),
                "dte":               dte,
                "bid":               round(bid, 2),
                "ask":               round(ask, 2),
                "premium":           round(mid, 2),
                "delta":             round(delta, 2),
                "annualized_return": round(annualized, 1),
                "prem_pct":          prem_pct,
                "max_contracts":     max_contracts,
                "avg_cost":          avg_cost,
                "breakeven":         round(breakeven, 2),
                "status_label":      status_label,
                "pnl_status":        pnl_status,
                "score":             score,
            })
        except:
            continue

    if not candidates:
        return None, {}

    best = max(candidates, key=lambda x: x["score"])
    return best, {"signal": f"✅ Position Income | {status_label}"}


def detect_price_drop(ticker: str, md: dict) -> dict:
    """
    Detect if a stock has dropped 8%+ recently — triggers Post-Drop CSP mode.
    Opposite of spike detection.
    Checks today's move AND position relative to MA50 (extended below = recent drop).
    """
    price          = md.get("price", 0)
    prev_close     = md.get("prev_close", price)
    day_change     = (price - prev_close) / prev_close if prev_close > 0 else 0
    ma50           = md.get("ma50", 0)
    ma200          = md.get("ma200", 0)
    pct_above_ma50 = md.get("pct_above_ma50", 0)
    above_ma200    = md.get("above_ma200", True)

    today_drop     = day_change <= -DROP_TRIGGER_MIN        # fell 8%+ today
    recent_drop    = pct_above_ma50 <= -DROP_TRIGGER_MIN    # below 50MA significantly

    return {
        "is_drop":        today_drop or recent_drop,
        "today_change":   round(day_change * 100, 1),
        "pct_below_ma50": round(pct_above_ma50 * 100, 1),
        "above_ma200":    above_ma200,
        "trigger":        "today" if today_drop else "recent" if recent_drop else "none",
    }


def find_drop_csp(ticker, price, contracts, ivdata, pir, quality,
                  drop_info, tier, sizing) -> tuple:
    """
    Post-Drop CSP — sell fear after sharp downside move.

    Key differences from Income CSP:
    - Gap filter DISABLED (drop is the trigger)
    - Near-high restriction DISABLED
    - More conservative delta (0.20-0.25)
    - Reduced position size (60% of normal)
    - Must be above 200MA (no broken stocks)
    - Only Core and Growth tier stocks
    - IVP > 40 required (elevated fear = elevated premium)
    - Strike below real support (MA50 or swing low)
    """
    # Quality gates
    if not drop_info["above_ma200"]:
        return None, {"signal": "❌ Below 200MA — skip (structurally broken)"}
    if tier not in DROP_CSP_ALLOWED_TIERS:
        return None, {"signal": f"❌ {tier} tier not allowed for post-drop CSP"}
    if ivdata["ivp"] < DROP_IVP_MIN:
        return None, {"signal": f"❌ IVP {ivdata['ivp']:.0f}% below {DROP_IVP_MIN}% minimum"}
    if (quality.get("days_to_earnings") is not None
            and 0 < quality["days_to_earnings"] <= DROP_EARNINGS_MIN):
        return None, {"signal": f"❌ Earnings in {quality['days_to_earnings']}d — hard stop"}

    atm_iv     = ivdata.get("atm_iv", 0.3)
    ma50       = 0  # will use strike selection logic
    candidates = []

    for c in contracts:
        if c.get("option_type") != "P":
            continue
        try:
            expiry = datetime.strptime(c["expiry"], "%Y-%m-%d")
            dte    = (expiry - datetime.now()).days
            if not (DROP_CSP_DTE_MIN <= dte <= DROP_CSP_DTE_MAX):
                continue

            strike = float(c["strike"])
            if strike >= price: continue  # must be OTM

            bid    = float(c.get("nbbo_bid", 0) or 0)
            ask    = float(c.get("nbbo_ask", 0) or 0)
            mid    = (bid + ask) / 2
            if mid < 1.0: continue

            # Use real delta from Schwab if available
            delta  = float(c.get("delta", 0) or 0)
            if abs(delta) == 0:
                delta = estimate_delta(price, strike, dte, atm_iv, "P")
            if delta is None: continue
            delta = abs(delta)
            if not (DROP_CSP_DELTA_MIN <= delta <= DROP_CSP_DELTA_MAX):
                continue

            # Liquidity
            oi     = int(c.get("open_interest", 0) or 0)
            vol    = int(c.get("volume", 0) or 0)
            spread = (ask - bid) / ask if ask > 0 else 1
            if oi < MIN_OPEN_INTEREST: continue
            if vol < MIN_DAILY_VOLUME: continue
            if spread > MAX_BID_ASK_SPREAD: continue

            # Premium efficiency
            prem_pct = mid / strike
            if prem_pct < MIN_PREMIUM_PCT_30_45: continue

            annualized  = (mid / strike) * (365 / dte) * 100

            # Reduced position size — 60% of normal
            normal_size = PORTFOLIO_SIZE * get_max_alloc(ticker)
            reduced_size = normal_size * DROP_SIZE_FACTOR
            max_contracts = max(1, int(reduced_size / (strike * 100)))

            # Extrinsic quality label
            intrinsic   = max(0, strike - price)
            extrinsic   = max(0, mid - intrinsic)
            ext_pct     = (extrinsic / mid * 100) if mid > 0 else 0

            score = (ivdata["ivp"] / 100) * delta * mid * (1 - abs(dte-35)/35)
            candidates.append({
                "strike":          strike,
                "expiry":          expiry.strftime("%Y-%m-%d"),
                "dte":             dte,
                "bid":             round(bid, 2),
                "ask":             round(ask, 2),
                "premium":         round(mid, 2),
                "delta":           round(delta, 2),
                "annualized_return": round(annualized, 1),
                "prem_pct":        round(prem_pct * 100, 2),
                "max_contracts":   max_contracts,
                "collateral":      round(strike * 100 * max_contracts, 0),
                "score":           score,
            })
        except:
            continue

    if not candidates:
        return None, {}

    best = max(candidates, key=lambda x: x["score"])
    return best, {"signal": f"✅ Post-Drop CSP | {drop_info['today_change']:+.1f}% | IVP {ivdata['ivp']:.0f}%"}


def detect_price_spike(ticker: str, md: dict) -> dict:
    """
    Detect if a stock has spiked 8%+ upward recently.
    Opportunistic mode is TRIGGERED by spikes (opposite of income mode which skips them).
    """
    price          = md.get("price", 0)
    prev_close     = md.get("prev_close", price)
    day_change     = (price - prev_close) / prev_close if prev_close > 0 else 0
    pct_above_ma50 = md.get("pct_above_ma50", 0)

    today_spike   = day_change >= OPP_SPIKE_MIN        # big move today
    extended_run  = pct_above_ma50 >= OPP_SPIKE_MIN    # stock has run up recently

    return {
        "is_spike":       today_spike or extended_run,
        "today_change":   round(day_change * 100, 1),
        "pct_above_ma50": round(pct_above_ma50 * 100, 1),
        "trigger":        "today" if today_spike else "extended" if extended_run else "none",
    }


def find_spike_cc(ticker, price, qty, avg_cost, contracts, ivdata, spike_info) -> tuple:
    """
    Opportunistic CC after volatility spike.
    Rules from framework doc:
    - DTE: 14-30 (shorter — capture fast vol contraction)
    - Delta: 0.20-0.35 (strikes above spike high)
    - IVP > 40 required
    - Earnings hard stop: 7 days
    - Must hold shares (qty >= 100)
    """
    if qty < 100:
        return None, {}
    if ivdata["ivp"] < OPP_IVP_MIN:
        return None, {"signal": f"IVP {ivdata['ivp']:.0f}% < {OPP_IVP_MIN}% minimum for spike CC"}

    atm_iv     = ivdata.get("atm_iv", 0.3)
    candidates = []

    for c in contracts:
        if c.get("option_type") != "C":
            continue
        try:
            expiry = datetime.strptime(c["expiry"], "%Y-%m-%d")
            dte    = (expiry - datetime.now()).days
            if not (OPP_CC_DTE_MIN <= dte <= OPP_CC_DTE_MAX):
                continue
            strike = float(c["strike"])
            if strike <= price: continue  # must be OTM
            bid    = float(c.get("nbbo_bid", 0) or 0)
            ask    = float(c.get("nbbo_ask", 0) or 0)
            mid    = (bid + ask) / 2
            if mid < 0.50: continue

            delta = float(c.get("delta", 0) or 0)
            if delta == 0:
                delta = estimate_delta(price, strike, dte, atm_iv, "C")
            if delta is None or not (OPP_CC_DELTA_MIN <= abs(delta) <= OPP_CC_DELTA_MAX):
                continue

            oi     = int(c.get("open_interest", 0) or 0)
            vol    = int(c.get("volume", 0) or 0)
            spread = (ask - bid) / ask if ask > 0 else 1
            if oi < MIN_OPEN_INTEREST: continue
            if vol < MIN_DAILY_VOLUME: continue
            if spread > MAX_BID_ASK_SPREAD: continue

            # CC: use strike as collateral basis (matches CSP formula)
            annualized     = (mid / strike) * (365 / dte) * 100
            protection_pct = round((mid / price) * 100, 1)
            max_contracts  = max(1, int(qty / 100))
            score          = (ivdata["ivp"] / 100) * abs(delta) * mid

            candidates.append({
                "strike":            strike,
                "expiry":            expiry.strftime("%Y-%m-%d"),
                "dte":               dte,
                "bid":               round(bid, 2),
                "ask":               round(ask, 2),
                "premium":           round(mid, 2),
                "delta":             round(abs(delta), 2),
                "annualized_return": round(annualized, 1),
                "max_contracts":     max_contracts,
                "avg_cost":          avg_cost,
                "protection_pct":    protection_pct,
                "score":             score,
            })
        except:
            continue

    if not candidates:
        return None, {}

    best = max(candidates, key=lambda x: x["score"])
    return best, {"signal": f"✅ Spike CC | {spike_info['today_change']:+.1f}% move | IVP {ivdata['ivp']:.0f}%"}


def timing_score(strategy, pir, ivp, is_spec=False, ivp_override=None) -> dict:
    """
    Score timing quality for a trade setup.
    Returns dict with: score (0-100), recommend (bool), signal (str)
    strategy: CSP, CC, LEAPS, PMCC, BCS
    pir: position in 52w range (0=at low, 1=at high)
    ivp: IV percentile (0-100)
    """
    effective_ivp_min = ivp_override if ivp_override else IVP_MIN_SELL
    if strategy in ("CSP","CC") and ivp < effective_ivp_min:
        return {"score":10,"recommend":False,
                "signal":f"❌ SKIP — IVP {ivp:.0f}% below minimum ({effective_ivp_min})"}

    high_ivp  = ivp >= IVP_MIN_SELL
    near_low  = pir < 0.30
    near_high = pir > 0.70
    spec = " (use wider strikes — speculative)" if is_spec else ""

    if strategy == "CSP":
        if high_ivp and near_low:
            return {"score":95,"recommend":True,
                    "signal":f"🔥 EXCELLENT — IVP {ivp:.0f}% + near 52w low{spec}"}
        elif high_ivp and not near_high:
            return {"score":80,"recommend":True,
                    "signal":f"✅ GOOD — IVP {ivp:.0f}%, healthy price level{spec}"}
        elif high_ivp and near_high:
            return {"score":55,"recommend":True,
                    "signal":f"⚠️ CAUTION — IVP {ivp:.0f}% but near 52w high{spec}"}
        else:
            return {"score":20,"recommend":False,
                    "signal":f"❌ SKIP — IVP {ivp:.0f}% too low for premium selling"}

    elif strategy == "CC":
        if near_low:
            return {"score":5,"recommend":False,
                    "signal":"❌ AVOID — Never sell CC near 52w low (limits upside in recovery)"}
        elif near_high and high_ivp:
            return {"score":90,"recommend":True,
                    "signal":f"🔥 EXCELLENT — Near highs + IVP {ivp:.0f}%"}
        elif near_high:
            return {"score":75,"recommend":True,
                    "signal":f"✅ GOOD — Stock near highs, income opportunity"}
        elif high_ivp:
            return {"score":65,"recommend":True,
                    "signal":f"✅ OK — IVP {ivp:.0f}% boosts CC premium"}
        else:
            return {"score":30,"recommend":False,
                    "signal":f"⚠️ WEAK — IVP {ivp:.0f}% too low for CC"}

    elif strategy == "LEAPS":
        very_cheap = ivp < 30
        cheap      = ivp <= 50
        expensive  = ivp > 50

        if is_spec and near_high:
            return {"score":5,"recommend":False,
                    "signal":"❌ AVOID — Speculative + near highs, wait for bigger drawdown"}
        elif expensive and near_high:
            return {"score":5,"recommend":False,
                    "signal":f"❌ POOR — IVP {ivp:.0f}% expensive + near highs"}
        elif expensive and not near_low:
            return {"score":20,"recommend":False,
                    "signal":f"❌ SKIP — IVP {ivp:.0f}% too expensive to buy LEAPS (want <50%)"}
        elif very_cheap and near_low:
            return {"score":98,"recommend":True,
                    "signal":f"🔥 EXCEPTIONAL — IVP {ivp:.0f}% (very cheap) + near 52w low"}
        elif very_cheap and not near_high:
            return {"score":85,"recommend":True,
                    "signal":f"🔥 EXCELLENT — IVP {ivp:.0f}% very cheap LEAPS"}
        elif cheap and near_low:
            return {"score":82,"recommend":True,
                    "signal":f"✅ GOOD — IVP {ivp:.0f}% + near 52w low = solid LEAPS entry"}
        elif cheap and not near_high:
            return {"score":68,"recommend":True,
                    "signal":f"✅ ACCEPTABLE — IVP {ivp:.0f}%, reasonable LEAPS entry"}
        elif expensive and near_low:
            return {"score":45,"recommend":True,
                    "signal":f"⚠️ MIXED — Near 52w low but IVP {ivp:.0f}% makes options pricey"}
        else:
            return {"score":25,"recommend":False,
                    "signal":f"⚠️ WEAK — IVP {ivp:.0f}% elevated + not near lows"}

    elif strategy == "PMCC":
        if near_low:
            return {"score":5,"recommend":False,
                    "signal":"❌ AVOID — Don't sell calls near 52w lows"}
        elif high_ivp:
            return {"score":82,"recommend":True,
                    "signal":f"✅ GOOD — IVP {ivp:.0f}% gives fat PMCC premium"}
        else:
            return {"score":40,"recommend":False,
                    "signal":f"⚠️ WEAK — Low IVP {ivp:.0f}% for PMCC short call"}

    elif strategy == "BCS":
        if near_low and not high_ivp:
            return {"score":88,"recommend":True,
                    "signal":f"✅ GOOD — Near lows + IVP {ivp:.0f}%, directional setup"}
        elif near_low and high_ivp:
            return {"score":70,"recommend":True,
                    "signal":f"✅ OK — Near lows but IVP {ivp:.0f}% makes spread wider"}
        elif near_high:
            return {"score":20,"recommend":False,
                    "signal":"❌ AVOID — Don't buy bull spreads near 52w highs"}
        else:
            return {"score":55,"recommend":True,
                    "signal":f"✅ NEUTRAL — Mid-range entry, moderate setup"}

    return {"score":50,"recommend":True,"signal":"—"}


def get_max_alloc(ticker: str) -> float:
    """Return max allocation as decimal for a ticker based on tier."""
    if ticker in CORE_STOCKS:        return 0.08
    elif ticker in GROWTH_STOCKS:    return 0.05
    elif ticker in CYCLICAL_STOCKS:  return 0.04
    else:                            return 0.025


def find_best_csp(ticker, price, contracts, ivdata, pir, quality) -> tuple:
    """
    Find best cash-secured put opportunity.
    Returns (csp_dict, timing_dict) or (None, {})
    """
    if not contracts: return None, {}

    atm_iv     = ivdata.get("atm_iv", 0.3)
    candidates = []

    for c in contracts:
        if c.get("option_type") != "P":
            continue
        try:
            opt_type = c.get("option_type", "P")
            if opt_type != "P": continue
            expiry = datetime.strptime(c["expiry"], "%Y-%m-%d")
            dte    = (expiry - datetime.now()).days
            if not (CSP_MIN_DTE <= dte <= CSP_MAX_DTE):
                continue

            strike = float(c["strike"])
            if strike <= 0 or strike >= price: continue  # must be OTM

            bid    = float(c.get("nbbo_bid", 0) or 0)
            ask    = float(c.get("nbbo_ask", 0) or 0)
            mid    = (bid + ask) / 2
            if mid < 0.10: continue

            # Use real delta from Schwab if available
            delta  = float(c.get("delta", 0) or 0)
            if abs(delta) == 0:
                delta = estimate_delta(price, strike, dte, atm_iv, "P")
            if delta is None: continue
            delta = abs(delta)

            # PLTR stricter delta
            d_min = CSP_DELTA_PLTR_MIN if ticker == "PLTR" else CSP_DELTA_MIN
            d_max = CSP_DELTA_PLTR_MAX if ticker == "PLTR" else CSP_DELTA_MAX
            if ivdata["ivp"] > 50: d_max = CSP_DELTA_HIGH_IVP_MAX
            if not (d_min <= delta <= d_max): continue

            # Liquidity checks
            oi     = int(c.get("open_interest", 0) or 0)
            vol    = int(c.get("volume", 0) or 0)
            spread = (ask - bid) / ask if ask > 0 else 1
            if oi  < MIN_OPEN_INTEREST:    continue
            if vol < MIN_DAILY_VOLUME:     continue
            if spread > MAX_BID_ASK_SPREAD: continue

            # Annualized return — matches spreadsheet formula
            annualized    = (mid / strike) * (365 / dte) * 100
            below_min     = annualized < CSP_MIN_ANNUALIZED
            if annualized > MAX_ANNUALIZED: continue  # filter bad data
            if annualized < 5: continue  # reject truly garbage premiums

            otm_pct       = round((price - strike) / price * 100, 1)
            max_contracts = max(1, int(PORTFOLIO_SIZE * get_max_alloc(ticker) / (strike * 100)))
            collateral    = strike * 100 * max_contracts
            prem_pct      = round(mid / strike * 100, 2)

            iv   = round(float(c.get("iv", 0) or atm_iv) * 100, 1)
            timing = timing_score("CSP", pir, ivdata["ivp"])
            # score lower if timing doesn't recommend — but still include
            score = timing["score"] * delta * annualized
            candidates.append({
                "strike":            strike,
                "expiry":            expiry.strftime("%Y-%m-%d"),
                "dte":               dte,
                "bid":               round(bid, 2),
                "ask":               round(ask, 2),
                "premium":           round(mid, 2),
                "delta":             round(delta, 2),
                "iv":                iv,
                "ivp":               round(ivdata["ivp"], 1),
                "otm_pct":           otm_pct,
                "annualized_return": round(annualized, 1),
                "below_min":         below_min,
                "max_contracts":     max_contracts,
                "collateral":        round(collateral, 0),
                "prem_pct":          prem_pct,
                "timing":            timing,
                "score":             score,
            })
        except Exception:
            continue

    if not candidates:
        return None, {}

    best = max(candidates, key=lambda x: x["score"])
    return best, best["timing"]


def find_best_cc(ticker, price, qty, avg_cost, contracts, ivdata, pir):
    timing = timing_score("CC", pir, ivdata["ivp"])
    if not contracts or price <= 0 or qty < 100: return None, timing
    # Note: timing["recommend"] is advisory only — dashboard shows all

    atm_iv        = ivdata["iv_current"]
    today         = datetime.now()
    max_contracts = int(qty / 100)
    best          = None; best_score = 0

    for c in contracts:
        try:
            opt_type = c.get("option_type", "")
            if opt_type != "C": continue
            strike = float(c.get("strike", 0) or 0)
            if strike <= 0: continue
            expiry = datetime.strptime(c["expiry"], "%Y-%m-%d")
            dte = (expiry - today).days
            if not (CC_DTE_MIN <= dte <= CC_DTE_MAX): continue
        except Exception: continue
        otm_pct = (strike - price) / price * 100
        if not (1 <= otm_pct <= 15): continue
        if avg_cost > 0 and strike < avg_cost * 1.01: continue  # protect cost basis
        bid = float(c.get("nbbo_bid",0) or 0)
        ask = float(c.get("nbbo_ask",0) or 0)
        mid = (bid + ask) / 2
        if mid < 1.0: continue
        # Liquidity filter
        oi_val  = int(c.get("open_interest", 0) or 0)
        vol_val = int(c.get("volume", 0) or 0)
        spread  = (ask - bid) / ask if ask > 0 else 1
        if oi_val < MIN_OPEN_INTEREST: continue
        if vol_val < MIN_DAILY_VOLUME: continue
        if spread > MAX_BID_ASK_SPREAD: continue
        delta = estimate_delta(price, strike, dte, atm_iv, "C")
        if delta is None or not (CC_DELTA_MIN <= delta <= CC_DELTA_MAX): continue
        annualized = (mid / price) * (365 / dte) * 100
        if annualized > MAX_ANNUALIZED: continue  # filter bad data only
        if annualized < 3: continue  # reject truly garbage premiums
        score = (timing["score"]/100) * mid * (1 + atm_iv) * (1 - abs(dte-37)/37)
        if score > best_score:
            best_score = score
            best = {"strike":strike,"expiry":expiry.strftime("%Y-%m-%d"),"dte":dte,
                    "bid":round(bid,2),"ask":round(ask,2),"premium":round(mid,2),
                    "otm_pct":round(otm_pct,1),"iv":round(atm_iv*100,1),
                    "ivp":ivdata["ivp"],"delta":delta,
                    "annualized_return":round(annualized,1),
                    "max_contracts":max_contracts,"avg_cost":round(avg_cost,2),
                    "timing":timing}
    return best, timing


def find_best_leaps(ticker, price, contracts, ivdata, pir):
    """Deep ITM LEAPS — delta 0.80-0.90, minimize extrinsic."""
    is_spec = ticker in SPECULATIVE
    timing  = timing_score("LEAPS", pir, ivdata["ivp"], is_spec)
    if not timing["recommend"]: return None, timing
    if not contracts or price <= 0: return None, timing

    atm_iv = ivdata["iv_current"]
    today  = datetime.now()
    candidates = []

    for c in contracts:
        try:
            opt_type = c.get("option_type", "")
            if opt_type != "C": continue
            strike = float(c.get("strike", 0) or 0)
            if strike <= 0: continue
            expiry = datetime.strptime(c["expiry"], "%Y-%m-%d")
            dte = (expiry - today).days
            if dte < LEAPS_DTE_MIN: continue
        except Exception: continue
        itm_pct = (price - strike) / price * 100  # positive = ITM
        if not (-5 <= itm_pct <= 35): continue
        bid = float(c.get("nbbo_bid",0) or 0)
        ask = float(c.get("nbbo_ask",0) or 0)
        mid = (bid + ask) / 2
        if mid < 5.0: continue
        delta = estimate_delta(price, strike, dte, atm_iv, "C")
        if delta is None or not (LEAPS_DELTA_MIN <= delta <= 0.98): continue
        intrinsic     = max(0, price - strike)
        extrinsic     = max(0, mid - intrinsic)
        extrinsic_pct = (extrinsic / mid * 100) if mid > 0 else 100
        if extrinsic_pct > LEAPS_EXTRINSIC_MAX: continue  # reject >25% extrinsic

        # Extrinsic quality label (from framework)
        if extrinsic_pct < 10:
            ext_label = f"🔥 Excellent ({extrinsic_pct:.1f}%)"
        elif extrinsic_pct < 20:
            ext_label = f"✅ Good ({extrinsic_pct:.1f}%)"
        elif extrinsic_pct < 30:
            ext_label = f"⚠️ Acceptable ({extrinsic_pct:.1f}%)"
        else:
            ext_label = f"❌ Too expensive ({extrinsic_pct:.1f}%)"

        # Score heavily penalizes high extrinsic — prefers <20% target
        # <10%: full score, 10-20%: slight penalty, 20-30%: significant penalty
        if extrinsic_pct < 10:
            ext_score = 30
        elif extrinsic_pct < 20:
            ext_score = 20
        elif extrinsic_pct < 30:
            ext_score = 8
        else:
            ext_score = 0

        delta_score = delta * 40
        score       = (timing["score"]/100) * (delta_score + ext_score) * (dte/365)
        candidates.append({
            "strike":strike,"expiry":expiry.strftime("%Y-%m-%d"),"dte":dte,
            "bid":round(bid,2),"ask":round(ask,2),"premium":round(mid,2),
            "itm_pct":round(itm_pct,1),"delta":delta,
            "intrinsic":round(intrinsic,2),"extrinsic":round(extrinsic,2),
            "extrinsic_pct":round(extrinsic_pct,1),
            "ext_label":ext_label,
            "iv":round(atm_iv*100,1),"ivp":ivdata["ivp"],
            "leverage":round(price/mid,1) if mid > 0 else 0,
            "timing":timing,"score":score
        })

    if not candidates: return None, timing
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[0], timing


def find_pmcc_short_call(ticker, price, existing_leaps, contracts, ivdata, pir):
    """Find best short call to sell against an existing LEAPS position."""
    timing = timing_score("PMCC", pir, ivdata["ivp"])
    if not timing["recommend"]: return None, timing
    if not existing_leaps or not contracts: return None, timing

    atm_iv      = ivdata["iv_current"]
    today       = datetime.now()
    leaps_strike= existing_leaps["strike"]
    best        = None; best_score = 0

    for c in contracts:
        try:
            opt_type = c.get("option_type", "")
            if opt_type != "C": continue
            strike = float(c.get("strike", 0) or 0)
            if strike <= 0: continue
            expiry = datetime.strptime(c["expiry"], "%Y-%m-%d")
            dte = (expiry - today).days
            if not (CC_DTE_MIN <= dte <= CC_DTE_MAX): continue
        except Exception: continue
        # Short call must be below LEAPS strike (spread protection)
        if strike >= leaps_strike: continue
        otm_pct = (strike - price) / price * 100
        if not (1 <= otm_pct <= 15): continue
        bid = float(c.get("nbbo_bid",0) or 0)
        ask = float(c.get("nbbo_ask",0) or 0)
        mid = (bid + ask) / 2
        if mid < 0.50: continue
        delta = estimate_delta(price, strike, dte, atm_iv, "C")
        if delta is None or not (CC_DELTA_MIN <= delta <= CC_DELTA_MAX): continue
        # Annualized based on LEAPS cost
        leaps_cost = existing_leaps["avg_cost"] * 100
        if leaps_cost > 0:
            months_to_recover = leaps_cost / (mid * 100 * existing_leaps["quantity"])
        else:
            months_to_recover = 999
        score = (timing["score"]/100) * mid * (1 + atm_iv)
        if score > best_score:
            best_score = score
            annualized = (mid / price) * (365 / dte) * 100
            best = {"strike":strike,"expiry":expiry.strftime("%Y-%m-%d"),"dte":dte,
                    "bid":round(bid,2),"ask":round(ask,2),"premium":round(mid,2),
                    "otm_pct":round(otm_pct,1),"delta":delta,
                    "iv":round(atm_iv*100,1),"ivp":ivdata["ivp"],
                    "annualized_return":round(annualized,1),
                    "leaps_strike":leaps_strike,
                    "months_to_recover":round(months_to_recover,1) if months_to_recover < 100 else "N/A",
                    "max_contracts":existing_leaps["quantity"],
                    "timing":timing}
    return best, timing


def find_bull_call_spread(ticker, price, contracts, ivdata, pir, quality):
    """
    Bull Call Spread — Final Rules:
    - DTE: 45-120 days (min 45, preferred 60-120)
    - IVP: 20-80 (reject <20 or >80)
    - Short call delta: 0.25-0.40
    - Spread width: min $10 or 1-2% of stock price
    - Distance to short strike: 3-5% at 45-60 DTE, 5-8% at 60-90, 8-10% at 90-120
    - ROR: min 80%, preferred 80-200% (not driven by short DTE)
    - Trend filter: stock above 50 DMA OR within 20% of 52w low
    """
    timing = timing_score("BCS", pir, ivdata["ivp"])
    # IVP filter: reject <20 or >80
    if ivdata["ivp"] < 20 or ivdata["ivp"] > 80: return None, {"score":0,"recommend":False,"signal":f"❌ IVP {ivdata['ivp']:.0f}% out of range (need 20-80)"}
    if not timing["recommend"]: return None, timing
    if not contracts or price <= 0: return None, timing
    # Trend filter: above 50 DMA or within 20% of 52w low
    pct_above_ma50 = quality.get("pct_above_ma50", 0)
    pullback = quality.get("pullback_pct", 0)
    trend_ok = pct_above_ma50 >= 0 or pullback >= 80  # above 50MA or near 52w low
    if not trend_ok: return None, {"score":0,"recommend":False,"signal":"❌ Below 50 DMA and not near 52w low"}

    atm_iv = ivdata["iv_current"]
    today  = datetime.now()
    best   = None; best_score = 0

    # Get calls in 30-60 DTE range
    calls = []
    for c in contracts:
        try:
            opt_type = c.get("option_type", "")
            if opt_type != "C": continue
            strike = float(c.get("strike", 0) or 0)
            if strike <= 0: continue
            expiry = datetime.strptime(c["expiry"], "%Y-%m-%d")
            dte = (expiry - today).days
        except Exception: continue
        if not (45 <= dte <= 120): continue  # min 45, preferred 60-120
        bid = float(c.get("nbbo_bid",0) or 0)
        ask = float(c.get("nbbo_ask",0) or 0)
        mid = (bid + ask) / 2
        if mid < 0.10: continue
        calls.append({"strike":strike,"dte":dte,"expiry":expiry,
                      "bid":bid,"ask":ask,"mid":mid})

    # Try all ITM long + OTM short combinations
    for long_c in calls:
        if long_c["strike"] >= price: continue  # long must be ITM
        for short_c in calls:
            if short_c["strike"] <= price: continue  # short must be OTM
            if short_c["strike"] <= long_c["strike"]: continue
            if short_c["dte"] != long_c["dte"]: continue  # same expiry

            dte = long_c["dte"]
            width = short_c["strike"] - long_c["strike"]

            # Spread width: min $10 or 1-2% of stock price
            min_width = max(10, price * 0.01)
            if width < min_width: continue
            if width > price * 0.15: continue  # not too wide

            # Distance to short strike based on DTE
            dist_pct = (short_c["strike"] - price) / price * 100
            if 45 <= dte < 60:
                if dist_pct > 5: continue   # max 3-5% away
            elif 60 <= dte < 90:
                if dist_pct > 8: continue   # max 5-8% away
            elif dte >= 90:
                if dist_pct > 10: continue  # max 8-10% away

            # Short call delta: 0.25-0.40
            short_delta = estimate_delta(price, short_c["strike"], dte, atm_iv, "C")
            if short_delta is None or not (0.25 <= short_delta <= 0.40): continue

            debit = long_c["mid"] - short_c["mid"]
            if debit <= 0: continue
            max_profit = width - debit
            if max_profit <= 0: continue
            ror = max_profit / debit  # return on risk

            # ROR: min 80%, reject if inflated by very short DTE
            if ror < BCS_MIN_ROR: continue
            if ror > 2.0 and dte < 60: continue  # >200% ROR on short DTE = lottery trap

            # Quality tier label
            if ror >= 1.5 and dte >= 90 and 30 <= ivdata["ivp"] <= 70:
                tier_label = "A"
            elif ror >= 1.0 and dte >= 60:
                tier_label = "B"
            elif ror >= 0.8:
                tier_label = "C"
            else:
                tier_label = "D"

            if tier_label == "D": continue

            score = (timing["score"]/100) * (quality["quality_score"]/5) * ror * quality["pullback"]
            if score > best_score:
                best_score = score
                best = {
                    "long_strike": long_c["strike"],
                    "short_strike": short_c["strike"],
                    "expiry": long_c["expiry"].strftime("%Y-%m-%d"),
                    "dte": long_c["dte"],
                    "debit": round(debit,2),
                    "max_profit": round(max_profit,2),
                    "max_risk": round(debit,2),
                    "ror": round(ror*100,1),
                    "breakeven": round(long_c["strike"] + debit, 2),
                    "iv": round(atm_iv*100,1),
                    "ivp": ivdata["ivp"],
                    "timing": timing,
                    "tier_label": tier_label,
                    "dist_pct": round(dist_pct, 1),
                    "short_delta": round(short_delta, 2),
                }
    return best, timing


# ════════════════════════════════════════════════════════════
# OPPORTUNISTIC VOLATILITY SCANNER
# ════════════════════════════════════════════════════════════

def check_volatility_spike(ticker: str, md: dict) -> dict:
    """
    Detect if a stock has spiked ≥8% recently (1-3 days).
    Uses price vs recent closes from Yahoo Finance.
    Returns spike details if triggered, else None.
    """
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            headers={"User-Agent": "Mozilla/5.0"},
            params={"interval": "1d", "range": "5d"},
            timeout=8
        )
        data    = r.json()["chart"]["result"][0]
        closes  = data["indicators"]["quote"][0].get("close", [])
        closes  = [c for c in closes if c is not None]
        if len(closes) < 2:
            return None
        current = closes[-1]
        # Check 1-day and 3-day spike
        spike_1d = (current - closes[-2]) / closes[-2] if len(closes) >= 2 else 0
        spike_3d = (current - closes[-4]) / closes[-4] if len(closes) >= 4 else 0
        best_spike = max(spike_1d, spike_3d)
        if best_spike >= OPP_SPIKE_MIN:
            return {
                "spike_pct":  round(best_spike * 100, 1),
                "spike_days": 1 if spike_1d >= OPP_SPIKE_MIN else 3,
                "current":    round(current, 2),
                "triggered":  True,
            }
        return None
    except:
        return None


def find_opp_cc(ticker: str, price: float, qty: float, avg_cost: float,
                contracts: list, ivdata: dict) -> dict:
    """
    Find best covered call for opportunistic volatility mode.
    Rules: 14-30 DTE, delta 0.20-0.35, IVP > 40, strike above spike high.
    Only valid if holding ≥100 shares.
    """
    if qty < 100:
        return None
    best = None
    best_score = 0
    for c in contracts:
        if c.get("option_type") != "C":
            continue
        strike = float(c.get("strike", 0))
        if strike <= price:
            continue  # must be OTM
        expiry = c.get("expiry", "")
        try:
            exp_dt = datetime.strptime(expiry, "%Y-%m-%d")
        except:
            continue
        dte = (exp_dt - datetime.now()).days
        if not (OPP_CC_DTE_MIN <= dte <= OPP_CC_DTE_MAX):
            continue
        bid = float(c.get("nbbo_bid", 0) or 0)
        ask = float(c.get("nbbo_ask", 0) or 0)
        mid = (bid + ask) / 2
        if mid < 0.50:
            continue
        # Liquidity
        oi  = int(c.get("open_interest", 0) or 0)
        vol = int(c.get("volume", 0) or 0)
        spd = (ask - bid) / ask if ask > 0 else 1
        if oi < MIN_OPEN_INTEREST or vol < MIN_DAILY_VOLUME or spd > MAX_BID_ASK_SPREAD:
            continue
        # Use real delta from Schwab if available, else estimate
        delta = abs(float(c.get("delta", 0) or 0))
        if delta == 0:
            atm_iv = ivdata.get("atm_iv", 0.30)
            delta = abs(estimate_delta(price, strike, dte, atm_iv, "C") or 0)
        if not (OPP_CC_DELTA_MIN <= delta <= OPP_CC_DELTA_MAX):
            continue
        annualized = (mid / price) * (365 / dte) * 100
        max_contracts = int(qty // 100)
        score = (delta * 40) + (annualized * 0.5) + (oi / 10000)
        if score > best_score:
            best_score = score
            best = {
                "strike":           round(strike, 2),
                "expiry":           expiry,
                "dte":              dte,
                "bid":              round(bid, 2),
                "ask":              round(ask, 2),
                "premium":          round(mid, 2),
                "delta":            round(delta, 2),
                "annualized_return": round(annualized, 1),
                "max_contracts":    max_contracts,
                "avg_cost":         round(avg_cost, 2),
                "protection_pct":   round(mid / price * 100, 2),
            }
    return best


def fmt_opp_cc(opp: dict) -> str:
    """Format opportunistic CC alert for Telegram."""
    cc  = opp["cc"]
    spk = opp["spike"]
    s   = opp["sizing"]
    lines = [
        f"⚡ *VOLATILITY SPIKE — {opp['ticker']} @ ${opp['price']}*",
        f"_+{spk['spike_pct']}% spike in {spk['spike_days']}d — IV elevated — sell calls now_",
        "",
        f"  [{opp['tier']}] | Hold {int(s['quantity'])} shares @ ${cc['avg_cost']} avg",
        f"  Sell Call ${cc['strike']} | {cc['expiry']} | {cc['dte']} DTE",
        f"  Bid ${cc['bid']} / Ask ${cc['ask']} | Premium ${cc['premium']}",
        f"  δ{cc['delta']} | Annualized: {cc['annualized_return']}% | {cc['max_contracts']} contracts",
        f"  Protection: {cc['protection_pct']}% downside buffer",
        f"  IVP: {opp['ivp']:.0f}% | Exit at 50-70% profit",
        f"_Scanned {now_et().strftime('%b %d %H:%M')} PT_"
    ]
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════
# PETER LYNCH DISCOVERY
# ════════════════════════════════════════════════════════════

def peter_lynch_screen(known_tickers, flow_data) -> list:
    flow_tickers = {}
    for alert in flow_data:
        t       = alert.get("ticker","")
        premium = float(alert.get("total_premium",0) or 0)
        if (t and t not in known_tickers and premium > 500_000
                and alert.get("type") == "call"):
            flow_tickers[t] = flow_tickers.get(t,0) + premium

    discoveries = []
    for ticker in sorted(flow_tickers, key=flow_tickers.get, reverse=True)[:8]:
        try:
            fund = get_fundamentals(ticker)
            peg  = fund.get("peg_ratio")
            eps  = fund.get("eps_growth")
            mcap = fund.get("market_cap",0) or 0
            if peg and eps and 0 < peg < 1.5 and eps > 0.15 and mcap > 5_000_000_000:
                discoveries.append({
                    "ticker": ticker,
                    "peg_ratio": round(peg,2),
                    "eps_growth": round(eps*100,1),
                    "whale_flow": f"${flow_tickers[ticker]:,.0f}",
                    "why": f"PEG {peg:.1f}, EPS +{eps*100:.0f}%, whale call flow ${flow_tickers[ticker]/1e6:.1f}M"
                })
        except: continue
    return sorted(discoveries, key=lambda x: x.get("peg_ratio",99))[:3]


# ════════════════════════════════════════════════════════════
# CLAUDE ANALYSIS
# ════════════════════════════════════════════════════════════

def claude_analyze(csps, ccs, leaps_list, pmccs, bcss, discoveries, spikes=None, drops=None, pio=None) -> str:
    if not ANTHROPIC_API_KEY: return ""
    all_opps = csps + ccs + leaps_list + pmccs + bcss
    if not all_opps: return ""

    prompt = f"""Expert options income trader, $7M portfolio. Framework:
- Quality stock first, premium is secondary
- CSP: delta 0.20-0.30, 30-45 DTE, ≥15% annualized, IVP≥30
- CC: delta 0.15-0.25, ≥10% annualized, only when not near 52w low
- LEAPS: delta 0.80-0.90, deep ITM, <25% extrinsic, 2+ years
- PMCC: sell 30-45 DTE calls against existing LEAPS
- Bull Call Spread: ROR≥80%, rank by quality→pullback→ROR
- Earnings blackout: no CSP/CC within 14 days of earnings

Deal quality checklist:
1. Would I be happy owning at strike price?
2. Is return worth the risk?
3. Is volatility helping?
4. Is chain liquid?
5. Is strike near real support?

CSPs (Income Mode): {json.dumps(csps,indent=2)}
CCs (Income Mode): {json.dumps(ccs,indent=2)}
LEAPS: {json.dumps(leaps_list,indent=2)}
PMCCs: {json.dumps(pmccs,indent=2)}
Bull Call Spreads: {json.dumps(bcss,indent=2)}
Post-Drop CSPs (Sell Fear Mode): {json.dumps(drops,indent=2) if drops else 'None'}
Post-Spike CCs (Sell Strength Mode): {json.dumps(spikes,indent=2) if spikes else 'None'}
Position Income CCs (Existing Holdings): {json.dumps(pio,indent=2) if pio else 'None'}
Peter Lynch Discoveries: {json.dumps(discoveries,indent=2) if discoveries else 'None'}

CRITICAL FORMAT RULE: Always use EXACT expiry dates in YYYY-MM-DD format.
Never write "Apr-26" or "April expiry" — always write the full date like "2026-04-17".
There are multiple weekly expirations in any month — the exact date is essential.

Give:
1. Best CSP — ticker, exact strike, EXACT expiry date (YYYY-MM-DD), DTE, bid/ask, delta, annualized return
2. Best CC — same format (if any)
3. Best LEAPS or PMCC — same format (if any)
4. Best Bull Call Spread — long strike / short strike, EXACT expiry (YYYY-MM-DD), debit, max profit, ROR%
5. Any Peter Lynch discovery worth investigating
6. One-line IVP environment summary
7. Hard pass on anything that fails quality check

Direct, specific, no fluff. Every trade must include the full YYYY-MM-DD expiry date."""

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key":ANTHROPIC_API_KEY,
                     "anthropic-version":"2023-06-01",
                     "content-type":"application/json"},
            json={"model":"claude-sonnet-4-20250514","max_tokens":900,
                  "messages":[{"role":"user","content":prompt}]},
            timeout=30
        )
        if r.status_code == 200:
            return r.json()["content"][0]["text"]
        print(f"Claude {r.status_code}: {r.text[:100]}")
    except Exception as e:
        print(f"Claude error: {e}")
    return ""


# ════════════════════════════════════════════════════════════
# TELEGRAM
# ════════════════════════════════════════════════════════════

def send_telegram(msg: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[TG]\n{msg}\n"); return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id":TELEGRAM_CHAT_ID,"text":msg,"parse_mode":"Markdown"},
            timeout=10
        )
        print("✅" if r.status_code==200 else f"TG err: {r.text[:80]}")
    except Exception as e:
        print(f"TG: {e}")


def fmt_quality(q) -> str:
    """Format quality summary for Telegram alerts."""
    warnings = q.get("warnings", [])
    score    = q.get("quality_score", 0)
    pullback = q.get("pullback_pct", 0)
    earn     = q.get("days_to_earnings")
    earn_status = q.get("earnings_status","ok")

    lines = []
    # Earnings
    if earn_status == "hard_stop":
        lines.append(f"🚨 Earnings in {earn}d — HARD STOP")
    elif earn_status == "warning":
        lines.append(f"⚠️ Earnings in {earn}d — caution")
    elif earn and earn > 0:
        lines.append(f"✅ Earnings in {earn}d — safe")

    # MA50 extension
    if q.get("ma50_extended"):
        lines.append(f"📈 Extended {q.get('pct_above_ma50',0):.1f}% above 50MA")

    # MA200
    if not q.get("checks",{}).get("ma200_ok", True):
        lines.append("⚠️ Below 200MA")

    # Price location
    if q.get("near_high"):
        lines.append(f"📍 Near 52w high — CSP skipped")
    else:
        lines.append(f"✅ {pullback:.1f}% off highs | Quality {score}/6")

    return " | ".join(lines) if lines else f"✅ Quality {score}/6 | {pullback:.1f}% off highs"


def fmt_csp(opp) -> str:
    t = opp["csp"]["timing"]; s = opp["sizing"]; q = opp["quality"]
    d = f" | δ{opp['csp']['delta']}" if opp['csp'].get('delta') else ""
    return "\n".join([
        f"💰 *CSP — {opp['ticker']} @ ${opp['price']}*",
        f"_{t['signal']}_",
        f"  {fmt_quality(q)}",
        *([f"  {opp['darkpool']['label']}"]
           if opp.get('darkpool',{}).get('show') else []),
        f"  [{opp['tier']}] {s['tier']} tier | Max: {s['max_pct']}% (${PORTFOLIO_SIZE*s['max_pct']/100:,.0f})",
        f"  Sell Put ${opp['csp']['strike']} | {opp['csp']['expiry']} | {opp['csp']['dte']} DTE",
        f"  Bid ${opp['csp']['bid']} / Ask ${opp['csp']['ask']}",
        f"  {opp['csp']['otm_pct']}% OTM | IV {opp['csp']['iv']}% | IVP {opp['csp']['ivp']:.0f}%{d}",
        f"  Annualized: {opp['csp']['annualized_return']}% | ${opp['csp']['premium']/opp['csp']['dte']:.2f}/day | {opp['csp']['max_contracts']} contracts",
        *([f"  ⚠️ Below 20% minimum — consider skipping or check wider strike"]
           if opp['csp'].get('below_min') else []),
        f"  Collateral: ${opp['csp']['collateral']:,.0f} | Room: ${s['room_usd']:,.0f}",
        *([f"  ⚠️ OI Signal: {opp['oi_signal']['signal']} (calls {opp['oi_signal']['call_oi_change']:+,} / puts {opp['oi_signal']['put_oi_change']:+,})"]
           if opp.get("oi_signal") else []),
        *([f"  📍 Max Pain: ${opp['expiry_breakdown']['max_pain_strike']} | P/C ratio: {opp['expiry_breakdown']['put_call_ratio']}"]
           if opp.get("expiry_breakdown") and opp["expiry_breakdown"].get("max_pain_strike") else []),
        f"_Scanned {now_et().strftime('%b %d %H:%M')} PT_"
    ])


def fmt_cc(opp) -> str:
    t = opp["cc"]["timing"]; s = opp["sizing"]
    d = f" | δ{opp['cc']['delta']}" if opp['cc'].get('delta') else ""
    return "\n".join([
        f"📈 *CC — {opp['ticker']} @ ${opp['price']}*",
        f"_{t['signal']}_",
        *([f"  {opp['darkpool']['label']}"]
           if opp.get('darkpool',{}).get('show') else []),
        f"  Hold {int(s['quantity'])} shares @ ${opp['cc']['avg_cost']} avg",
        f"  Sell Call ${opp['cc']['strike']} | {opp['cc']['expiry']} | {opp['cc']['dte']} DTE",
        f"  Bid ${opp['cc']['bid']} / Ask ${opp['cc']['ask']}",
        f"  {opp['cc']['otm_pct']}% OTM | IV {opp['cc']['iv']}% | IVP {opp['cc']['ivp']:.0f}%{d}",
        f"  Annualized: {opp['cc']['annualized_return']}% | ${opp['cc']['premium']/opp['cc']['dte']:.2f}/day | {opp['cc']['max_contracts']} contracts",
        f"_Scanned {now_et().strftime('%b %d %H:%M')} PT_"
    ])


def fmt_leaps(opp) -> str:
    t = opp["leaps"]["timing"]; s = opp["sizing"]; l = opp["leaps"]
    d = f" | δ{l['delta']}" if l.get('delta') else ""
    itm = f"{l['itm_pct']}% ITM" if l['itm_pct'] > 0 else f"{abs(l['itm_pct'])}% OTM"

    # Downgrade timing signal if extrinsic doesn't match the label
    # "Exceptional" requires extrinsic <10%, "Good" requires <20%
    signal = t["signal"]
    ext_pct = l.get("extrinsic_pct", 100)
    if "EXCEPTIONAL" in signal and ext_pct >= 10:
        if ext_pct < 20:
            signal = signal.replace("🔥 EXCEPTIONAL", "✅ GOOD")
        elif ext_pct < 30:
            signal = signal.replace("🔥 EXCEPTIONAL", "⚠️ ACCEPTABLE")
        else:
            signal = signal.replace("🔥 EXCEPTIONAL", "⚠️ POOR EXTRINSIC")
    elif "GOOD" in signal and ext_pct >= 20:
        signal = signal.replace("✅ GOOD", "⚠️ ACCEPTABLE")

    return "\n".join([
        f"🚀 *LEAPS — {opp['ticker']} @ ${opp['price']}*",
        f"_{signal}_",
        *([f"  {opp['darkpool_leaps']['label']}"]
           if opp.get('darkpool_leaps',{}).get('show') else []),
        f"  52w: ${opp['w52_low']} — ${opp['w52_high']} | {opp['pullback_pct']}% off high",
        f"  Buy Call ${l['strike']} | {l['expiry']} | {l['dte']} DTE",
        f"  Bid ${l['bid']} / Ask ${l['ask']} | Cost ${l['premium']}",
        f"  {itm} | IVP {l['ivp']:.0f}%{d}",
        f"  Intrinsic: ${l['intrinsic']} | Extrinsic: ${l['extrinsic']} — {l.get('ext_label', str(l['extrinsic_pct'])+'%')}",
        f"  Leverage: {l['leverage']}x | Tier: {s['tier']} | Room: ${s['room_usd']:,.0f}",
        f"_Scanned {now_et().strftime('%b %d %H:%M')} PT_"
    ])


def fmt_pmcc(opp) -> str:
    t = opp["pmcc"]["timing"]; p = opp["pmcc"]; l = opp["existing_leaps"]
    d = f" | δ{p['delta']}" if p.get('delta') else ""
    return "\n".join([
        f"⚡ *PMCC — {opp['ticker']} @ ${opp['price']}*",
        f"_{t['signal']}_",
        f"  You own: LEAPS ${l['strike']} call ({l['dte']}DTE, {l['quantity']} contracts)",
        f"  Sell Call ${p['strike']} | {p['expiry']} | {p['dte']} DTE",
        f"  Bid ${p['bid']} / Ask ${p['ask']}",
        f"  {p['otm_pct']}% OTM | IVP {p['ivp']:.0f}%{d}",
        f"  Annualized: {p['annualized_return']}% | Months to recover LEAPS: {p['months_to_recover']}",
        f"_Scanned {now_et().strftime('%b %d %H:%M')} PT_"
    ])


def fmt_pio_cc(opp) -> str:
    """Format Position Income Optimization CC alert."""
    s  = opp["sizing"]
    p  = opp["pio_cc"]
    dp = opp.get("darkpool", {})
    pnl_map = {
        "profit":    "📈 In profit — aggressive income",
        "loss":      "📉 In loss — conservative recovery income",
        "breakeven": "➡️ Near break-even — protect position",
        "unknown":   "❓ Position status unknown"
    }
    pnl_label = pnl_map.get(opp["pnl_status"], "")
    pos_map = {
        "light":      "🟢 Light position (<4%) — aggressive",
        "normal":     "🟡 Normal position (4-8%)",
        "heavy":      "🟠 Heavy position (8-12%) — conservative",
        "overweight": "🔴 Overweight (>12%) — reduce exposure"
    }
    pos_label = pos_map.get(opp["pos_status"], "")

    lines = [
        f"💼 *POSITION INCOME — {opp['ticker']} @ ${opp['price']}*",
        f"_{pnl_label}_",
        f"_{pos_label}_",
        "",
        *([f"  {dp['label']}"] if dp.get("show") else []),
        f"  Hold {int(s['quantity'])} shares @ ${p['avg_cost']} avg cost",
        f"  Break-even: ${p['breakeven']} | Strike above break-even ✅",
        f"  Sell Call ${p['strike']} | {p['expiry']} | {p['dte']} DTE",
        f"  Bid ${p['bid']} / Ask ${p['ask']} | Mid ${p['premium']}",
        f"  δ{p['delta']} | Premium: {p['prem_pct']}% of strike",
        f"  Annualized: {p['annualized_return']}% | ${p['premium']/p['dte']:.2f}/day",
        f"  Max {p['max_contracts']} contracts",
        f"_Scanned {now_et().strftime('%b %d %H:%M')} PT_"
    ]
    return "\n".join([l for l in lines if l is not None])


def fmt_drop_csp(opp) -> str:
    """Format post-drop CSP alert for Telegram."""
    s  = opp["sizing"]
    d  = opp["drop_csp"]
    di = opp["drop_info"]
    dp = opp.get("darkpool", {})

    trigger = (f"🔻 Dropped {di['today_change']:+.1f}% today"
               if di["trigger"] == "today"
               else f"🔻 Dropped {di['pct_below_ma50']:+.1f}% below 50MA recently")

    lines = [
        f"🔻 *POST-DROP CSP — {opp['ticker']} @ ${opp['price']}*",
        f"_{trigger} — Selling fear, not chasing a falling knife_",
        f"_⚠️ ELEVATED RISK — Reduced position size ({int(DROP_SIZE_FACTOR*100)}% of normal)_",
        "",
        *([f"  {dp['label']}"] if dp.get("show") else []),
        f"  IVP: {opp['ivp']:.0f}% | 200MA: ✅ Above",
        f"  1d change: {di['today_change']:+.1f}% | Pullback: {opp['pullback_pct']}% off highs",
        f"  Sell Put ${d['strike']} | {d['expiry']} | {d['dte']} DTE",
        f"  Bid ${d['bid']} / Ask ${d['ask']} | Mid ${d['premium']}",
        f"  δ{d['delta']} | Premium: {d['prem_pct']}% of strike",
        f"  Annualized: {d['annualized_return']}% | ${d['premium']/d['dte']:.2f}/day",
        f"  Collateral: ${d['collateral']:,.0f} | {d['max_contracts']} contracts (reduced size)",
        f"  [{opp['tier']}] {s['tier']} tier | Room: ${s['room_usd']:,.0f}",
        "",
        f"  ✅ _Favorable if: market selloff, sector rotation, profit taking_",
        f"  ❌ _Avoid if: earnings miss, guidance cut, broken trend_",
        f"_Scanned {now_et().strftime('%b %d %H:%M')} PT_"
    ]
    return "\n".join([l for l in lines if l is not None])


def fmt_spike_cc(opp) -> str:
    """Format opportunistic spike CC alert for Telegram."""
    s  = opp["sizing"]
    sc = opp["spike_cc"]
    si = opp["spike_info"]
    dp = opp.get("darkpool", {})

    trigger = (f"📈 Up {si['today_change']:+.1f}% today"
               if si["trigger"] == "today"
               else f"📈 Extended {si['pct_above_ma50']:+.1f}% above 50MA")

    lines = [
        f"⚡ *SPIKE CC — {opp['ticker']} @ ${opp['price']}*",
        f"_{trigger} — IV spiked, sell calls before vol contracts_",
        "",
        *([f"  {dp['label']}"] if dp.get("show") else []),
        f"  IVP: {opp['ivp']:.0f}% | {opp['pullback_pct']}% off highs",
        f"  Sell Call ${sc['strike']} | {sc['expiry']} | {sc['dte']} DTE",
        f"  Bid ${sc['bid']} / Ask ${sc['ask']} | Mid ${sc['premium']}",
        f"  δ{sc['delta']} | Annualized: {sc['annualized_return']}% | ${sc['premium']/sc['dte']:.2f}/day",
        f"  Protection: {sc['protection_pct']}% downside buffer",
        f"  Hold {int(s['quantity'])} shares @ ${sc['avg_cost']} avg",
        f"  Max {sc['max_contracts']} contracts",
        "",
        f"  ⚠️ _Exit when 50-70% of premium captured_",
        f"  ⚠️ _Close early if stock reverses sharply_",
        f"_Scanned {now_et().strftime('%b %d %H:%M')} PT_"
    ]
    return "\n".join([l for l in lines if l is not None])


def fmt_bcs(opp) -> str:
    t = opp["bcs"]["timing"]; b = opp["bcs"]; q = opp["quality"]
    tier = b.get("tier_label","B")
    tier_emoji = {"A":"🟢","B":"🟡","C":"🟠","D":"🔴"}.get(tier,"⚪")
    width = b["short_strike"] - b["long_strike"]
    return "\n".join([
        f"📊 *Bull Call Spread — {opp['ticker']} @ ${opp['price']}*",
        f"_{t['signal']}_",
        f"  {fmt_quality(q)}",
        f"  Quality: {tier_emoji} Tier {tier}",
        f"  Buy ${b['long_strike']} Call / Sell ${b['short_strike']} Call",
        f"  Spread width: ${width:.0f} | Short δ{b.get('short_delta',0)} | {b.get('dist_pct',0):.1f}% OTM",
        f"  Expiry: {b['expiry']} | {b['dte']} DTE",
        f"  Debit: ${b['debit']} | Max Profit: ${b['max_profit']} | Max Risk: ${b['max_risk']}",
        f"  Return on Risk: {b['ror']}% | Breakeven: ${b['breakeven']}",
        f"  IVP: {b['ivp']:.0f}% | IV: {b['iv']}%",
        f"_Scanned {now_et().strftime('%b %d %H:%M')} PT_"
    ])


# ════════════════════════════════════════════════════════════
# MAIN SCANNER
# ════════════════════════════════════════════════════════════

def run_scanner():
    print(f"\n{'='*60}")
    print(f"🐋 WHALE INTELLIGENCE v5 — {now_et().strftime('%Y-%m-%d %H:%M')} ET")
    print(f"   Framework: Quality → Pullback → Option Yield")
    print(f"{'='*60}\n")

    global PORTFOLIO_SIZE

    print("📊 IBKR positions...")
    ibkr     = get_ibkr_positions()
    stk_hold = {k:v for k,v in ibkr.items() if v.get("asset_class")=="STK"}

    all_tickers = ALL_TICKERS
    print(f"💹 Market data ({len(all_tickers)} stocks)...")
    # Use Schwab for real-time quotes if available, else Yahoo Finance
    schwab_quotes = schwab_get_quotes(all_tickers) if SCHWAB_APP_KEY else {}
    mkt = get_market_data(all_tickers)
    # Merge Schwab data over Yahoo — Schwab is more accurate
    for ticker, sq in schwab_quotes.items():
        if ticker in mkt and sq.get("price", 0) > 0:
            mkt[ticker]["price"]       = sq["price"]
            mkt[ticker]["week52_high"] = sq["week52_high"] or mkt[ticker]["week52_high"]
            mkt[ticker]["week52_low"]  = sq["week52_low"]  or mkt[ticker]["week52_low"]
            mkt[ticker]["avg_volume"]  = sq["avg_volume"]  or mkt[ticker]["avg_volume"]
            if sq.get("prev_close", 0) > 0:
                mkt[ticker]["day_change_pct"] = abs(
                    sq["price"] - sq["prev_close"]
                ) / sq["prev_close"]
    print(f"   Schwab: {len(schwab_quotes)} real-time | Yahoo fallback: {len(mkt)-len(schwab_quotes)}")

    # Fetch Schwab accounts for position awareness
    schwab_accounts  = schwab_get_accounts() if SCHWAB_APP_KEY else []
    schwab_positions = schwab_parse_positions(schwab_accounts) if schwab_accounts else {}
    if schwab_positions:
        print(f"   Schwab positions: {len(schwab_positions)} holdings parsed")
    # Merge Schwab positions into IBKR dict (Schwab takes priority if both have ticker)
    for ticker, pos in schwab_positions.items():
        if ticker not in ibkr or ibkr[ticker].get("market_value", 0) == 0:
            ibkr[ticker] = pos

    # ── Calculate real portfolio size from live account data ──
    schwab_total = sum(a.get("net_liquidation", 0) for a in schwab_accounts)
    ibkr_total   = sum(v.get("market_value", 0) for v in ibkr.values()
                       if v.get("asset_class") == "STK")
    live_total   = schwab_total + ibkr_total
    if live_total >= 100_000:  # sanity check — must be at least $100k to trust
        PORTFOLIO_SIZE = round(live_total, -3)  # round to nearest $1000
        print(f"   💼 Portfolio size: ${PORTFOLIO_SIZE:,.0f} (Schwab: ${schwab_total:,.0f} | IBKR stocks: ${ibkr_total:,.0f})")
    else:
        print(f"   💼 Portfolio size: ${PORTFOLIO_SIZE:,.0f} (fallback — live data unavailable)")
    ok  = sum(1 for v in mkt.values() if v["price"]>0)
    print(f"   {ok}/{len(all_tickers)} prices ✓")

    print("🌊 Market intelligence...")
    flow       = []  # UW flow removed

    tide = {"score": 0, "label": "—", "available": False}

    print("   📈 Fetching S&P 500 regime...")
    spy_md     = get_market_data(["SPY"]).get("SPY", {})
    spy_price  = spy_md.get("price", 0)
    spy_ma200  = spy_md.get("ma200", 0)
    spy_above  = spy_price >= spy_ma200 if spy_ma200 > 0 else True
    spy_regime = {
        "above_ma200": spy_above,
        "spy":         spy_price,
        "ma200":       round(spy_ma200, 2),
        "label": (f"✅ S&P 500 above 200MA (${spy_ma200:.0f}) — Normal environment"
                  if spy_above else
                  f"⚠️ S&P 500 BELOW 200MA (${spy_ma200:.0f}) — Risk regime: reduce size, lower delta")
    }
    print(f"   {spy_regime['label']}")

    print("   😱 Fetching VIX...")
    vix_data   = get_vix()
    print(f"   {vix_data['label']}")

    spike_data = {"available": False}  # Not available on current UW plan

    oi_signals = {}  # OI removed — IVP handles individual trade filtering

    # ── GO / NO-GO DECISION ──────────────────────────────────
    gng = market_go_nogo(tide, vix_data, spy_regime)
    print(f"\n{'='*50}")
    print(f"📡 MARKET CONTEXT")
    print(f"   {gng['quality']}")
    print(f"   VIX: {gng['vix']}")
    print(f"   Scanner always runs — IVP filters per stock")
    print(f"{'='*50}\n")

    # ── MORNING MARKET BRIEFING ─────────────────────────────
    # Structure: 1) Market situation  2) Summary verdict  3) Trades follow
    vix   = gng["vix"]



    briefing = (
        f"📡 *MARKET BRIEFING — {now_et().strftime('%b %d, %Y %H:%M')} ET*\n"
        f"\n"
        f"━━━ MARKET CONDITIONS ━━━\n"
        f"\n"
        f"*VIX: {vix}*\n"
        f"{vix_data['label']}\n"
        f"_VIX measures market fear. Above 25 = high volatility."
        f" Higher VIX = fatter premiums = better CSP/CC income._\n"
        f"\n"


        f"\n"
        f"*S&P 500:* {spy_regime['label']}\n"
        f"_S&P below 200MA = reduce size, lower delta._\n"
        f"\n"
        f"━━━ TODAY'S CONTEXT ━━━\n"
        f"\n"
        f"{gng['quality']}\n"
        f"\n"
        f"_Individual trades filtered by per-stock IV Percentile (IVP ≥ 30)._\n"
        f"_Trading opportunities follow below ↓_"
    )
    send_telegram(briefing)
    time.sleep(2)

    # Scanner always runs — IVP filters individual trades

    csp_opps = []; cc_opps  = []; leaps_opps = []
    pmcc_opps= []; bcs_opps = []; spike_opps = []; drop_opps = []; pio_opps = []
    # Caches for dashboard reuse — avoid re-fetching chains
    contracts_cache = {}
    schwab_ivp_cache = {}
    qty_cache = {}
    avg_cache = {}

    print(f"\n🔍 Scanning {len(all_tickers)} stocks...")
    for ticker in all_tickers:
        # Determine tier
        if ticker in CORE_STOCKS:        tier = "Core"
        elif ticker in GROWTH_STOCKS:    tier = "Growth"
        elif ticker in CYCLICAL_STOCKS:  tier = "Cyclical"
        else:                            tier = "Opportunistic"
        is_core = (tier == "Core")
        md    = mkt.get(ticker,{})
        price = md.get("price",0)
        if price <= 0: continue

        w52h       = md.get("week52_high", price)
        w52l       = md.get("week52_low",  price)
        pir        = position_in_range(price, w52l, w52h)
        pullback   = pullback_from_high(price, w52h)

        # Earnings check
        earn_date  = get_earnings_date(ticker)

        # Stock quality gate — framework step 1
        quality    = stock_quality_check(ticker, md, earn_date)

        # Use Schwab option chain if available (real Greeks/IV), else UW
        if SCHWAB_APP_KEY:
            from datetime import timedelta
            from_d = datetime.now().strftime("%Y-%m-%d")
            to_d   = (datetime.now() + timedelta(days=75)).strftime("%Y-%m-%d")
            contracts = schwab_get_option_chain(ticker, from_d, to_d)
        if not SCHWAB_APP_KEY or not contracts:
            contracts = get_option_contracts(ticker)
        if not contracts: continue

        # Use Schwab real IVP if available
        schwab_ivp = schwab_get_ivp(ticker) if SCHWAB_APP_KEY else 0
        ivdata     = calculate_ivp(contracts)
        if schwab_ivp > 0:
            ivdata["ivp"] = schwab_ivp  # replace proxy with real IVP
        sizing     = position_check(ticker, ibkr)
        qty        = sizing["quantity"]
        avg        = sizing["avg_cost"]
        # Cache for dashboard scan
        contracts_cache[ticker]  = contracts
        schwab_ivp_cache[ticker] = schwab_ivp
        qty_cache[ticker]        = qty
        avg_cache[ticker]        = avg
        dp_stock   = {"show": False, "score": 50, "total_notional": 0}
        dp_leaps   = {"show": False, "score": 50, "total_notional": 0}
        dp         = dp_stock
        dp_boost   = 1.2 if dp.get("significant") else 1.0  # only boost on significant dark pool

        oi_sig     = {}
        oi_warning = False
        exp_bdown  = {}

        base = {"ticker":ticker,"price":price,"pir":pir,
                "tier":tier,
                "w52_low":w52l,"w52_high":w52h,
                "pullback_pct":round(pullback*100,1),
                "ivp":ivdata["ivp"],"quality":quality,
                "sizing":sizing,"darkpool":dp_stock,"darkpool_leaps":dp_leaps,
                "oi_signal":oi_sig,"expiry_breakdown":exp_bdown,
                "oi_warning":oi_warning}

        # ── CSP ──────────────────────────────────────────
        if (gng["sell_premium"]
                and sizing["status"] != "OVERWEIGHT"
                and not quality["hard_stop"]
                and not oi_warning
                and ticker not in LEAPS_ONLY):
            # Apply risk regime adjustments when S&P below 200MA
            q_adjusted = dict(quality)
            if gng.get("spy_warning"):
                q_adjusted["quality_score"] = max(0, quality["quality_score"] - 1)
            csp, _ = find_best_csp(ticker, price, contracts, ivdata, pir, q_adjusted)
            if csp:
                # below_min trades still shown but scored lower
                score_mult = 0.5 if csp.get("below_min") else 1.0
                csp_opps.append({**base,"csp":csp,
                    "score":csp["timing"]["score"]*quality["quality_score"]*csp["annualized_return"]*dp_boost*score_mult})
                print(f"  [{tier}] {ticker}: 💰 CSP ${csp['strike']} {csp['annualized_return']}% ann δ{csp['delta']} IVP{ivdata['ivp']:.0f}%")

        # ── Position Income Optimization (Mode 4) ──────────────
        # Generate income from existing holdings regardless of market conditions
        # Ignores 200MA, pullback, gap rules — pure income focus
        if qty >= 100 and avg > 0:
            pos_status = get_position_status(sizing["current_pct"])
            pnl_status = get_pnl_status(avg, price)
            # Only run if not already flagged for standard CC
            pio_cc, _ = find_position_income_cc(
                ticker, price, qty, avg, contracts, ivdata, pos_status, pnl_status)
            if pio_cc:
                pio_opps.append({**base, "pio_cc": pio_cc,
                    "pos_status": pos_status, "pnl_status": pnl_status,
                    "score": pio_cc["annualized_return"] * pio_cc["delta"]})
                print(f"  [{tier}] {ticker}: 💼 PIO CC ${pio_cc['strike']} "
                      f"{pio_cc['annualized_return']}% ann | {pnl_status} | δ{pio_cc['delta']}")

        # ── Post-Drop CSP (Mode 3) ───────────────────────────
        drop_info = detect_price_drop(ticker, md)
        if (drop_info["is_drop"]
                and tier in DROP_CSP_ALLOWED_TIERS
                and sizing["status"] != "OVERWEIGHT"):
            drop_csp, drop_timing = find_drop_csp(
                ticker, price, contracts, ivdata, pir,
                quality, drop_info, tier, sizing)
            if drop_csp:
                drop_opps.append({**base, "drop_csp": drop_csp,
                    "drop_info": drop_info,
                    "score": drop_csp["annualized_return"] * (ivdata["ivp"]/100)})
                print(f"  [{tier}] {ticker}: 🔻 DROP CSP ${drop_csp['strike']} "
                      f"{drop_csp['annualized_return']}% ann | "
                      f"{drop_info['today_change']:+.1f}% drop | "
                      f"IVP {ivdata['ivp']:.0f}%")

        # ── Opportunistic Spike CC ────────────────────────
        # Triggered BY gap moves — opposite of income mode which skips them
        # Only for SPIKE_CC_CANDIDATES where you hold shares
        spike_info = detect_price_spike(ticker, md)
        if (ticker in SPIKE_CC_CANDIDATES
                and spike_info["is_spike"]
                and qty >= 100
                and (quality.get("days_to_earnings") is None
                     or quality["days_to_earnings"] > OPP_EARNINGS_MIN)):
            spike_cc, _ = find_spike_cc(
                ticker, price, qty, avg, contracts, ivdata, spike_info)
            if spike_cc:
                spike_opps.append({**base, "spike_cc": spike_cc,
                    "spike_info": spike_info,
                    "score": spike_cc["annualized_return"] * (ivdata["ivp"] / 100)})
                print(f"  [{tier}] {ticker}: ⚡ SPIKE CC ${spike_cc['strike']} "
                      f"{spike_cc['annualized_return']}% ann | "
                      f"{spike_info['today_change']:+.1f}% move")

        # ── CC ───────────────────────────────────────────
        holding = stk_hold.get(ticker,{})
        qty = holding.get("quantity",0); avg = holding.get("avg_cost",0)
        if (gng["sell_premium"]
                and qty >= 100
                and not quality["hard_stop"]
                and ticker not in LEAPS_ONLY):
            cc, _ = find_best_cc(ticker, price, qty, avg, contracts, ivdata, pir)
            if cc:
                cc_opps.append({**base,"cc":cc,
                    "score":cc["timing"]["score"]*cc["annualized_return"]})
                print(f"  {ticker}: 📈 CC  ${cc['strike']} {cc['annualized_return']}% ann δ{cc['delta']}")

        # ── LEAPS ────────────────────────────────────────
        # LEAPS use leaps_hard_stop (more lenient) — 200MA not blocking for Core
        leaps_blocked = quality.get("leaps_hard_stop", quality["hard_stop"])
        if gng["buy_leaps"] and not leaps_blocked:
            leaps, leaps_timing = find_best_leaps(ticker, price, contracts, ivdata, pir)
            if leaps is None and ivdata["ivp"] > 0:
                print(f"  [{tier}] {ticker}: LEAPS rejected — IVP {ivdata['ivp']:.0f}% timing: {leaps_timing.get('signal','')[:50]}")
        else:
            leaps = None
            leaps_timing = {}
            if leaps_blocked and tier in ("Core","Growth"):
                print(f"  [{tier}] {ticker}: LEAPS hard stop (earnings/price)")
        if leaps:
            leaps_opps.append({**base,"leaps":leaps,
                "score":leaps["timing"]["score"]*(1/max(0.01,leaps["extrinsic_pct"]))*leaps["delta"]})
            print(f"  {ticker}: 🚀 LEAPS ${leaps['strike']} δ{leaps['delta']} ext{leaps['extrinsic_pct']}% IVP{ivdata['ivp']:.0f}%")

        # ── PMCC ─────────────────────────────────────────
        existing_leaps = find_existing_leaps(ticker, ibkr)
        if existing_leaps:
            pmcc, _ = find_pmcc_short_call(ticker, price, existing_leaps, contracts, ivdata, pir)
            if pmcc:
                pmcc_opps.append({**base,"pmcc":pmcc,"existing_leaps":existing_leaps,
                    "score":pmcc["timing"]["score"]*pmcc["annualized_return"]})
                print(f"  {ticker}: ⚡ PMCC ${pmcc['strike']} {pmcc['annualized_return']}% ann")

        # ── Bull Call Spread ──────────────────────────────
        if quality["passes"] and pullback >= PULLBACK_MIN:
            bcs, _ = find_bull_call_spread(ticker, price, contracts, ivdata, pir, quality)
            if bcs:
                bcs_opps.append({**base,"bcs":bcs,
                    "score":quality["quality_score"]*bcs["ror"]*pullback*dp_boost})
                print(f"  {ticker}: 📊 BCS ROR {bcs['ror']}% | debit ${bcs['debit']}")

    # ── Sort & top 3 each ─────────────────────────────────
    for lst in [csp_opps,cc_opps,leaps_opps,pmcc_opps,bcs_opps]:
        lst.sort(key=lambda x: x["score"], reverse=True)

    top_csps  = csp_opps[:3];  top_ccs   = cc_opps[:3]
    top_leaps = leaps_opps[:3];top_pmccs = pmcc_opps[:3]
    top_bcss  = bcs_opps[:3]
    top_spikes = spike_opps[:3]
    top_drops  = drop_opps[:3]
    top_pio    = pio_opps[:5]  # show up to 5 position income trades

    total = sum(len(x) for x in [top_csps,top_ccs,top_leaps,top_pmccs,top_bcss,top_spikes,top_drops,top_pio])
    print(f"\n🏆 {len(top_csps)} CSPs | {len(top_ccs)} CCs | {len(top_leaps)} LEAPS | "
          f"{len(top_pmccs)} PMCCs | {len(top_bcss)} Spreads")

    # If LEAPS recommended but none found — explain why
    if gng["buy_leaps"] and len(top_leaps) == 0:
        leaps_msg = (
            "🚀 *LEAPS — No qualifying trades today*\n\n"
            "System recommended buying LEAPS but none passed all filters.\n\n"
            "Most likely reason: IVP is above 50% on most stocks, making options "
            "too expensive to buy. LEAPS are best when IVP < 40%.\n\n"
            "_Wait for a volatility spike followed by a quick reversal — "
            "that's when LEAPS become cheapest on quality stocks._"
        )
        send_telegram(leaps_msg)

    # ── Peter Lynch ───────────────────────────────────────
    print("🔬 Peter Lynch screen...")
    discoveries = peter_lynch_screen(set(ALL_TICKERS), flow)
    if discoveries:
        print(f"   Found: {[d['ticker'] for d in discoveries]}")

    if total == 0 and not discoveries:
        print("✅ No qualifying opportunities today.")
        return

    # ── Opportunistic Volatility Scan ────────────────────
    print("\n⚡ Opportunistic volatility scan...")
    opp_opps = []
    # Scan ALL tickers including ones not normally scanned for spikes
    opp_tickers = ALL_TICKERS + ["NBIS", "PLTR", "IBIT"]
    opp_tickers = list(set(opp_tickers))  # deduplicate
    for ticker in opp_tickers:
        try:
            md_t  = mkt.get(ticker, {})
            price = md_t.get("price", 0)
            if price <= 0:
                continue
            # Check for volatility spike
            spike = check_volatility_spike(ticker, md_t)
            if not spike:
                continue
            # Check earnings blackout
            earn_date = get_earnings_date(ticker)
            if earn_date:
                days_earn = (earn_date - datetime.now()).days
                if 0 < days_earn < OPP_EARNINGS_MIN:
                    print(f"  {ticker}: spike {spike['spike_pct']}% but earnings in {days_earn}d — skip")
                    continue
            # Check IVP
            ivdata_t = calculate_ivp(get_option_contracts(ticker))
            if ivdata_t.get("ivp", 0) < OPP_IVP_MIN:
                print(f"  {ticker}: spike {spike['spike_pct']}% but IVP {ivdata_t['ivp']:.0f}% too low")
                continue
            # Check if holding shares (from IBKR or Schwab)
            pos   = ibkr.get(ticker, {})
            qty   = float(pos.get("quantity", 0))
            avg   = float(pos.get("avg_cost", 0))
            if qty < 100:
                print(f"  {ticker}: spike {spike['spike_pct']}% but <100 shares held")
                continue
            # Get contracts and find best CC
            contracts_t = get_option_contracts(ticker)
            if SCHWAB_APP_KEY:
                from datetime import timedelta
                from_d = datetime.now().strftime("%Y-%m-%d")
                to_d   = (datetime.now() + timedelta(days=35)).strftime("%Y-%m-%d")
                sc = schwab_get_option_chain(ticker, from_d, to_d)
                if sc:
                    contracts_t = sc
            tier_t = ("Core" if ticker in CORE_STOCKS else
                      "Growth" if ticker in GROWTH_STOCKS else
                      "Cyclical" if ticker in CYCLICAL_STOCKS else "Opportunistic")
            sizing_t = position_check(ticker, ibkr)
            cc_opp = find_opp_cc(ticker, price, qty, avg, contracts_t, ivdata_t)
            if cc_opp:
                opp_opps.append({
                    "ticker": ticker, "price": price,
                    "tier": tier_t, "spike": spike,
                    "cc": cc_opp, "sizing": sizing_t,
                    "ivp": ivdata_t.get("ivp", 0),
                })
                print(f"  {ticker}: ⚡ SPIKE {spike['spike_pct']}% | CC ${cc_opp['strike']} {cc_opp['annualized_return']}% ann")
        except Exception as e:
            print(f"  {ticker} opp error: {e}")

    if opp_opps:
        print(f"\n⚡ {len(opp_opps)} volatility spike opportunities found")
    else:
        print("   No volatility spike setups today")

    # ── Claude analysis ───────────────────────────────────
    print("\n🧠 Claude analysis...")
    analysis = claude_analyze(top_csps,top_ccs,top_leaps,top_pmccs,top_bcss,discoveries,top_spikes,top_drops,top_pio)
    if analysis: print(f"\n{analysis}")

    # ── Telegram — ORDER: Summary → Trades ───────────────
    print("\n📱 Sending...")

    # 1. Claude summary FIRST (before individual trades)
    if analysis:
        send_telegram(f"🧠 *CLAUDE SUMMARY*\n\n{analysis}")
        time.sleep(2)

    # 2. Peter Lynch discoveries (context before trades)
    if discoveries:
        msg = "🔬 *Peter Lynch Discoveries*\n_Not on watchlist — quality fundamentals + whale flow_\n\n"
        for d in discoveries:
            msg += f"*{d['ticker']}* — PEG {d['peg_ratio']} | EPS +{d['eps_growth']}% | Flow {d['whale_flow']}\n"
        send_telegram(msg)
        time.sleep(2)

    # 2b. Opportunistic volatility spike alerts (before regular trades)
    if opp_opps:
        send_telegram("━━━ *⚡ VOLATILITY SPIKE OPPORTUNITIES* ━━━")
        send_telegram(
            "_These triggered because of recent sharp price spikes.\n"
            "IV is elevated — good time to sell calls if you hold shares._"
        )
        time.sleep(1)
        for o in opp_opps:
            send_telegram(fmt_opp_cc(o))
            time.sleep(2)

    # 3. Individual trade alerts
    if top_pio:
        send_telegram("━━━ *💼 POSITION INCOME OPPORTUNITIES* ━━━"); time.sleep(1)
        for o in top_pio: send_telegram(fmt_pio_cc(o)); time.sleep(2)
    if top_drops:
        send_telegram("━━━ *🔻 POST-DROP CSP OPPORTUNITIES* ━━━"); time.sleep(1)
        for o in top_drops: send_telegram(fmt_drop_csp(o)); time.sleep(2)
    if top_spikes:
        send_telegram("━━━ *⚡ VOLATILITY SPIKE CC OPPORTUNITIES* ━━━"); time.sleep(1)
        for o in top_spikes: send_telegram(fmt_spike_cc(o)); time.sleep(2)
    if top_csps:
        send_telegram("━━━ *CSP OPPORTUNITIES* ━━━"); time.sleep(1)
        for o in top_csps: send_telegram(fmt_csp(o)); time.sleep(2)
    if top_ccs:
        send_telegram("━━━ *COVERED CALL OPPORTUNITIES* ━━━"); time.sleep(1)
        for o in top_ccs: send_telegram(fmt_cc(o)); time.sleep(2)
    if top_leaps:
        send_telegram("━━━ *LEAPS OPPORTUNITIES* ━━━"); time.sleep(1)
        for o in top_leaps: send_telegram(fmt_leaps(o)); time.sleep(2)
    if top_pmccs:
        send_telegram("━━━ *PMCC — SELL AGAINST YOUR LEAPS* ━━━"); time.sleep(1)
        for o in top_pmccs: send_telegram(fmt_pmcc(o)); time.sleep(2)
    if top_bcss:
        send_telegram("━━━ *BULL CALL SPREADS* ━━━"); time.sleep(1)
        for o in top_bcss: send_telegram(fmt_bcs(o)); time.sleep(2)

    # ── Save results.json for dashboard ─────────────────────
    def opp_to_dict(o, strategy_key):
        """Convert opportunity to clean dict for JSON."""
        s = o.get(strategy_key, {})
        return {
            "ticker":            o.get("ticker",""),
            "tier":              o.get("tier",""),
            "price":             o.get("price",0),
            "ivp":               round(o.get("ivp",0),1),
            "mode":              strategy_key.upper(),
            "strike":            s.get("strike",0),
            "expiry":            s.get("expiry",""),
            "dte":               s.get("dte",0),
            "premium":           s.get("premium",0),
            "annualized_return": s.get("annualized_return",0),
            "delta":             s.get("delta",0),
            "signal":            s.get("timing",{}).get("signal","") or "",
            "below_min":         s.get("below_min", False),
            "risk_note":         None,
        }

    # ── Dashboard-only scan — collect ALL candidates for review ──
    # Relaxed quality gate — shows everything sorted best to worst
    # Telegram stays strict, dashboard shows full picture
    dashboard_csps  = []
    dashboard_ccs   = []
    dashboard_leaps = []
    dashboard_bcss  = []

    for ticker in all_tickers:
        if ticker in CORE_STOCKS:        tier = "Core"
        elif ticker in GROWTH_STOCKS:    tier = "Growth"
        elif ticker in CYCLICAL_STOCKS:  tier = "Cyclical"
        else:                            tier = "Opportunistic"

        md    = mkt.get(ticker, {})
        price = md.get("price", 0)
        if price <= 0: continue

        w52h     = md.get("week52_high", price)
        w52l     = md.get("week52_low",  price)
        pir      = position_in_range(price, w52l, w52h)
        earn_date = get_earnings_date(ticker)
        quality  = stock_quality_check(ticker, md, earn_date)
        ivdata_d = calculate_ivp(contracts_cache.get(ticker, []))
        if schwab_ivp_cache.get(ticker, 0) > 0:
            ivdata_d["ivp"] = schwab_ivp_cache[ticker]
        if "iv_current" not in ivdata_d:
            ivdata_d["iv_current"] = ivdata_d.get("atm_iv", 0.30)

        # Hard earnings stop still applies — never trade near earnings
        if quality["earnings_status"] == "hard_stop": continue
        if price < 10: continue

        contracts_d = contracts_cache.get(ticker, [])
        if not contracts_d: continue

        # CSP — relaxed: skip near-high/ma50/gap checks, keep earnings
        csp_d, _ = find_best_csp(ticker, price, contracts_d, ivdata_d, pir, quality)
        if csp_d:
            warnings = []
            if quality["near_high"]:      warnings.append("Near 52w high")
            if quality["ma50_extended"]:  warnings.append(">8% above MA50")
            if not quality["checks"].get("above_ma200", True): warnings.append("Below 200MA")
            if not quality["checks"].get("no_gap", True):      warnings.append("Gap/spike day")
            if quality["earnings_status"] == "warning":        warnings.append(f"Earnings ~{quality['days_to_earnings']}d")
            dashboard_csps.append({
                "ticker": ticker, "tier": tier, "price": price,
                "ivp": round(ivdata_d["ivp"], 1),
                "mode": "CSP",
                "strike": csp_d["strike"], "expiry": csp_d["expiry"],
                "dte": csp_d["dte"], "premium": csp_d["premium"],
                "annualized_return": csp_d["annualized_return"],
                "delta": csp_d["delta"],
                "below_min": csp_d.get("below_min", False),
                "warnings": warnings,
                "passes_quality": not quality["hard_stop"],
                "signal": csp_d["timing"].get("signal", ""),
                "risk_note": ", ".join(warnings) if warnings else None,
            })

        # CC — relaxed — check both ibkr and schwab positions for shares
        ibkr_pos_d = ibkr.get(ticker, {})
        qty_d = float(ibkr_pos_d.get("quantity", ibkr_pos_d.get("qty", 0)) or 0)
        avg_d = float(ibkr_pos_d.get("avg_cost", 0) or 0)
        if qty_d >= 100:
            cc_d, _ = find_best_cc(ticker, price, qty_d, avg_d, contracts_d, ivdata_d, pir)
            if cc_d:
                warnings = []
                if quality["earnings_status"] == "warning": warnings.append(f"Earnings ~{quality['days_to_earnings']}d")
                dashboard_ccs.append({
                    "ticker": ticker, "tier": tier, "price": price,
                    "ivp": round(ivdata_d["ivp"], 1),
                    "mode": "CC",
                    "strike": cc_d["strike"], "expiry": cc_d["expiry"],
                    "dte": cc_d["dte"], "premium": cc_d["premium"],
                    "annualized_return": cc_d["annualized_return"],
                    "delta": cc_d["delta"],
                    "below_min": cc_d["annualized_return"] < CC_MIN_ANNUALIZED,
                    "warnings": warnings,
                    "passes_quality": not quality["hard_stop"],
                    "signal": cc_d["timing"].get("signal", ""),
                    "risk_note": ", ".join(warnings) if warnings else None,
                })

        # LEAPS — relaxed: use leaps_hard_stop only
        if not quality["leaps_hard_stop"] and ticker not in LEAPS_ONLY:
            leaps_d, lt = find_best_leaps(ticker, price, contracts_d, ivdata_d, pir)
            if leaps_d:
                warnings = []
                if not quality["checks"].get("above_ma200", True): warnings.append("Below 200MA")
                if quality["earnings_status"] == "warning":        warnings.append(f"Earnings ~{quality['days_to_earnings']}d")
                dashboard_leaps.append({
                    "ticker": ticker, "tier": tier, "price": price,
                    "ivp": round(ivdata_d["ivp"], 1),
                    "mode": "LEAPS",
                    "strike": leaps_d["strike"], "expiry": leaps_d["expiry"],
                    "dte": leaps_d["dte"], "premium": leaps_d["premium"],
                    "annualized_return": 0,
                    "delta": leaps_d["delta"],
                    "extrinsic_pct": leaps_d["extrinsic_pct"],
                    "below_min": False,
                    "warnings": warnings,
                    "passes_quality": not quality["hard_stop"],
                    "signal": lt.get("signal", ""),
                    "risk_note": ", ".join(warnings) if warnings else None,
                })

        # BCS — relaxed
        bcs_d, _ = find_bull_call_spread(ticker, price, contracts_d, ivdata_d, pir, quality)
        if bcs_d:
            b = bcs_d
            dashboard_bcss.append({
                "ticker": ticker, "tier": tier, "price": price,
                "ivp": round(ivdata_d["ivp"], 1),
                "mode": "BCS",
                "strike": b["long_strike"],
                "long_strike": b["long_strike"],
                "short_strike": b["short_strike"],
                "expiry": b["expiry"],
                "dte": b["dte"],
                "premium": b["debit"],
                "annualized_return": b["ror"],
                "delta": b.get("short_delta", 0),
                "below_min": b["ror"] < BCS_MIN_ROR * 100,
                "warnings": [],
                "passes_quality": not quality["hard_stop"],
                "signal": b["timing"].get("signal", ""),
                "risk_note": f"Max profit: ${b['max_profit']} | Breakeven: ${b['breakeven']}",
            })

    # Sort each category by annualized return (or score for LEAPS)
    dashboard_csps.sort(key=lambda x: x["annualized_return"], reverse=True)
    dashboard_ccs.sort(key=lambda x: x["annualized_return"], reverse=True)
    dashboard_leaps.sort(key=lambda x: x["ivp"], reverse=False)  # lowest IVP first = cheapest
    dashboard_bcss.sort(key=lambda x: x["annualized_return"], reverse=True)

    print(f"   📊 Dashboard: {len(dashboard_csps)} CSPs | {len(dashboard_ccs)} CCs | {len(dashboard_leaps)} LEAPS | {len(dashboard_bcss)} BCS")

    all_opps = []
    for o in top_csps:   all_opps.append(opp_to_dict(o, "csp"))
    for o in top_ccs:    all_opps.append(opp_to_dict(o, "cc"))
    for o in top_leaps:  all_opps.append(opp_to_dict(o, "leaps"))
    for o in top_pmccs:  all_opps.append(opp_to_dict(o, "pmcc"))
    for o in top_bcss:
        b = o.get("bcs", {})
        all_opps.append({
            "ticker":            o.get("ticker",""),
            "tier":              o.get("tier",""),
            "price":             o.get("price",0),
            "ivp":               round(o.get("ivp",0),1),
            "mode":              "BCS",
            "strike":            b.get("long_strike",0),
            "long_strike":       b.get("long_strike",0),
            "short_strike":      b.get("short_strike",0),
            "expiry":            b.get("expiry",""),
            "dte":               b.get("dte",0),
            "premium":           b.get("debit",0),
            "annualized_return": b.get("ror",0),
            "delta":             b.get("short_delta",0),
            "signal":            b.get("timing",{}).get("signal","") or "",
            "below_min":         b.get("ror",0) < BCS_MIN_ROR * 100,
            "risk_note":         f"Max profit: ${b.get('max_profit',0)} | Breakeven: ${b.get('breakeven',0)}",
            "passes_quality":    True,
            "warnings":          [],
        })
    for o in top_spikes:
        s = o.get("spike_cc", {})
        all_opps.append({
            "ticker": o.get("ticker",""), "tier": o.get("tier",""),
            "price": o.get("price",0), "ivp": round(o.get("ivp",0),1),
            "mode": "SPIKE_CC", "strike": s.get("strike",0),
            "expiry": s.get("expiry",""), "dte": s.get("dte",0),
            "premium": s.get("premium",0),
            "annualized_return": s.get("annualized_return",0),
            "delta": s.get("delta",0), "signal": "", "below_min": False, "risk_note": None,
        })
    for o in top_drops:
        s = o.get("drop_csp", {})
        all_opps.append({
            "ticker": o.get("ticker",""), "tier": o.get("tier",""),
            "price": o.get("price",0), "ivp": round(o.get("ivp",0),1),
            "mode": "DROP_CSP", "strike": s.get("strike",0),
            "expiry": s.get("expiry",""), "dte": s.get("dte",0),
            "premium": s.get("premium",0),
            "annualized_return": s.get("annualized_return",0),
            "delta": s.get("delta",0),
            "signal": s.get("timing",{}).get("signal","") or "",
            "below_min": s.get("below_min", False), "risk_note": "60% normal size",
        })
    for o in top_pio:
        s = o.get("pio_cc", {})
        all_opps.append({
            "ticker": o.get("ticker",""), "tier": o.get("tier",""),
            "price": o.get("price",0), "ivp": round(o.get("ivp",0),1),
            "mode": "PIO", "strike": s.get("strike",0),
            "expiry": s.get("expiry",""), "dte": s.get("dte",0),
            "premium": s.get("premium",0),
            "annualized_return": s.get("annualized_return",0),
            "delta": s.get("delta",0), "signal": o.get("pnl_status",""),
            "below_min": False, "risk_note": None,
        })

    # Build positions list — anonymized for public dashboard
    # No share counts, no dollar values, no exact P&L or portfolio size
    pos_list = []
    for ticker, pos in ibkr.items():
        if pos.get("asset_class") == "STK" and pos.get("market_value",0) > 0:
            price_now = mkt.get(ticker,{}).get("price", 0) or pos.get("avg_cost",0)
            avg = float(pos.get("avg_cost",0) or 0)
            qty_p = int(pos.get("quantity", pos.get("qty", 0)) or 0)
            pnl = round((price_now - avg) / avg * 100, 1) if avg > 0 else 0
            mv  = float(pos.get("market_value", qty_p * price_now) or 0)
            tier = ("Core" if ticker in CORE_STOCKS else
                    "Growth" if ticker in GROWTH_STOCKS else
                    "Cyclical" if ticker in CYCLICAL_STOCKS else "Opportunistic")
            global_pct = round(mv/PORTFOLIO_SIZE*100, 1) if PORTFOLIO_SIZE > 0 else 0
            max_pct = {"Core":8.0,"Growth":5.0,"Cyclical":4.0,"Opportunistic":2.5}.get(tier,2.5)

            # Broad P&L buckets — no exact numbers
            if pnl > 15:      pnl_status = "strong_profit"
            elif pnl > 5:     pnl_status = "profit"
            elif pnl > -5:    pnl_status = "breakeven"
            elif pnl > -15:   pnl_status = "loss"
            else:             pnl_status = "significant_loss"

            # Position size status
            if global_pct >= max_pct:       size_status = "overweight"
            elif global_pct >= max_pct*0.8: size_status = "near_max"
            elif global_pct >= max_pct*0.4: size_status = "normal"
            else:                           size_status = "light"

            # Action signal
            if pnl_status in ("strong_profit","profit") and size_status in ("overweight","near_max"):
                action = "REDUCE"; action_note = "In profit + overweight — consider trimming"
            elif pnl_status in ("strong_profit","profit"):
                action = "GENERATE INCOME"; action_note = "In profit — sell covered calls"
            elif pnl_status == "breakeven":
                action = "HOLD"; action_note = "Near break-even — conservative CC if possible"
            elif pnl_status in ("loss","significant_loss") and size_status == "light":
                action = "MONITOR"; action_note = "In loss — monitor, no averaging down"
            else:
                action = "PROTECT"; action_note = "In loss — sell conservative CC to reduce cost basis"

            has_cc = any(o.get("ticker")==ticker and o.get("mode")=="CC" for o in dashboard_ccs)

            pos_list.append({
                "ticker":      ticker,
                "tier":        tier,
                "pnl_status":  pnl_status,
                "size_status": size_status,
                "action":      action,
                "action_note": action_note,
                "has_cc":      has_cc,
            })

    results = {
        "scan_time":      now_et().strftime("%Y-%m-%d %H:%M ET"),
        "scan_date":      now_et().strftime("%Y-%m-%d"),
        "dashboard_opportunities": dashboard_csps + dashboard_ccs + dashboard_leaps + dashboard_bcss,
        "market": {
            "vix":        gng["vix"],
            "vix_label":  vix_data.get("label",""),
            "spy":        round(spy_regime.get("spy",0),2),
            "spy_ma200":  round(spy_regime.get("ma200",0),2),
            "spy_above":  spy_regime.get("above_ma200",True),
            "verdict":    gng.get("quality",""),
        },
        "opportunities":  all_opps,
        "positions":      pos_list,
        "analysis":       analysis,
        "total_opps":     len(all_opps),
    }

    with open("results.json","w") as f:
        json.dump(results, f, indent=2)
    print("   💾 results.json saved")

    print("\n✅ Done!")


if __name__ == "__main__":
    run_scanner()
