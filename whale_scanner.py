"""
Whale Intelligence — Personal Options Trading Scanner
v4 — Tiered position sizing, IVP proxy, delta fix,
     Peter Lynch discovery, quality-aware filtering
"""

import os, json, re, math, time
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional

UNUSUAL_WHALES_API_KEY = os.environ.get("UW_API_KEY", "")
TELEGRAM_BOT_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID       = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY      = os.environ.get("ANTHROPIC_API_KEY", "")
IBKR_FLEX_TOKEN        = os.environ.get("IBKR_FLEX_TOKEN", "")
IBKR_FLEX_QUERY_ID     = os.environ.get("IBKR_FLEX_QUERY_ID", "")

PORTFOLIO_SIZE = 7_000_000

# ── Tiered position limits ──────────────────────────────────
POSITION_TIERS = {
    # Mega cap — highest conviction, highest allocation
    "AAPL":  0.08, "AMZN": 0.08, "GOOGL": 0.08,
    "NVDA":  0.08, "MSFT": 0.08,
    # Quality core
    "ASML":  0.06, "TSM":  0.06, "MELI":  0.06,
    "NVO":   0.06, "BRK-B":0.06,
    # Standard core
    "IBKR":  0.04, "MU":   0.04,
    # Default for opportunistic
    "__DEFAULT__": 0.025
}

# ── Strategy parameters ─────────────────────────────────────
CSP_DTE_MIN       = 25
CSP_DTE_MAX       = 45
CC_DTE_MIN        = 25
CC_DTE_MAX        = 45
LEAPS_DTE_MIN     = 300
CSP_DELTA_MAX     = 0.32
CC_DELTA_MAX      = 0.35
ALERT_PREMIUM_MIN = 1.00
MIN_ANNUALIZED    = 15.0
MAX_ANNUALIZED    = 120.0
IVP_MIN_TO_SELL   = 40      # Min IV percentile to sell premium (CSP/CC)
IVP_MAX_TO_BUY    = 50      # Max IV percentile to buy LEAPS

# ── Watchlists ──────────────────────────────────────────────
CORE_STOCKS = [
    "AAPL","AMZN","ASML","BRK-B","GOOGL",
    "IBKR","MELI","MU","NVDA","NVO","TSM"
]
OPPORTUNISTIC_STOCKS = [
    "BABA","CLS","CRDO","DDOG","FIX","KNX","LULU","NFLX","NOW",
    "POWL","UBER","VRT","VRTX","CPRT","CRSP","GRAB","IBIT",
    "NBIS","PATH","PLTR","TSLA"
]
# Biotech/speculative — require wider OTM buffer
SPECULATIVE_TICKERS = {"VRTX","CRSP","NBIS","GRAB","PATH","IBIT","PLTR","BABA","CRDO"}

UW_BASE    = "https://api.unusualwhales.com"
UW_HEADERS = {"Authorization": f"Bearer {UNUSUAL_WHALES_API_KEY}"}


# ─────────────────────────────────────────────
# MARKET DATA
# ─────────────────────────────────────────────

def get_market_data(tickers: list) -> dict:
    data = {}
    for ticker in tickers:
        yf = ticker.replace("BRK.B","BRK-B")
        try:
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{yf}",
                headers={"User-Agent":"Mozilla/5.0"}, timeout=10
            )
            j    = r.json()
            meta = j["chart"]["result"][0]["meta"]
            data[ticker] = {
                "price":       round(float(meta.get("regularMarketPrice",0)),2),
                "week52_high": round(float(meta.get("fiftyTwoWeekHigh",0)),2),
                "week52_low":  round(float(meta.get("fiftyTwoWeekLow",0)),2),
                "avg_volume":  int(meta.get("averageDailyVolume3Month",0)),
            }
        except:
            data[ticker] = {"price":0,"week52_high":0,"week52_low":0,"avg_volume":0}
    return data


def get_fundamentals(ticker: str) -> dict:
    """Fetch P/E, EPS growth, PEG for Peter Lynch screening."""
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
            f"?modules=defaultKeyStatistics,financialData,earningsTrend",
            headers={"User-Agent":"Mozilla/5.0"}, timeout=10
        )
        j   = r.json().get("quoteSummary",{}).get("result",[{}])[0]
        ks  = j.get("defaultKeyStatistics",{})
        fd  = j.get("financialData",{})
        return {
            "peg_ratio":       ks.get("pegRatio",{}).get("raw"),
            "forward_pe":      ks.get("forwardPE",{}).get("raw"),
            "eps_growth":      fd.get("earningsGrowth",{}).get("raw"),
            "revenue_growth":  fd.get("revenueGrowth",{}).get("raw"),
            "profit_margin":   fd.get("profitMargins",{}).get("raw"),
            "debt_to_equity":  fd.get("debtToEquity",{}).get("raw"),
            "return_on_equity":fd.get("returnOnEquity",{}).get("raw"),
        }
    except:
        return {}


def position_in_range(price, w52_low, w52_high) -> float:
    if w52_high <= w52_low or price <= 0: return 0.5
    return round((price - w52_low) / (w52_high - w52_low), 2)


# ─────────────────────────────────────────────
# IBKR
# ─────────────────────────────────────────────

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
            }
        stk = sum(1 for v in positions.values() if v["asset_class"]=="STK")
        opt = sum(1 for v in positions.values() if v["asset_class"]=="OPT")
        print(f"   IBKR: {stk} stocks, {opt} options loaded")
    except Exception as e:
        print(f"   IBKR error: {e}")
    return positions


# ─────────────────────────────────────────────
# UNUSUAL WHALES
# ─────────────────────────────────────────────

def get_option_contracts(ticker: str) -> list:
    try:
        r = requests.get(f"{UW_BASE}/api/stock/{ticker}/option-contracts",
                         headers=UW_HEADERS, timeout=15)
        if r.status_code == 200:
            return r.json().get("data", [])
    except: pass
    return []


def get_darkpool(ticker: str) -> list:
    try:
        r = requests.get(f"{UW_BASE}/api/darkpool/{ticker}",
                         headers=UW_HEADERS, timeout=10)
        if r.status_code == 200:
            return r.json().get("data", [])
    except: pass
    return []


def get_flow_alerts_market() -> list:
    """Market-wide flow for Peter Lynch discovery."""
    try:
        r = requests.get(f"{UW_BASE}/api/option-trades/flow-alerts",
                         headers=UW_HEADERS, timeout=10)
        if r.status_code == 200:
            return r.json().get("data", [])
    except: pass
    return []


# ─────────────────────────────────────────────
# IV PERCENTILE PROXY
# ─────────────────────────────────────────────

def calculate_ivp(contracts: list, target_dte_min=20, target_dte_max=50) -> dict:
    """
    Calculate IV percentile proxy from option chain.
    Uses ATM options in target DTE range.
    IVP = where current IV sits vs the range of IVs in the chain.
    Returns iv_current, iv_low, iv_high, ivp (0-100)
    """
    today = datetime.now()
    ivs   = []
    for c in contracts:
        parsed = parse_option_symbol(c.get("option_symbol",""))
        if not parsed: continue
        expiry, _, _ = parsed
        dte = (expiry - today).days
        if not (target_dte_min <= dte <= target_dte_max): continue
        iv = float(c.get("implied_volatility",0) or 0)
        if 0.05 < iv < 5.0:   # sanity range
            ivs.append(iv)

    if not ivs:
        return {"iv_current": 0.30, "iv_low": 0.20, "iv_high": 0.60, "ivp": 50}

    iv_sorted = sorted(ivs)
    iv_low    = iv_sorted[0]
    iv_high   = iv_sorted[-1]
    iv_median = iv_sorted[len(iv_sorted)//2]
    iv_range  = iv_high - iv_low
    ivp       = round((iv_median - iv_low) / iv_range * 100, 1) if iv_range > 0 else 50

    return {
        "iv_current": round(iv_median, 3),
        "iv_low":     round(iv_low, 3),
        "iv_high":    round(iv_high, 3),
        "ivp":        ivp
    }


# ─────────────────────────────────────────────
# OPTION MATH
# ─────────────────────────────────────────────

def parse_option_symbol(sym: str):
    try:
        m = re.match(r'^([A-Z.\-]+)(\d{6})([CP])(\d{8})$', sym)
        if not m: return None
        expiry = datetime.strptime("20"+m.group(2), "%Y%m%d")
        return expiry, m.group(3), int(m.group(4))/1000
    except: return None


def estimate_delta(otm_pct: float, dte: int, iv: float, opt_type: str) -> Optional[float]:
    """Delta approximation using ATM IV only."""
    try:
        if iv <= 0 or dte <= 0 or iv > 3.0: return None
        t   = dte / 365
        sst = iv * math.sqrt(t)
        if sst == 0: return None
        moneyness = -otm_pct / 100
        d1  = moneyness / sst + 0.5 * sst
        cdf = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
        return round((1 - cdf) if opt_type == "P" else cdf, 2)
    except: return None


def score_darkpool(trades: list) -> dict:
    if not trades:
        return {"score":50,"total_notional":0,"label":"No dark pool data"}
    total = bullish = 0
    for t in trades[:20]:
        n = float(t.get("size",0)) * float(t.get("price",0))
        total += n
        if float(t.get("price",0)) >= float(t.get("vwap",t.get("price",0))):
            bullish += n
    score = (bullish/total*100) if total > 0 else 50
    label = ("🟢 Institutions accumulating" if score > 55
             else "🔴 Institutions distributing" if score < 45
             else "⚪ Mixed institutional activity")
    return {"score":round(score,1),"total_notional":round(total,0),"label":label}


def get_max_allocation(ticker: str) -> float:
    return POSITION_TIERS.get(ticker, POSITION_TIERS["__DEFAULT__"])


def position_check(ticker, ibkr_positions):
    pos  = ibkr_positions.get(ticker, {})
    val  = pos.get("market_value", 0)
    qty  = pos.get("quantity", 0)
    avg  = pos.get("avg_cost", 0)
    pct  = (val / PORTFOLIO_SIZE) * 100
    max_pct = get_max_allocation(ticker) * 100
    max_val = PORTFOLIO_SIZE * get_max_allocation(ticker)
    room = max(0, round(max_val - val, 0))
    tier = ("Mega cap" if get_max_allocation(ticker) >= 0.08
            else "Quality core" if get_max_allocation(ticker) >= 0.06
            else "Standard core" if get_max_allocation(ticker) >= 0.04
            else "Opportunistic")
    return {
        "current_value": round(val,0), "quantity": qty, "avg_cost": avg,
        "current_pct": round(pct,2), "max_pct": round(max_pct,1),
        "room_usd": room, "tier": tier,
        "status": ("OVERWEIGHT" if pct > max_pct * 1.2
                   else "FULL"     if pct > max_pct * 0.9
                   else "HAS ROOM" if val > 0
                   else "NEW POSITION")
    }


# ─────────────────────────────────────────────
# MARKET TIMING INTELLIGENCE
# ─────────────────────────────────────────────

def timing_score(strategy: str, pir: float, ivp: float, is_speculative: bool = False) -> dict:
    """
    Score timing using IVP (IV percentile) and price position in 52w range.
    pir: 0=52w low, 1=52w high
    ivp: 0-100 (how elevated IV is vs recent history)
    """
    high_ivp  = ivp >= IVP_MIN_TO_SELL   # IV elevated vs history
    low_ivp   = ivp <= IVP_MAX_TO_BUY    # IV cheap vs history
    near_low  = pir < 0.30
    near_high = pir > 0.70
    mid_range = 0.30 <= pir <= 0.70

    # Extra OTM buffer for speculative names
    spec_note = " (speculative — use wider strikes)" if is_speculative else ""

    if strategy == "CSP":
        if high_ivp and near_low:
            return {"score":95, "recommend":True,
                    "signal":f"🔥 EXCELLENT — High IVP ({ivp:.0f}%) + near 52w low = ideal CSP entry{spec_note}"}
        elif high_ivp and mid_range:
            return {"score":80, "recommend":True,
                    "signal":f"✅ GOOD — High IVP ({ivp:.0f}%), reasonable price level{spec_note}"}
        elif high_ivp and near_high:
            return {"score":55, "recommend":True,
                    "signal":f"⚠️ CAUTION — High IVP ({ivp:.0f}%) but stock near 52w high{spec_note}"}
        elif not high_ivp and near_low:
            return {"score":40, "recommend":False,
                    "signal":f"⚠️ WEAK — Low IVP ({ivp:.0f}%), poor premium despite low price"}
        else:
            return {"score":25, "recommend":False,
                    "signal":f"❌ POOR — Low IVP ({ivp:.0f}%), thin premium. Wait for higher IV"}

    elif strategy == "CC":
        if near_low:
            return {"score":10, "recommend":False,
                    "signal":f"❌ AVOID — Never sell CC near 52w low (locks in loss potential)"}
        elif near_high and high_ivp:
            return {"score":90, "recommend":True,
                    "signal":f"🔥 EXCELLENT — Stock near highs + high IVP ({ivp:.0f}%) = fat CC premium"}
        elif near_high and not high_ivp:
            return {"score":75, "recommend":True,
                    "signal":f"✅ GOOD — Stock near highs, decent CC level"}
        elif mid_range and high_ivp:
            return {"score":65, "recommend":True,
                    "signal":f"✅ OK — Elevated IVP ({ivp:.0f}%) boosts CC premium"}
        else:
            return {"score":35, "recommend":False,
                    "signal":f"⚠️ WEAK — Low IVP ({ivp:.0f}%) + mid range. Skip CC"}

    elif strategy == "LEAPS":
        if is_speculative and near_high:
            return {"score":10, "recommend":False,
                    "signal":f"❌ AVOID — Speculative stock near highs, wait for bigger drawdown"}
        elif low_ivp and near_low:
            return {"score":95, "recommend":True,
                    "signal":f"🔥 EXCEPTIONAL — Low IVP ({ivp:.0f}%) + near 52w low = cheapest LEAPS entry"}
        elif low_ivp and mid_range:
            return {"score":78, "recommend":True,
                    "signal":f"✅ GOOD — Low IVP ({ivp:.0f}%) = reasonably priced LEAPS"}
        elif low_ivp and near_high:
            return {"score":40, "recommend":False,
                    "signal":f"⚠️ WEAK — Low IV but stock stretched near highs"}
        elif not low_ivp and near_low:
            return {"score":55, "recommend":True,
                    "signal":f"⚠️ MIXED — Good price level but IVP ({ivp:.0f}%) makes LEAPS pricey"}
        elif high_ivp and near_high:
            return {"score":5, "recommend":False,
                    "signal":f"❌ POOR — Expensive options + high price. Don't buy LEAPS"}
        else:
            return {"score":45, "recommend":False,
                    "signal":f"⚠️ NEUTRAL — IVP {ivp:.0f}%, wait for better entry"}

    return {"score":50, "recommend":True, "signal":"Neutral"}


# ─────────────────────────────────────────────
# OPPORTUNITY FINDERS
# ─────────────────────────────────────────────

def find_best_csp(ticker, price, contracts, ivdata, pir):
    is_spec = ticker in SPECULATIVE_TICKERS
    otm_min = 10 if is_spec else 3    # wider buffer for speculative
    otm_max = 25 if is_spec else 18

    timing = timing_score("CSP", pir, ivdata["ivp"], is_spec)
    if not timing["recommend"]: return None, timing
    if not contracts or price <= 0: return None, timing

    # Use ATM IV for accurate delta calculation
    atm_iv = ivdata["iv_current"]
    today  = datetime.now()
    best   = None; best_score = 0

    for c in contracts:
        parsed = parse_option_symbol(c.get("option_symbol",""))
        if not parsed: continue
        expiry, opt_type, strike = parsed
        if opt_type != "P": continue
        dte = (expiry - today).days
        if not (CSP_DTE_MIN <= dte <= CSP_DTE_MAX): continue
        otm_pct = (price - strike) / price * 100
        if not (otm_min <= otm_pct <= otm_max): continue
        bid = float(c.get("nbbo_bid",0) or 0)
        ask = float(c.get("nbbo_ask",0) or 0)
        mid = (bid + ask) / 2
        if mid < ALERT_PREMIUM_MIN: continue
        if mid > strike * 0.25: continue
        # Use ATM IV for delta — much more accurate
        delta = estimate_delta(otm_pct, dte, atm_iv, "P")
        if delta and delta > CSP_DELTA_MAX: continue
        annualized = (mid / strike) * (365 / dte) * 100
        if not (MIN_ANNUALIZED <= annualized <= MAX_ANNUALIZED): continue
        max_contracts = max(1, int((PORTFOLIO_SIZE * get_max_allocation(ticker)) / (strike * 100)))
        score = (timing["score"]/100) * (1 - abs(dte-35)/35) * (1 - abs(otm_pct-10)/10) * mid * (1 + atm_iv)
        if score > best_score:
            best_score = score
            best = {"strike":strike, "expiry":expiry.strftime("%Y-%m-%d"), "dte":dte,
                    "bid":round(bid,2), "ask":round(ask,2), "premium":round(mid,2),
                    "otm_pct":round(otm_pct,1), "iv":round(atm_iv*100,1),
                    "ivp":ivdata["ivp"], "delta":delta,
                    "annualized_return":round(annualized,1),
                    "max_contracts":max_contracts,
                    "collateral":round(strike*100*max_contracts,0),
                    "timing":timing}
    return best, timing


def find_best_cc(ticker, price, qty, avg_cost, contracts, ivdata, pir):
    timing = timing_score("CC", pir, ivdata["ivp"], ticker in SPECULATIVE_TICKERS)
    if not timing["recommend"]: return None, timing
    if not contracts or price <= 0 or qty < 100: return None, timing

    atm_iv        = ivdata["iv_current"]
    today         = datetime.now()
    max_contracts = int(qty / 100)
    best          = None; best_score = 0

    for c in contracts:
        parsed = parse_option_symbol(c.get("option_symbol",""))
        if not parsed: continue
        expiry, opt_type, strike = parsed
        if opt_type != "C": continue
        dte = (expiry - today).days
        if not (CC_DTE_MIN <= dte <= CC_DTE_MAX): continue
        otm_pct = (strike - price) / price * 100
        if not (1 <= otm_pct <= 15): continue
        if avg_cost > 0 and strike < avg_cost * 1.02: continue  # protect cost basis
        bid = float(c.get("nbbo_bid",0) or 0)
        ask = float(c.get("nbbo_ask",0) or 0)
        mid = (bid + ask) / 2
        if mid < ALERT_PREMIUM_MIN: continue
        delta = estimate_delta(otm_pct, dte, atm_iv, "C")
        if delta and delta > CC_DELTA_MAX: continue
        annualized = (mid / price) * (365 / dte) * 100
        if not (MIN_ANNUALIZED <= annualized <= MAX_ANNUALIZED): continue
        score = (timing["score"]/100) * (1 - abs(dte-35)/35) * mid * (1 + atm_iv)
        if score > best_score:
            best_score = score
            best = {"strike":strike, "expiry":expiry.strftime("%Y-%m-%d"), "dte":dte,
                    "bid":round(bid,2), "ask":round(ask,2), "premium":round(mid,2),
                    "otm_pct":round(otm_pct,1), "iv":round(atm_iv*100,1),
                    "ivp":ivdata["ivp"], "delta":delta,
                    "annualized_return":round(annualized,1),
                    "max_contracts":max_contracts, "avg_cost":round(avg_cost,2),
                    "timing":timing}
    return best, timing


def find_best_leaps(ticker, price, contracts, ivdata, pir):
    """
    Deep ITM LEAPS strategy:
    - Target delta 0.80+ (strike ~70-80% of stock price = 20-30% ITM)
    - Minimize extrinsic value (time value paid should be <20% of premium)
    - This gives synthetic stock exposure with defined risk
    - Only fall back to ATM if no deep ITM contracts available
    """
    is_spec = ticker in SPECULATIVE_TICKERS
    timing  = timing_score("LEAPS", pir, ivdata["ivp"], is_spec)
    if not timing["recommend"]: return None, timing
    if not contracts or price <= 0: return None, timing

    atm_iv = ivdata["iv_current"]
    today  = datetime.now()

    candidates = []

    for c in contracts:
        parsed = parse_option_symbol(c.get("option_symbol",""))
        if not parsed: continue
        expiry, opt_type, strike = parsed
        if opt_type != "C": continue
        dte = (expiry - today).days
        if dte < LEAPS_DTE_MIN: continue

        # ITM % (positive = ITM, negative = OTM)
        itm_pct = (price - strike) / price * 100

        # Accept range: 15% ITM to 10% OTM — prefer deep ITM
        if not (-10 <= itm_pct <= 40): continue

        bid = float(c.get("nbbo_bid",0) or 0)
        ask = float(c.get("nbbo_ask",0) or 0)
        mid = (bid + ask) / 2
        if mid < 2.0: continue

        delta = estimate_delta(-itm_pct, dte, atm_iv, "C")  # note: flip sign for delta calc

        # Intrinsic value = how much the option is in the money
        intrinsic  = max(0, price - strike)
        extrinsic  = max(0, mid - intrinsic)
        extrinsic_pct = (extrinsic / mid * 100) if mid > 0 else 100

        # Score: heavily reward low extrinsic % and high delta
        # Deep ITM (low extrinsic) scores much higher
        delta_score    = (delta or 0.5) * 40          # want delta close to 1.0
        extrinsic_score= max(0, (30 - extrinsic_pct)) # reward <20% extrinsic
        timing_s       = timing["score"] / 100
        score          = timing_s * (delta_score + extrinsic_score) * (dte / 365)

        candidates.append({
            "strike": strike,
            "expiry": expiry.strftime("%Y-%m-%d"),
            "dte": dte,
            "bid": round(bid,2), "ask": round(ask,2),
            "premium": round(mid,2),
            "itm_pct": round(itm_pct,1),
            "intrinsic": round(intrinsic,2),
            "extrinsic": round(extrinsic,2),
            "extrinsic_pct": round(extrinsic_pct,1),
            "iv": round(atm_iv*100,1),
            "ivp": ivdata["ivp"],
            "delta": delta,
            "leverage": round(price/mid,1) if mid > 0 else 0,
            "timing": timing,
            "score": score
        })

    if not candidates:
        return None, timing

    # Sort by score — deep ITM wins
    candidates.sort(key=lambda x: x["score"], reverse=True)
    best = candidates[0]

    # Only return if extrinsic is reasonable (<35% of premium)
    if best["extrinsic_pct"] > 35:
        return None, timing

    return best, timing


# ─────────────────────────────────────────────
# PETER LYNCH DISCOVERY
# ─────────────────────────────────────────────

def peter_lynch_screen(known_tickers: list, flow_data: list) -> list:
    """
    Find stocks NOT on watchlist that appear in whale flow
    and pass basic Peter Lynch quality checks:
    - PEG ratio < 1.5 (growth at reasonable price)
    - EPS growth > 15%
    - Reasonable debt
    Returns list of interesting discoveries.
    """
    # Find tickers in flow not on our list
    flow_tickers = set()
    for alert in flow_data:
        t = alert.get("ticker","")
        premium = float(alert.get("total_premium",0) or 0)
        if t and t not in known_tickers and premium > 500_000:
            if alert.get("type") == "call":  # bullish flow only
                flow_tickers.add(t)

    discoveries = []
    for ticker in list(flow_tickers)[:10]:  # check top 10
        try:
            fund = get_fundamentals(ticker)
            peg  = fund.get("peg_ratio")
            eps  = fund.get("eps_growth")
            roe  = fund.get("return_on_equity")
            if peg and eps and 0 < peg < 1.5 and eps > 0.15:
                discoveries.append({
                    "ticker": ticker,
                    "peg_ratio": round(peg,2),
                    "eps_growth": round(eps*100,1),
                    "roe": round(roe*100,1) if roe else None,
                    "why": f"PEG {peg:.1f} + {eps*100:.0f}% EPS growth + whale call flow"
                })
        except: continue

    return sorted(discoveries, key=lambda x: x.get("peg_ratio",99))[:3]


# ─────────────────────────────────────────────
# CLAUDE ANALYSIS
# ─────────────────────────────────────────────

def claude_analyze(csps, ccs, leaps_list, discoveries):
    if not ANTHROPIC_API_KEY: return ""
    all_opps = csps + ccs + leaps_list
    if not all_opps: return ""

    disc_text = json.dumps(discoveries, indent=2) if discoveries else "None found today"
    prompt = f"""You are an expert options income trader managing a $7M portfolio.
Strategy: Sell CSPs/CCs (25-45 DTE, delta 0.20-0.30) for income. Buy LEAPS on conviction.
Position sizing is tiered: mega cap 8%, quality core 6%, standard 4%, opportunistic 2.5%.

SCREENED CSP OPPORTUNITIES (already filtered by IVP, delta, timing):
{json.dumps(csps, indent=2)}

COVERED CALL OPPORTUNITIES (only on held stocks):
{json.dumps(ccs, indent=2)}

LEAPS OPPORTUNITIES:
{json.dumps(leaps_list, indent=2)}

PETER LYNCH DISCOVERIES (not on watchlist, whale flow + quality fundamentals):
{disc_text}

Provide:
1. Best CSP trade — exact strike, expiry, why, execution tip
2. Best CC trade — exact details (if any)
3. Best LEAPS trade — exact details (if any)
4. Any Peter Lynch discovery worth investigating
5. One-line market timing summary (IVP environment)
6. Hard pass on anything that looks wrong

Be direct and specific. No fluff."""

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key":ANTHROPIC_API_KEY,
                     "anthropic-version":"2023-06-01",
                     "content-type":"application/json"},
            json={"model":"claude-sonnet-4-20250514","max_tokens":800,
                  "messages":[{"role":"user","content":prompt}]},
            timeout=30
        )
        if r.status_code == 200:
            return r.json()["content"][0]["text"]
        print(f"Claude error {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"Claude exception: {e}")
    return ""


# ─────────────────────────────────────────────
# TELEGRAM FORMATTERS
# ─────────────────────────────────────────────

def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[TELEGRAM]\n{message}\n"); return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id":TELEGRAM_CHAT_ID,"text":message,"parse_mode":"Markdown"},
            timeout=10
        )
        print("✅ Sent!" if r.status_code==200 else f"TG error: {r.text[:80]}")
    except Exception as e:
        print(f"TG error: {e}")


def fmt_csp(opp):
    t = opp["csp"]["timing"]; s = opp["sizing"]
    d = f" | δ {opp['csp']['delta']}" if opp['csp'].get('delta') else ""
    return "\n".join([
        f"💰 *CSP — {opp['ticker']} @ ${opp['price']}*",
        f"_{t['signal']}_",
        f"  Tier: {s['tier']} | Max: {s['max_pct']}% (${PORTFOLIO_SIZE*s['max_pct']/100:,.0f})",
        f"  Sell Put ${opp['csp']['strike']} | {opp['csp']['expiry']} | {opp['csp']['dte']} DTE",
        f"  Bid ${opp['csp']['bid']} / Ask ${opp['csp']['ask']} | Mid ${opp['csp']['premium']}",
        f"  {opp['csp']['otm_pct']}% OTM | IV: {opp['csp']['iv']}% | IVP: {opp['csp']['ivp']:.0f}%{d}",
        f"  Annualized: {opp['csp']['annualized_return']}% | Max {opp['csp']['max_contracts']} contracts",
        f"  Collateral: ${opp['csp']['collateral']:,.0f}",
        f"  Current: ${s['current_value']:,.0f} ({s['current_pct']}%) | Room: ${s['room_usd']:,.0f}",
        f"_Scanned {datetime.now().strftime('%b %d %H:%M')} ET_"
    ])


def fmt_cc(opp):
    t = opp["cc"]["timing"]; s = opp["sizing"]
    d = f" | δ {opp['cc']['delta']}" if opp['cc'].get('delta') else ""
    return "\n".join([
        f"📈 *CC — {opp['ticker']} @ ${opp['price']}*",
        f"_{t['signal']}_",
        f"  You hold {int(s['quantity'])} shares @ ${opp['cc']['avg_cost']} avg cost",
        f"  Sell Call ${opp['cc']['strike']} | {opp['cc']['expiry']} | {opp['cc']['dte']} DTE",
        f"  Bid ${opp['cc']['bid']} / Ask ${opp['cc']['ask']} | Mid ${opp['cc']['premium']}",
        f"  {opp['cc']['otm_pct']}% OTM | IV: {opp['cc']['iv']}% | IVP: {opp['cc']['ivp']:.0f}%{d}",
        f"  Annualized: {opp['cc']['annualized_return']}% | {opp['cc']['max_contracts']} contracts",
        f"_Scanned {datetime.now().strftime('%b %d %H:%M')} ET_"
    ])


def fmt_leaps(opp):
    t = opp["leaps"]["timing"]; s = opp["sizing"]
    l = opp["leaps"]
    d = f" | δ {l['delta']}" if l.get('delta') else ""
    itm_label = f"{l['itm_pct']}% ITM" if l['itm_pct'] > 0 else f"{abs(l['itm_pct'])}% OTM"
    return "\n".join([
        f"🚀 *LEAPS — {opp['ticker']} @ ${opp['price']}*",
        f"_{t['signal']}_",
        f"  52w: ${opp['w52_low']} — ${opp['w52_high']} (at {opp['pir']*100:.0f}%)",
        f"  Buy Call ${l['strike']} | {l['expiry']} | {l['dte']} DTE",
        f"  Bid ${l['bid']} / Ask ${l['ask']} | Cost ${l['premium']}",
        f"  {itm_label} | IVP: {l['ivp']:.0f}%{d}",
        f"  Intrinsic: ${l['intrinsic']} | Extrinsic: ${l['extrinsic']} ({l['extrinsic_pct']}% of cost)",
        f"  Leverage: {l['leverage']}x",
        f"  Tier: {s['tier']} | Room: ${s['room_usd']:,.0f}",
        f"_Scanned {datetime.now().strftime('%b %d %H:%M')} ET_"
    ])


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def run_scanner():
    print(f"\n{'='*60}")
    print(f"🐋 WHALE INTELLIGENCE SCANNER v4")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ET")
    print(f"{'='*60}\n")

    print("📊 Fetching IBKR positions...")
    ibkr         = get_ibkr_positions()
    stk_holdings = {k:v for k,v in ibkr.items() if v.get("asset_class")=="STK"}

    all_tickers  = CORE_STOCKS + OPPORTUNISTIC_STOCKS
    print(f"\n💹 Fetching market data ({len(all_tickers)} stocks)...")
    mkt  = get_market_data(all_tickers)
    ok   = sum(1 for v in mkt.values() if v["price"]>0)
    print(f"   {ok}/{len(all_tickers)} prices obtained")

    print("\n🌊 Fetching market-wide flow for Peter Lynch discovery...")
    flow_data    = get_flow_alerts_market()
    known        = set(all_tickers)

    csp_opps  = []
    cc_opps   = []
    leaps_opps= []

    print(f"\n🔍 Scanning stocks...")
    for ticker in all_tickers:
        is_core = ticker in CORE_STOCKS
        md      = mkt.get(ticker,{})
        price   = md.get("price",0)
        if price <= 0: continue

        w52h = md.get("week52_high", price)
        w52l = md.get("week52_low",  price)
        pir  = position_in_range(price, w52l, w52h)

        contracts = get_option_contracts(ticker)
        if not contracts: continue

        ivdata = calculate_ivp(contracts)

        sizing = position_check(ticker, ibkr)

        # ── CSP ──
        if sizing["status"] != "OVERWEIGHT":
            csp, _ = find_best_csp(ticker, price, contracts, ivdata, pir)
            if csp:
                csp_opps.append({
                    "ticker":ticker, "price":price, "pir":pir,
                    "w52_low":w52l, "w52_high":w52h,
                    "csp":csp, "sizing":sizing,
                    "score": csp["timing"]["score"] + csp["annualized_return"]*0.5
                })
                print(f"  {ticker}: 💰 CSP ${csp['strike']} | {csp['annualized_return']}% | δ{csp['delta']} | IVP {ivdata['ivp']:.0f}%")

        # ── CC (held stocks only) ──
        holding = stk_holdings.get(ticker,{})
        qty = holding.get("quantity",0)
        avg = holding.get("avg_cost",0)
        if qty >= 100:
            cc, _ = find_best_cc(ticker, price, qty, avg, contracts, ivdata, pir)
            if cc:
                cc_opps.append({
                    "ticker":ticker, "price":price, "pir":pir,
                    "cc":cc, "sizing":sizing,
                    "score": cc["timing"]["score"] + cc["annualized_return"]*0.5
                })
                print(f"  {ticker}: 📈 CC  ${cc['strike']} | {cc['annualized_return']}% | δ{cc['delta']} | IVP {ivdata['ivp']:.0f}%")

        # ── LEAPS ──
        leaps, _ = find_best_leaps(ticker, price, contracts, ivdata, pir)
        if leaps:
            leaps_opps.append({
                "ticker":ticker, "price":price, "pir":pir,
                "w52_low":w52l, "w52_high":w52h,
                "leaps":leaps, "sizing":sizing,
                "score": leaps["timing"]["score"] + leaps["leverage"]*2
            })
            print(f"  {ticker}: 🚀 LEAPS ${leaps['strike']} | {leaps['dte']}DTE | δ{leaps['delta']} | IVP {ivdata['ivp']:.0f}%")

    # Sort and take top 3 each
    csp_opps.sort(   key=lambda x: x["score"], reverse=True)
    cc_opps.sort(    key=lambda x: x["score"], reverse=True)
    leaps_opps.sort( key=lambda x: x["score"], reverse=True)
    top_csps  = csp_opps[:3]
    top_ccs   = cc_opps[:3]
    top_leaps = leaps_opps[:3]

    # Peter Lynch discovery
    print("\n🔬 Running Peter Lynch screen...")
    discoveries = peter_lynch_screen(known, flow_data)
    if discoveries:
        print(f"   Found {len(discoveries)} interesting names: {[d['ticker'] for d in discoveries]}")

    total = len(top_csps)+len(top_ccs)+len(top_leaps)
    if total == 0:
        print("\n✅ No qualifying opportunities today (all filtered by IVP/timing/delta).")
        if discoveries:
            send_telegram(f"🔬 *Peter Lynch Watch*\n\n" +
                         "\n".join([f"*{d['ticker']}* — {d['why']}" for d in discoveries]))
        return

    print(f"\n🏆 {len(top_csps)} CSPs | {len(top_ccs)} CCs | {len(top_leaps)} LEAPS")

    # Claude
    print("\n🧠 Claude analysis...")
    analysis = claude_analyze(top_csps, top_ccs, top_leaps, discoveries)
    if analysis: print(f"\n{analysis}")

    # Send Telegram
    print("\n📱 Sending alerts...")
    if top_csps:
        send_telegram("📋 *TOP CSP OPPORTUNITIES*"); time.sleep(1)
        for o in top_csps:
            send_telegram(fmt_csp(o)); time.sleep(2)

    if top_ccs:
        send_telegram("📋 *TOP COVERED CALL OPPORTUNITIES*"); time.sleep(1)
        for o in top_ccs:
            send_telegram(fmt_cc(o)); time.sleep(2)

    if top_leaps:
        send_telegram("📋 *TOP LEAPS OPPORTUNITIES*"); time.sleep(1)
        for o in top_leaps:
            send_telegram(fmt_leaps(o)); time.sleep(2)

    if discoveries:
        time.sleep(2)
        disc_msg = "🔬 *Peter Lynch Discoveries*\n_Not on your watchlist — whale flow + quality fundamentals_\n\n"
        for d in discoveries:
            disc_msg += f"*{d['ticker']}* — PEG {d['peg_ratio']} | EPS growth {d['eps_growth']}%\n_{d['why']}_\n\n"
        send_telegram(disc_msg)

    if analysis:
        time.sleep(2)
        send_telegram(f"🧠 *Claude's Summary*\n\n{analysis}")

    print("\n✅ Scan complete!")


if __name__ == "__main__":
    run_scanner()
