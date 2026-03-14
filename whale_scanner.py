"""
Whale Intelligence — Personal Options Trading Scanner
v5 — Full framework implementation:
     Earnings blackout, pullback filter, 200MA, PMCC detection,
     Bull Call Spread, tiered position sizing, IVP, deep ITM LEAPS,
     deal quality checklist, Peter Lynch discovery
"""

import os, json, re, math, time
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Optional

# ── API Keys ────────────────────────────────────────────────
UNUSUAL_WHALES_API_KEY = os.environ.get("UW_API_KEY", "")
TELEGRAM_BOT_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID       = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY      = os.environ.get("ANTHROPIC_API_KEY", "")
IBKR_FLEX_TOKEN        = os.environ.get("IBKR_FLEX_TOKEN", "")
IBKR_FLEX_QUERY_ID     = os.environ.get("IBKR_FLEX_QUERY_ID", "")

PORTFOLIO_SIZE = 7_000_000

# ── Tiered position limits ──────────────────────────────────
POSITION_TIERS = {
    "AAPL": 0.08, "AMZN": 0.08, "GOOGL": 0.08, "NVDA": 0.08, "MSFT": 0.08,
    "ASML": 0.06, "TSM":  0.06, "MELI":  0.06, "NVO":  0.06, "BRK-B": 0.06,
    "IBKR": 0.04, "MU":   0.04,
    "__DEFAULT__": 0.025
}

# ── Strategy parameters (from framework doc) ────────────────
CSP_DTE_MIN           = 30;   CSP_DTE_MAX     = 45
CC_DTE_MIN            = 30;   CC_DTE_MAX      = 45
LEAPS_DTE_MIN         = 500   # 2+ years
CSP_DELTA_MIN         = 0.20; CSP_DELTA_MAX   = 0.30   # standard
CC_DELTA_MIN          = 0.15; CC_DELTA_MAX    = 0.25
LEAPS_DELTA_MIN       = 0.80; LEAPS_DELTA_MAX = 0.90
CSP_MIN_ANNUALIZED    = 20.0  # preferred minimum
CC_MIN_ANNUALIZED     = 15.0
MAX_ANNUALIZED        = 120.0 # cap bad data
IVP_MIN_SELL          = 30    # min IVP to sell premium
IVP_MAX_BUY           = 50    # max IVP to buy LEAPS
EARNINGS_BLACKOUT     = 14    # skip CSP/CC if earnings within N days
PULLBACK_MIN          = 0.15  # stock must be ≥15% below 52w high
PULLBACK_MAX          = 0.65  # but not >65% (company may be broken)
BCS_MIN_ROR           = 0.80  # Bull Call Spread min return on risk

CORE_STOCKS = ["AAPL","AMZN","ASML","BRK-B","GOOGL","IBKR","MELI","MU","NVDA","NVO","TSM"]
OPPORTUNISTIC_STOCKS = [
    "BABA","CLS","CRDO","DDOG","FIX","KNX","LULU","NFLX","NOW","POWL",
    "UBER","VRT","VRTX","CPRT","CRSP","GRAB","IBIT","NBIS","PATH","PLTR","TSLA"
]
SPECULATIVE = {"VRTX","CRSP","NBIS","GRAB","PATH","IBIT","PLTR","BABA","CRDO"}

UW_BASE    = "https://api.unusualwhales.com"
UW_HEADERS = {"Authorization": f"Bearer {UNUSUAL_WHALES_API_KEY}"}


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

            ma200 = sum(closes[-200:]) / min(200, len(closes)) if closes else 0
            price = float(meta.get("regularMarketPrice",0))

            data[ticker] = {
                "price":        round(price, 2),
                "week52_high":  round(float(meta.get("fiftyTwoWeekHigh", price)), 2),
                "week52_low":   round(float(meta.get("fiftyTwoWeekLow",  price)), 2),
                "avg_volume":   int(meta.get("averageDailyVolume3Month", 0)),
                "ma200":        round(ma200, 2),
                "above_ma200":  price >= ma200 * 0.97,  # within 3% counts as near MA
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
    try:
        r = requests.get(f"{UW_BASE}/api/option-trades/flow-alerts",
                         headers=UW_HEADERS, timeout=10)
        if r.status_code == 200:
            return r.json().get("data", [])
    except: pass
    return []
# ════════════════════════════════════════════════════════════
# MARKET INTELLIGENCE (Tide, OI Change, Expiry Breakdown)
# ════════════════════════════════════════════════════════════

def get_market_tide() -> dict:
    """
    Market Tide = net options premium flow (calls - puts) market-wide.
    Positive = call premium dominating = bullish environment
    Negative = put premium dominating = bearish/hedging environment
    Returns score -100 to +100 and a human label.
    """
    try:
        r = requests.get(f"{UW_BASE}/api/market/market-tide",
                         headers=UW_HEADERS, timeout=10)
        if r.status_code != 200:
            return {"score": 0, "label": "Unknown", "raw": {}, "available": False}
        data = r.json()
        # UW returns array of {timestamp, call_premium, put_premium, net}
        items = data.get("data", data.get("results", []))
        if not items:
            return {"score": 0, "label": "Unknown", "raw": {}, "available": False}

        # Use most recent entry
        latest = items[-1] if isinstance(items, list) else items
        call_prem = float(latest.get("call_premium", latest.get("calls", 0)) or 0)
        put_prem  = float(latest.get("put_premium",  latest.get("puts",  0)) or 0)
        net       = float(latest.get("net", call_prem - put_prem) or 0)
        total     = call_prem + put_prem

        # Normalize to -100/+100
        score = round((net / total * 100), 1) if total > 0 else 0

        if score > 20:
            label = f"🟢 BULLISH TIDE ({score:+.0f}) — Call premium dominating, good for CSP"
        elif score > 5:
            label = f"🟡 MILD BULLISH TIDE ({score:+.0f}) — Slight call premium edge"
        elif score > -5:
            label = f"⚪ NEUTRAL TIDE ({score:+.0f}) — Balanced, selective trades only"
        elif score > -20:
            label = f"🟠 MILD BEARISH TIDE ({score:+.0f}) — Put premium building, be cautious"
        else:
            label = f"🔴 BEARISH TIDE ({score:+.0f}) — Put premium dominating, avoid new CSPs"

        return {
            "score": score,
            "label": label,
            "call_premium": round(call_prem/1e6, 1),
            "put_premium":  round(put_prem/1e6, 1),
            "net_million":  round(net/1e6, 1),
            "available": True
        }
    except Exception as e:
        print(f"   Market tide error: {e}")
        return {"score": 0, "label": "Tide data unavailable", "available": False}


def get_oi_change() -> dict:
    """
    Overnight OI change — which strikes saw largest new positioning.
    Large put OI increase on a ticker = warning for CSP sellers.
    Large call OI increase = institutions opening new bullish bets.
    Returns dict of {ticker: {call_oi_change, put_oi_change, net_signal}}
    """
    try:
        r = requests.get(f"{UW_BASE}/api/market/oi-change",
                         headers=UW_HEADERS, timeout=10)
        if r.status_code != 200:
            return {}
        items = r.json().get("data", r.json().get("results", []))
        if not items:
            return {}

        oi_signals = {}
        for item in items[:50]:  # top 50 by OI change
            ticker = item.get("ticker","")
            if not ticker: continue
            call_oi = float(item.get("call_oi_change", item.get("calls_oi_change", 0)) or 0)
            put_oi  = float(item.get("put_oi_change",  item.get("puts_oi_change",  0)) or 0)
            if abs(call_oi) < 100 and abs(put_oi) < 100: continue
            net = call_oi - put_oi
            oi_signals[ticker] = {
                "call_oi_change": int(call_oi),
                "put_oi_change":  int(put_oi),
                "net": int(net),
                "signal": ("🟢 Bullish OI" if net > 500
                           else "🔴 Bearish OI" if net < -500
                           else "⚪ Neutral OI")
            }
        return oi_signals
    except Exception as e:
        print(f"   OI change error: {e}")
        return {}


def get_expiry_breakdown(ticker: str) -> dict:
    """
    Where is OI concentrated by strike and expiry?
    Used to avoid selling CSP/CC at strikes targeted by heavy put OI.
    Returns max_pain strike and risky put strikes to avoid.
    """
    try:
        r = requests.get(f"{UW_BASE}/api/stock/{ticker}/expiry-breakdown",
                         headers=UW_HEADERS, timeout=10)
        if r.status_code != 200:
            return {}
        items = r.json().get("data", [])
        if not items:
            return {}

        # Find expiry with most total OI (most relevant)
        best_expiry = None
        best_oi     = 0
        for item in items:
            total_oi = int(item.get("total_oi", 0) or 0)
            if total_oi > best_oi:
                best_oi     = total_oi
                best_expiry = item

        if not best_expiry:
            return {}

        # Max pain = strike where total option value is minimized
        # Approximate: weighted average of put/call OI
        call_oi = float(best_expiry.get("call_oi", 0) or 0)
        put_oi  = float(best_expiry.get("put_oi",  0) or 0)
        max_pain_strike = float(best_expiry.get("max_pain_strike",
                                best_expiry.get("strike", 0)) or 0)

        return {
            "expiry":          best_expiry.get("expiry",""),
            "max_pain_strike": max_pain_strike,
            "call_oi":         int(call_oi),
            "put_oi":          int(put_oi),
            "put_call_ratio":  round(put_oi/call_oi, 2) if call_oi > 0 else 0,
            "high_put_oi":     put_oi > call_oi * 1.5  # warning: heavy put side
        }
    except Exception as e:
        return {}


def market_go_nogo(tide: dict, oi_signals: dict) -> dict:
    """
    Master go/no-go decision for the day.
    Framework: only trade when opportunity is really good.

    Returns:
      sell_premium: bool  — ok to sell CSP/CC today?
      buy_leaps:    bool  — ok to buy LEAPS today?
      score:        0-100 — overall market quality score
      reason:       str   — human explanation
    """
    tide_score = tide.get("score", 0)

    # Count bullish vs bearish OI signals in our watchlists
    all_tickers = set(CORE_STOCKS + OPPORTUNISTIC_STOCKS)
    bull_oi = sum(1 for t,v in oi_signals.items()
                  if t in all_tickers and v["net"] > 500)
    bear_oi = sum(1 for t,v in oi_signals.items()
                  if t in all_tickers and v["net"] < -500)

    # Overall market score (0-100)
    # Tide contributes 60%, OI breadth 40%
    tide_component = min(100, max(0, (tide_score + 50)))  # -50→0, 0→50, +50→100
    oi_component   = 50 + (bull_oi - bear_oi) * 5
    oi_component   = min(100, max(0, oi_component))
    market_score   = round(tide_component * 0.6 + oi_component * 0.4, 1)

    # Decision logic
    if market_score >= 65 and tide_score > 5:
        sell_premium = True
        buy_leaps    = False  # when tide is bullish, options are more expensive
        quality      = "🔥 EXCELLENT DAY — Strong bullish conditions for premium selling"
    elif market_score >= 50:
        sell_premium = True
        buy_leaps    = True
        quality      = "✅ GOOD DAY — Conditions support selective premium selling"
    elif market_score >= 40:
        sell_premium = False
        buy_leaps    = True
        quality      = "⚠️ CAUTIOUS DAY — Skip new CSPs, LEAPS/spreads only"
    else:
        sell_premium = False
        buy_leaps    = True  # market fear = cheap options = good LEAPS entry
        quality      = "🔴 POOR DAY FOR PREMIUM SELLING — Consider LEAPS on quality names"

    return {
        "sell_premium": sell_premium,
        "buy_leaps":    buy_leaps,
        "score":        market_score,
        "tide_score":   tide_score,
        "bull_oi_count":bull_oi,
        "bear_oi_count":bear_oi,
        "quality":      quality,
        "tide_label":   tide.get("label",""),
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
        parsed = parse_option_symbol(c.get("option_symbol",""))
        if not parsed: continue
        expiry, _, _ = parsed
        dte = (expiry - today).days
        if not (25 <= dte <= 50): continue
        iv = float(c.get("implied_volatility",0) or 0)
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


def score_darkpool(trades: list) -> dict:
    if not trades:
        return {"score":50,"total_notional":0,"label":"No dark pool data"}
    total = bullish = 0
    for t in trades[:20]:
        n = float(t.get("size",0)) * float(t.get("price",0))
        total += n
        if float(t.get("price",0)) >= float(t.get("vwap", t.get("price",0))):
            bullish += n
    score = (bullish/total*100) if total > 0 else 50
    return {
        "score": round(score,1),
        "total_notional": round(total,0),
        "label": ("🟢 Institutions accumulating" if score > 55
                  else "🔴 Institutions distributing" if score < 45
                  else "⚪ Mixed")
    }


def get_max_alloc(ticker): return POSITION_TIERS.get(ticker, POSITION_TIERS["__DEFAULT__"])


def position_check(ticker, ibkr):
    pos  = ibkr.get(ticker,{})
    val  = pos.get("market_value",0)
    qty  = pos.get("quantity",0)
    avg  = pos.get("avg_cost",0)
    pct  = (val / PORTFOLIO_SIZE) * 100
    max_a= get_max_alloc(ticker)
    room = max(0, round(PORTFOLIO_SIZE * max_a - val, 0))
    tier = ("Mega cap" if max_a >= 0.08 else "Quality core" if max_a >= 0.06
            else "Standard core" if max_a >= 0.04 else "Opportunistic")
    return {
        "current_value":round(val,0), "quantity":qty, "avg_cost":avg,
        "current_pct":round(pct,2), "max_pct":round(max_a*100,1),
        "room_usd":room, "tier":tier,
        "status":("OVERWEIGHT" if pct > max_a*100*1.2
                  else "FULL"  if pct > max_a*100*0.9
                  else "HAS ROOM" if val > 0 else "NEW POSITION")
    }


# ════════════════════════════════════════════════════════════
# STOCK QUALITY GATE
# Framework: quality stock → pullback → check option yield
# ════════════════════════════════════════════════════════════

def stock_quality_check(ticker, md, earnings_date) -> dict:
    """
    Returns quality score and pass/fail for each criterion.
    Bad traders: scan → highest premium → trade
    Good traders: quality stock → pullback → check option yield
    """
    price    = md["price"]
    w52h     = md["week52_high"]
    w52l     = md["week52_low"]
    pullback = pullback_from_high(price, w52h)
    vol      = md["avg_volume"]
    above_ma = md["above_ma200"]
    ma200    = md["ma200"]

    checks = {}

    # Price > $20
    checks["price_ok"]    = price >= 20

    # Volume > 1M
    checks["volume_ok"]   = vol >= 1_000_000

    # Pullback 15-65% from high (opportunity zone)
    checks["pullback_ok"] = PULLBACK_MIN <= pullback <= PULLBACK_MAX

    # Above 200MA or within 5% of it (near support)
    checks["ma200_ok"]    = above_ma or (ma200 > 0 and price >= ma200 * 0.95)

    # Not in earnings blackout
    days_to_earnings = None
    if earnings_date:
        days_to_earnings = (earnings_date - datetime.now()).days
        checks["earnings_ok"] = days_to_earnings > EARNINGS_BLACKOUT or days_to_earnings < 0
    else:
        checks["earnings_ok"] = True

    # Quality score: sum of passes (0-5)
    quality_score = sum(checks.values())

    return {
        "checks": checks,
        "quality_score": quality_score,
        "pullback": pullback,
        "pullback_pct": round(pullback * 100, 1),
        "days_to_earnings": days_to_earnings,
        "passes": quality_score >= 3,  # need at least 3/5 to proceed
    }


# ════════════════════════════════════════════════════════════
# TIMING INTELLIGENCE (IVP + price position)
# ════════════════════════════════════════════════════════════

def timing_score(strategy, pir, ivp, is_spec=False) -> dict:
    high_ivp  = ivp >= IVP_MIN_SELL
    low_ivp   = ivp <= IVP_MAX_BUY
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
        elif not high_ivp:
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
        if is_spec and near_high:
            return {"score":5,"recommend":False,
                    "signal":"❌ AVOID — Speculative + near highs, wait for bigger drawdown"}
        elif low_ivp and near_low:
            return {"score":95,"recommend":True,
                    "signal":f"🔥 EXCEPTIONAL — IVP {ivp:.0f}% (cheap) + near 52w low"}
        elif low_ivp and not near_high:
            return {"score":78,"recommend":True,
                    "signal":f"✅ GOOD — Low IVP {ivp:.0f}% = reasonably priced LEAPS"}
        elif not low_ivp and near_low:
            return {"score":52,"recommend":True,
                    "signal":f"⚠️ MIXED — Good price but IVP {ivp:.0f}% makes options pricey"}
        else:
            return {"score":15,"recommend":False,
                    "signal":f"❌ POOR — IVP {ivp:.0f}% too high to buy LEAPS"}

    elif strategy == "PMCC":
        # PMCC short call — same as CC but we own LEAPS not stock
        if near_low:
            return {"score":5,"recommend":False,"signal":"❌ AVOID — Don't sell calls near lows"}
        elif high_ivp:
            return {"score":82,"recommend":True,
                    "signal":f"✅ GOOD — IVP {ivp:.0f}% gives fat PMCC premium"}
        else:
            return {"score":40,"recommend":False,
                    "signal":f"⚠️ WEAK — Low IVP {ivp:.0f}% for PMCC short call"}

    elif strategy == "BCS":
        # Bull call spread — want moderate to high IVP, near lows preferred
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
                    "signal":f"⚠️ NEUTRAL — Moderate timing for bull spread"}

    return {"score":50,"recommend":True,"signal":"Neutral"}


# ════════════════════════════════════════════════════════════
# OPPORTUNITY FINDERS
# ════════════════════════════════════════════════════════════

def find_best_csp(ticker, price, contracts, ivdata, pir, quality):
    timing = timing_score("CSP", pir, ivdata["ivp"], ticker in SPECULATIVE)
    if not timing["recommend"]: return None, timing
    if not contracts or price <= 0: return None, timing

    atm_iv = ivdata["iv_current"]
    today  = datetime.now()
    best   = None; best_score = 0

    # Wider OTM for speculative names
    otm_min = 10 if ticker in SPECULATIVE else 3
    otm_max = 20

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
        if mid < 1.0: continue
        if mid > strike * 0.25: continue
        delta = estimate_delta(price, strike, dte, atm_iv, "P")
        if delta is None or not (CSP_DELTA_MIN <= delta <= CSP_DELTA_MAX): continue
        annualized = (mid / strike) * (365 / dte) * 100
        if not (CSP_MIN_ANNUALIZED <= annualized <= MAX_ANNUALIZED): continue
        max_contracts = max(1, int((PORTFOLIO_SIZE * get_max_alloc(ticker)) / (strike * 100)))
        score = (timing["score"]/100) * (quality["quality_score"]/5) * mid * (1 + atm_iv) * (1 - abs(dte-37)/37)
        if score > best_score:
            best_score = score
            best = {"strike":strike,"expiry":expiry.strftime("%Y-%m-%d"),"dte":dte,
                    "bid":round(bid,2),"ask":round(ask,2),"premium":round(mid,2),
                    "otm_pct":round(otm_pct,1),"iv":round(atm_iv*100,1),
                    "ivp":ivdata["ivp"],"delta":delta,
                    "annualized_return":round(annualized,1),
                    "max_contracts":max_contracts,
                    "collateral":round(strike*100*max_contracts,0),
                    "timing":timing}
    return best, timing


def find_best_cc(ticker, price, qty, avg_cost, contracts, ivdata, pir):
    timing = timing_score("CC", pir, ivdata["ivp"])
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
        if avg_cost > 0 and strike < avg_cost * 1.01: continue  # protect cost basis
        bid = float(c.get("nbbo_bid",0) or 0)
        ask = float(c.get("nbbo_ask",0) or 0)
        mid = (bid + ask) / 2
        if mid < 1.0: continue
        delta = estimate_delta(price, strike, dte, atm_iv, "C")
        if delta is None or not (CC_DELTA_MIN <= delta <= CC_DELTA_MAX): continue
        annualized = (mid / price) * (365 / dte) * 100
        if not (CC_MIN_ANNUALIZED <= annualized <= MAX_ANNUALIZED): continue
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
        parsed = parse_option_symbol(c.get("option_symbol",""))
        if not parsed: continue
        expiry, opt_type, strike = parsed
        if opt_type != "C": continue
        dte = (expiry - today).days
        if dte < LEAPS_DTE_MIN: continue
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
        if extrinsic_pct > 30: continue  # hard reject — usually too expensive

        # Extrinsic quality label (from framework)
        if extrinsic_pct < 10:
            ext_label = "🔥 Excellent (<10%)"
        elif extrinsic_pct < 20:
            ext_label = "✅ Good (10-20%)"
        elif extrinsic_pct < 30:
            ext_label = "⚠️ Acceptable (20-30%)"
        else:
            ext_label = "❌ Too expensive (>30%)"

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
        parsed = parse_option_symbol(c.get("option_symbol",""))
        if not parsed: continue
        expiry, opt_type, strike = parsed
        if opt_type != "C": continue
        dte = (expiry - today).days
        if not (CC_DTE_MIN <= dte <= CC_DTE_MAX): continue
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
    Bull Call Spread: buy ITM call, sell OTM call.
    Target ROR ≥ 80%, prefer 100-200%.
    Rank by: stock quality → pullback → ROR → liquidity
    """
    timing = timing_score("BCS", pir, ivdata["ivp"])
    if not timing["recommend"]: return None, timing
    if not contracts or price <= 0: return None, timing

    atm_iv = ivdata["iv_current"]
    today  = datetime.now()
    best   = None; best_score = 0

    # Get calls in 30-60 DTE range
    calls = []
    for c in contracts:
        parsed = parse_option_symbol(c.get("option_symbol",""))
        if not parsed: continue
        expiry, opt_type, strike = parsed
        if opt_type != "C": continue
        dte = (expiry - today).days
        if not (30 <= dte <= 60): continue
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
            if short_c["strike"] - long_c["strike"] > price * 0.15: continue  # not too wide

            debit = long_c["mid"] - short_c["mid"]
            if debit <= 0: continue
            width = short_c["strike"] - long_c["strike"]
            max_profit = width - debit
            if max_profit <= 0: continue
            ror = max_profit / debit  # return on risk

            if ror < BCS_MIN_ROR: continue

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
                    "timing": timing
                }
    return best, timing


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

def claude_analyze(csps, ccs, leaps_list, pmccs, bcss, discoveries) -> str:
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

CSPs: {json.dumps(csps,indent=2)}
CCs: {json.dumps(ccs,indent=2)}
LEAPS: {json.dumps(leaps_list,indent=2)}
PMCCs: {json.dumps(pmccs,indent=2)}
Bull Call Spreads: {json.dumps(bcss,indent=2)}
Peter Lynch Discoveries: {json.dumps(discoveries,indent=2) if discoveries else 'None'}

Give:
1. Best CSP — exact trade, checklist pass/fail, execution price
2. Best CC (if any)
3. Best LEAPS or PMCC (if any)
4. Best Bull Call Spread (if any, ROR focus)
5. Any Peter Lynch discovery worth investigating
6. One-line IVP environment summary
7. Hard pass on anything that fails quality check

Direct, specific, no fluff."""

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
    c = q["checks"]
    flags = []
    if not c.get("pullback_ok"):  flags.append(f"⚠️ Pullback {q['pullback_pct']}% (need 15-65%)")
    if not c.get("ma200_ok"):     flags.append("⚠️ Below 200MA")
    if not c.get("volume_ok"):    flags.append("⚠️ Low volume")
    if q.get("days_to_earnings") and 0 < q["days_to_earnings"] <= EARNINGS_BLACKOUT:
        flags.append(f"🚨 Earnings in {q['days_to_earnings']} days!")
    return (" | ".join(flags)) if flags else f"✅ Quality score {q['quality_score']}/5 | {q['pullback_pct']}% off highs"


def fmt_csp(opp) -> str:
    t = opp["csp"]["timing"]; s = opp["sizing"]; q = opp["quality"]
    d = f" | δ{opp['csp']['delta']}" if opp['csp'].get('delta') else ""
    return "\n".join([
        f"💰 *CSP — {opp['ticker']} @ ${opp['price']}*",
        f"_{t['signal']}_",
        f"  {fmt_quality(q)}",
        f"  {opp['darkpool']['label']} | ${opp['darkpool']['total_notional']:,.0f} notional",
        f"  Tier: {s['tier']} | Max: {s['max_pct']}%",
        f"  Sell Put ${opp['csp']['strike']} | {opp['csp']['expiry']} | {opp['csp']['dte']} DTE",
        f"  Bid ${opp['csp']['bid']} / Ask ${opp['csp']['ask']}",
        f"  {opp['csp']['otm_pct']}% OTM | IV {opp['csp']['iv']}% | IVP {opp['csp']['ivp']:.0f}%{d}",
        f"  Annualized: {opp['csp']['annualized_return']}% | {opp['csp']['max_contracts']} contracts",
        f"  Collateral: ${opp['csp']['collateral']:,.0f} | Room: ${s['room_usd']:,.0f}",
        *([f"  ⚠️ OI Signal: {opp['oi_signal']['signal']} (calls {opp['oi_signal']['call_oi_change']:+,} / puts {opp['oi_signal']['put_oi_change']:+,})"]
           if opp.get("oi_signal") else []),
        *([f"  📍 Max Pain: ${opp['expiry_breakdown']['max_pain_strike']} | P/C ratio: {opp['expiry_breakdown']['put_call_ratio']}"]
           if opp.get("expiry_breakdown") and opp["expiry_breakdown"].get("max_pain_strike") else []),
        f"_Scanned {datetime.now().strftime('%b %d %H:%M')} ET_"
    ])


def fmt_cc(opp) -> str:
    t = opp["cc"]["timing"]; s = opp["sizing"]
    d = f" | δ{opp['cc']['delta']}" if opp['cc'].get('delta') else ""
    return "\n".join([
        f"📈 *CC — {opp['ticker']} @ ${opp['price']}*",
        f"_{t['signal']}_",
        f"  {opp['darkpool']['label']} | ${opp['darkpool']['total_notional']:,.0f} notional",
        f"  Hold {int(s['quantity'])} shares @ ${opp['cc']['avg_cost']} avg",
        f"  Sell Call ${opp['cc']['strike']} | {opp['cc']['expiry']} | {opp['cc']['dte']} DTE",
        f"  Bid ${opp['cc']['bid']} / Ask ${opp['cc']['ask']}",
        f"  {opp['cc']['otm_pct']}% OTM | IV {opp['cc']['iv']}% | IVP {opp['cc']['ivp']:.0f}%{d}",
        f"  Annualized: {opp['cc']['annualized_return']}% | {opp['cc']['max_contracts']} contracts",
        f"_Scanned {datetime.now().strftime('%b %d %H:%M')} ET_"
    ])


def fmt_leaps(opp) -> str:
    t = opp["leaps"]["timing"]; s = opp["sizing"]; l = opp["leaps"]
    d = f" | δ{l['delta']}" if l.get('delta') else ""
    itm = f"{l['itm_pct']}% ITM" if l['itm_pct'] > 0 else f"{abs(l['itm_pct'])}% OTM"
    return "\n".join([
        f"🚀 *LEAPS — {opp['ticker']} @ ${opp['price']}*",
        f"_{t['signal']}_",
        f"  {opp['darkpool']['label']} | ${opp['darkpool']['total_notional']:,.0f} notional",
        f"  52w: ${opp['w52_low']} — ${opp['w52_high']} | {opp['pullback_pct']}% off high",
        f"  Buy Call ${l['strike']} | {l['expiry']} | {l['dte']} DTE",
        f"  Bid ${l['bid']} / Ask ${l['ask']} | Cost ${l['premium']}",
        f"  {itm} | IVP {l['ivp']:.0f}%{d}",
        f"  Intrinsic: ${l['intrinsic']} | Extrinsic: ${l['extrinsic']} — {l.get('ext_label', str(l['extrinsic_pct'])+'%')}",
        f"  Leverage: {l['leverage']}x | Tier: {s['tier']} | Room: ${s['room_usd']:,.0f}",
        f"_Scanned {datetime.now().strftime('%b %d %H:%M')} ET_"
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
        f"_Scanned {datetime.now().strftime('%b %d %H:%M')} ET_"
    ])


def fmt_bcs(opp) -> str:
    t = opp["bcs"]["timing"]; b = opp["bcs"]; q = opp["quality"]
    return "\n".join([
        f"📊 *Bull Call Spread — {opp['ticker']} @ ${opp['price']}*",
        f"_{t['signal']}_",
        f"  {opp['darkpool']['label']} | ${opp['darkpool']['total_notional']:,.0f} notional",
        f"  {fmt_quality(q)}",
        f"  Buy ${b['long_strike']} Call / Sell ${b['short_strike']} Call",
        f"  Expiry: {b['expiry']} | {b['dte']} DTE",
        f"  Debit: ${b['debit']} | Max Profit: ${b['max_profit']}",
        f"  Return on Risk: {b['ror']}% | Breakeven: ${b['breakeven']}",
        f"  IVP: {b['ivp']:.0f}%",
        f"_Scanned {datetime.now().strftime('%b %d %H:%M')} ET_"
    ])


# ════════════════════════════════════════════════════════════
# MAIN SCANNER
# ════════════════════════════════════════════════════════════

def run_scanner():
    print(f"\n{'='*60}")
    print(f"🐋 WHALE INTELLIGENCE v5 — {datetime.now().strftime('%Y-%m-%d %H:%M')} ET")
    print(f"   Framework: Quality → Pullback → Option Yield")
    print(f"{'='*60}\n")

    print("📊 IBKR positions...")
    ibkr     = get_ibkr_positions()
    stk_hold = {k:v for k,v in ibkr.items() if v.get("asset_class")=="STK"}

    all_tickers = CORE_STOCKS + OPPORTUNISTIC_STOCKS
    print(f"💹 Market data ({len(all_tickers)} stocks)...")
    mkt = get_market_data(all_tickers)
    ok  = sum(1 for v in mkt.values() if v["price"]>0)
    print(f"   {ok}/{len(all_tickers)} prices ✓")

    print("🌊 Market intelligence...")
    flow       = get_flow_alerts_market()

    print("   📊 Fetching Market Tide...")
    tide       = get_market_tide()
    print(f"   {tide['label']}")

    print("   📈 Fetching OI Changes...")
    oi_signals = get_oi_change()
    print(f"   OI data for {len(oi_signals)} tickers")

    # ── GO / NO-GO DECISION ──────────────────────────────────
    gng = market_go_nogo(tide, oi_signals)
    print(f"\n{'='*50}")
    print(f"📡 MARKET QUALITY SCORE: {gng['score']}/100")
    print(f"   {gng['quality']}")
    print(f"   Tide: {gng['tide_score']:+.1f} | Bull OI: {gng['bull_oi_count']} | Bear OI: {gng['bear_oi_count']}")
    print(f"   Sell Premium: {'✅ YES' if gng['sell_premium'] else '❌ NO'}")
    print(f"   Buy LEAPS:    {'✅ YES' if gng['buy_leaps'] else '❌ NO'}")
    print(f"{'='*50}\n")

    # Send morning market briefing to Telegram
    morning_msg = (
        f"📡 *Market Intelligence — {datetime.now().strftime('%b %d %H:%M')} ET*\n\n"
        f"{tide['label']}\n"
        f"Market Score: {gng['score']}/100\n"
        f"{gng['quality']}\n\n"
        f"Sell Premium: {'✅ YES' if gng['sell_premium'] else '❌ SKIP TODAY'}\n"
        f"Buy LEAPS: {'✅ YES' if gng['buy_leaps'] else '⏳ WAIT'}"
    )
    send_telegram(morning_msg)
    time.sleep(2)

    # If market conditions are poor — skip premium selling, maybe skip entirely
    if not gng["sell_premium"] and not gng["buy_leaps"]:
        print("🚫 Market conditions too poor. No scan today.")
        send_telegram("🚫 *No trades today* — Market conditions don't meet quality threshold.")
        return

    csp_opps = []; cc_opps  = []; leaps_opps = []
    pmcc_opps= []; bcs_opps = []

    print(f"\n🔍 Scanning {len(all_tickers)} stocks...")
    for ticker in all_tickers:
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

        contracts  = get_option_contracts(ticker)
        if not contracts: continue

        ivdata     = calculate_ivp(contracts)
        sizing     = position_check(ticker, ibkr)
        dp         = score_darkpool(get_darkpool(ticker))
        dp_boost   = 1.2 if dp["score"] > 55 else 0.9 if dp["score"] < 45 else 1.0

        # OI signal for this ticker — warns if puts are being targeted
        oi_sig     = oi_signals.get(ticker, {})
        oi_warning = oi_sig.get("net", 0) < -500  # heavy new put OI = warning

        # Expiry breakdown — find max pain and risky strikes
        exp_bdown  = get_expiry_breakdown(ticker)

        base = {"ticker":ticker,"price":price,"pir":pir,
                "w52_low":w52l,"w52_high":w52h,
                "pullback_pct":round(pullback*100,1),
                "ivp":ivdata["ivp"],"quality":quality,
                "sizing":sizing,"darkpool":dp,
                "oi_signal":oi_sig,"expiry_breakdown":exp_bdown,
                "oi_warning":oi_warning}

        # ── CSP ──────────────────────────────────────────
        if (gng["sell_premium"]
                and sizing["status"] != "OVERWEIGHT"
                and quality["checks"].get("earnings_ok",True)
                and not oi_warning):  # skip if heavy new put OI
            csp, _ = find_best_csp(ticker, price, contracts, ivdata, pir, quality)
            if csp:
                csp_opps.append({**base,"csp":csp,
                    "score":csp["timing"]["score"]*quality["quality_score"]*csp["annualized_return"]*dp_boost})
                print(f"  {ticker}: 💰 CSP ${csp['strike']} {csp['annualized_return']}% ann δ{csp['delta']} IVP{ivdata['ivp']:.0f}%")

        # ── CC ───────────────────────────────────────────
        holding = stk_hold.get(ticker,{})
        qty = holding.get("quantity",0); avg = holding.get("avg_cost",0)
        if (gng["sell_premium"]
                and qty >= 100
                and quality["checks"].get("earnings_ok",True)):
            cc, _ = find_best_cc(ticker, price, qty, avg, contracts, ivdata, pir)
            if cc:
                cc_opps.append({**base,"cc":cc,
                    "score":cc["timing"]["score"]*cc["annualized_return"]})
                print(f"  {ticker}: 📈 CC  ${cc['strike']} {cc['annualized_return']}% ann δ{cc['delta']}")

        # ── LEAPS ────────────────────────────────────────
        if gng["buy_leaps"]:
         leaps, _ = find_best_leaps(ticker, price, contracts, ivdata, pir)
        else:
         leaps = None
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

    total = sum(len(x) for x in [top_csps,top_ccs,top_leaps,top_pmccs,top_bcss])
    print(f"\n🏆 {len(top_csps)} CSPs | {len(top_ccs)} CCs | {len(top_leaps)} LEAPS | "
          f"{len(top_pmccs)} PMCCs | {len(top_bcss)} Spreads")

    # ── Peter Lynch ───────────────────────────────────────
    print("🔬 Peter Lynch screen...")
    discoveries = peter_lynch_screen(set(all_tickers), flow)
    if discoveries:
        print(f"   Found: {[d['ticker'] for d in discoveries]}")

    if total == 0 and not discoveries:
        print("✅ No qualifying opportunities today.")
        return

    # ── Claude analysis ───────────────────────────────────
    print("🧠 Claude analysis...")
    analysis = claude_analyze(top_csps,top_ccs,top_leaps,top_pmccs,top_bcss,discoveries)
    if analysis: print(f"\n{analysis}")

    # ── Telegram ─────────────────────────────────────────
    print("\n📱 Sending...")
    if top_csps:
        send_telegram("📋 *TOP CSP OPPORTUNITIES*"); time.sleep(1)
        for o in top_csps: send_telegram(fmt_csp(o)); time.sleep(2)
    if top_ccs:
        send_telegram("📋 *TOP COVERED CALL OPPORTUNITIES*"); time.sleep(1)
        for o in top_ccs: send_telegram(fmt_cc(o)); time.sleep(2)
    if top_leaps:
        send_telegram("📋 *TOP LEAPS OPPORTUNITIES*"); time.sleep(1)
        for o in top_leaps: send_telegram(fmt_leaps(o)); time.sleep(2)
    if top_pmccs:
        send_telegram("📋 *PMCC — SELL CALLS AGAINST YOUR LEAPS*"); time.sleep(1)
        for o in top_pmccs: send_telegram(fmt_pmcc(o)); time.sleep(2)
    if top_bcss:
        send_telegram("📋 *TOP BULL CALL SPREADS*"); time.sleep(1)
        for o in top_bcss: send_telegram(fmt_bcs(o)); time.sleep(2)
    if discoveries:
        time.sleep(2)
        msg = "🔬 *Peter Lynch Discoveries*\n_Not on watchlist — quality fundamentals + whale flow_\n\n"
        for d in discoveries:
            msg += f"*{d['ticker']}* — PEG {d['peg_ratio']} | EPS +{d['eps_growth']}% | Flow {d['whale_flow']}\n"
        send_telegram(msg)
    if analysis:
        time.sleep(2)
        send_telegram(f"🧠 *Claude Summary*\n\n{analysis}")

    print("\n✅ Done!")


if __name__ == "__main__":
    run_scanner()
