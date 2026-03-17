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

UNUSUAL_WHALES_API_KEY = os.environ.get("UW_API_KEY", "")
TELEGRAM_BOT_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID       = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY      = os.environ.get("ANTHROPIC_API_KEY", "")
IBKR_FLEX_TOKEN        = os.environ.get("IBKR_FLEX_TOKEN", "")
IBKR_FLEX_QUERY_ID     = os.environ.get("IBKR_FLEX_QUERY_ID", "")

PORTFOLIO_SIZE = 7_000_000

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
# Gap risk filter
GAP_RISK_PCT          = 0.08  # skip if stock moved >8% in single day
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
    "CLS","CRDO","DDOG","FIX","KNX","NFLX","NOW","POWL",
    "UBER","VRT","IBIT","TSLA",
    # Watchlist only — scan but apply extra caution (wider strikes, tighter timing)
    "BABA",    # China risk — LEAPS only, no CSP
    "CPRT",    # Downtrend, wait for 200MA stabilization
    "LULU",    # Growth slowdown, unclear if temporary
    "PLTR",    # Extreme valuation, opportunistic only
    "VRTX",    # Biotech, wide strikes required
]
# Removed entirely (scanner will skip):
# GRAB  — decelerating growth, poor liquidity, geopolitical risk
# NBIS  — no profits, too early stage for options income
# CRSP  — binary biotech, dangerous for CSP (gap risk)
# PATH  — declining growth, not quality compounder
# NVO   — moved to CORE watchlist below (broken thesis, monitor for re-entry)

# Speculative tickers — require wider OTM buffers, stricter timing
SPECULATIVE = {"VRTX","IBIT","PLTR","BABA","CRDO","LULU"}

# Watchlist only — scanner will suggest LEAPS only, never CSP/CC
# until thesis recovers
LEAPS_ONLY = {"BABA", "CPRT"}

# NVO is now a Core holding with valuation awareness

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

            closes_clean = [c for c in closes if c is not None]
            ma200 = sum(closes_clean[-200:]) / min(200, len(closes_clean)) if closes_clean else 0
            ma50  = sum(closes_clean[-50:])  / min(50,  len(closes_clean)) if closes_clean else 0
            price = float(meta.get("regularMarketPrice", 0))
            prev_close = float(meta.get("chartPreviousClose", price) or price)
            day_change_pct = abs(price - prev_close) / prev_close if prev_close > 0 else 0

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
        # Debug: print raw structure to identify correct field names
        items = data.get("data", data.get("results", []))
        if not items:
            print(f"   Tide raw keys: {list(data.keys()) if isinstance(data,dict) else 'list'}")
            print(f"   Tide raw sample: {str(data)[:200]}")
            return {"score": 0, "label": "Tide: no data from API", "available": False}

        latest = items[-1] if isinstance(items, list) else items

        # Correct field names from UW API
        call_prem = float(latest.get("net_call_premium", 0) or 0)
        put_prem  = abs(float(latest.get("net_put_premium", 0) or 0))  # stored as negative
        net       = call_prem - put_prem
        total     = call_prem + put_prem
        print(f"   Tide: calls=${call_prem/1e6:.1f}M puts=${put_prem/1e6:.1f}M net=${net/1e6:.1f}M")
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
    """
    Fetch SPIKE index from Unusual Whales.
    SPIKE is UW's proprietary fear gauge based on options flow.
    Similar to VIX but calculated from actual order flow, not just prices.
    High SPIKE = market fear elevated = great time to sell CSP/CC premium.
    Low SPIKE = complacency = avoid selling premium, consider buying LEAPS.
    """
    try:
        r = requests.get(f"{UW_BASE}/api/market/spike",
                         headers=UW_HEADERS, timeout=8)
        if r.status_code != 200:
            return {"spike": 0, "label": "SPIKE unavailable", "available": False}
        data  = r.json()
        items = data.get("data", data.get("results", []))
        if not items:
            # SPIKE endpoint exists but returns empty on this plan
            # Fall back to VIX-based fear gauge only
            print("   SPIKE: no data (not available on current UW plan)")
            return {"spike": 0, "label": None, "available": False}

        latest = items[-1] if isinstance(items, list) else items
        spike  = float(latest.get("spike",
                  latest.get("value",
                  latest.get("index",
                  latest.get("score", 0)))) or 0)

        if spike < 20:
            label  = f"😴 SPIKE {spike:.1f} — Low fear (options flow calm). Avoid selling premium."
            regime = "low"
            action = "Consider LEAPS — options are cheap"
        elif spike < 30:
            label  = f"😐 SPIKE {spike:.1f} — Moderate fear. Selective premium selling OK."
            regime = "moderate"
            action = "Selective CSP/CC on strongest setups only"
        elif spike < 40:
            label  = f"😬 SPIKE {spike:.1f} — Elevated fear. Good CSP/CC opportunity."
            regime = "elevated"
            action = "Good time to sell premium on quality stocks"
        else:
            label  = f"😱 SPIKE {spike:.1f} — Extreme fear. Exceptional premium opportunity."
            regime = "extreme"
            action = "Excellent CSP/CC conditions — be selective on quality"

        return {"spike": spike, "label": label, "regime": regime,
                "action": action, "available": True}
    except Exception as e:
        return {"spike": 0, "label": "SPIKE unavailable", "available": False}




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
            # Try multiple field variations
            # UW OI change: use prev premium volume as proxy for directional bias
            # ask_volume > bid_volume = bought = bullish
            ask_vol = float(item.get("prev_ask_volume", 0) or 0)
            bid_vol = float(item.get("prev_bid_volume", 0) or 0)
            sym     = item.get("option_symbol", "")
            is_call = "C" in sym
            is_put  = "P" in sym
            premium = float(item.get("prev_total_premium", 0) or 0)
            # Net: positive = call buying, negative = put buying
            direction = (ask_vol - bid_vol)
            call_oi = premium if (is_call and direction > 0) else 0
            put_oi  = premium if (is_put  and direction > 0) else 0
            if abs(call_oi) < 100 and abs(put_oi) < 100: continue
            net_flow = call_oi - put_oi
            existing = oi_signals.get(ticker, {"call_flow":0,"put_flow":0})
            oi_signals[ticker] = {
                "call_flow":  existing["call_flow"] + call_oi,
                "put_flow":   existing["put_flow"]  + put_oi,
                "net":        existing["call_flow"] + call_oi - existing["put_flow"] - put_oi,
                "signal": ("🟢 Bullish OI" if net_flow > 100_000
                           else "🔴 Bearish OI" if net_flow < -100_000
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


def market_go_nogo(tide: dict, oi_signals: dict,
                   vix_data: dict, spike_data: dict,
                   spy_regime: dict = None) -> dict:
    """
    Master go/no-go decision using VIX + SPIKE + Market Tide + OI breadth.
    Framework: only trade when opportunity is really good.
    """
    tide_score  = tide.get("score", 0)
    vix         = vix_data.get("vix", 20)
    spike       = spike_data.get("spike", 25)
    vix_regime  = vix_data.get("regime", "moderate")
    spike_regime= spike_data.get("regime", "moderate")

    all_tickers = set(CORE_STOCKS + OPPORTUNISTIC_STOCKS)
    bull_oi = sum(1 for t,v in oi_signals.items()
                  if t in all_tickers and v["net"] > 500)
    bear_oi = sum(1 for t,v in oi_signals.items()
                  if t in all_tickers and v["net"] < -500)

    # Component scores (0-100 each)
    # VIX: higher = better for premium selling
    vix_score = (0 if vix < 12 else 20 if vix < 15 else
                 35 if vix < 20 else 60 if vix < 25 else
                 80 if vix < 35 else 95)

    # SPIKE: higher = better for premium selling
    spike_score = (0 if spike < 15 else 20 if spike < 20 else
                   45 if spike < 30 else 75 if spike < 40 else 95)

    # Tide: positive = good for selling
    tide_component = min(100, max(0, tide_score + 50))

    # OI breadth
    oi_component = min(100, max(0, 50 + (bull_oi - bear_oi) * 5))

    # Weighted score: VIX 30%, SPIKE 30%, Tide 25%, OI 15%
    market_score = round(
        vix_score   * 0.30 +
        spike_score * 0.30 +
        tide_component * 0.25 +
        oi_component   * 0.15, 1
    )
    # S&P below 200MA: warning regime — reduce score by 15 points
    spy_warning = spy_regime and not spy_regime.get("above_ma200", True)
    if spy_warning:
        market_score = max(0, market_score - 15)

    # Go/no-go logic
    if market_score >= 65:
        sell_premium = True
        buy_leaps    = vix_regime in ("low","low_moderate")  # cheap options = LEAPS
        quality      = "🔥 EXCELLENT CONDITIONS — High conviction day for premium selling"
    elif market_score >= 50:
        sell_premium = True
        buy_leaps    = True
        quality      = "✅ GOOD CONDITIONS — Selective premium selling supported"
    elif market_score >= 38:
        sell_premium = False
        buy_leaps    = True
        quality      = "⚠️ CAUTIOUS — Skip new CSPs today, LEAPS/spreads only"
    else:
        sell_premium = False
        buy_leaps    = False
        quality      = "🔴 POOR CONDITIONS — No new positions today. Watch and wait."

    return {
        "sell_premium":  sell_premium,
        "buy_leaps":     buy_leaps,
        "score":         market_score,
        "tide_score":    tide_score,
        "vix":           vix,
        "spike":         spike,
        "bull_oi_count": bull_oi,
        "bear_oi_count": bear_oi,
        "quality":       quality,
        "vix_regime":    vix_regime,
        "spike_regime":  spike_regime,
        "spy_warning":   spy_warning if "spy_warning" in dir() else False,
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
]

# All tickers for scanning
ALL_TICKERS = CORE_STOCKS + GROWTH_STOCKS + CYCLICAL_STOCKS + OPPORTUNISTIC_STOCKS

# Speculative — wider OTM buffers required
SPECULATIVE = {"IBIT", "BABA", "CRDO", "LULU"}

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

# Dark Pool notional thresholds by tier (notable, significant)
DP_THRESHOLDS = {
    # Mega cap
    "AAPL":  (50_000_000, 200_000_000),
    "NVDA":  (50_000_000, 200_000_000),
    "AMZN":  (50_000_000, 200_000_000),
    "GOOGL": (50_000_000, 200_000_000),
    "MSFT":  (50_000_000, 200_000_000),
    "META":  (50_000_000, 200_000_000),
    # Large cap core
    "ASML":  (25_000_000, 100_000_000),
    "MELI":  (25_000_000, 100_000_000),
    "TSM":   (25_000_000, 100_000_000),
    "BRK-B": (25_000_000, 100_000_000),
    "CPRT":  (20_000_000,  80_000_000),
    "VRTX":  (20_000_000,  80_000_000),
    "NVO":   (20_000_000,  80_000_000),
    "NFLX":  (20_000_000,  80_000_000),
    # Standard
    "IBKR":  (15_000_000,  50_000_000),
    "MU":    (15_000_000,  50_000_000),
    "NOW":   (15_000_000,  50_000_000),
    "DDOG":  (10_000_000,  40_000_000),
    "UBER":  (10_000_000,  40_000_000),
    "PLTR":  (10_000_000,  40_000_000),
    "__DEFAULT__": (10_000_000, 30_000_000),
}
DP_LEAPS_MIN = 50_000_000  # LEAPS needs much higher bar


def score_darkpool(trades: list, ticker: str = "", for_leaps: bool = False) -> dict:
    """
    Tiered dark pool analysis. Only surfaces signal when genuinely meaningful.
    Rules:
    1. Only show if notional exceeds tier threshold — below = noise, ignored
    2. Notable  = single large block above threshold
    3. Significant = multiple blocks (3+) + above VWAP = real accumulation
    4. Score boost only on Significant — not Notable
    5. LEAPS bar = $50M+ minimum (single $8.5M print is noise for LEAPS)
    """
    empty = {"score":50,"total_notional":0,"label":None,
             "significant":False,"notable":False,"show":False}
    if not trades:
        return empty

    total = bullish = ask_side = 0
    block_count = 0
    for t in trades[:30]:
        size  = float(t.get("size",  0) or 0)
        price = float(t.get("price", 0) or 0)
        vwap  = float(t.get("vwap",  price) or price)
        n     = size * price
        total += n
        if n > 1_000_000:
            block_count += 1
        if price >= vwap:
            bullish  += n
            ask_side += n

    if total == 0:
        return empty

    score        = round(bullish / total * 100, 1)
    ask_side_pct = round(ask_side / total * 100, 1)
    is_bullish   = score > 55
    is_bearish   = score < 45

    thresh       = DP_THRESHOLDS.get(ticker, DP_THRESHOLDS["__DEFAULT__"])
    notable_min  = thresh[0]
    sig_min      = thresh[1]
    if for_leaps:
        notable_min = max(notable_min, DP_LEAPS_MIN)
        sig_min     = max(sig_min, DP_LEAPS_MIN * 2)

    significant  = (total >= sig_min and block_count >= 3 and is_bullish)
    notable      = (total >= notable_min and not significant)
    show         = significant or notable

    if not show:
        return {**empty, "score":score, "total_notional":round(total,0)}

    notional_str = f"${total/1e6:.0f}M" if total >= 1_000_000 else f"${total/1e3:.0f}K"

    if significant and is_bullish:
        label = (f"🟢 *Dark Pool: Significant accumulation* — "
                 f"{notional_str} | {block_count} blocks | {ask_side_pct:.0f}% ask-side")
    elif significant and is_bearish:
        label = f"🔴 *Dark Pool: Significant distribution* — {notional_str} | {block_count} blocks"
    elif notable and is_bullish:
        label = f"🟡 *Dark Pool: Notable buying* — {notional_str}"
    elif notable and is_bearish:
        label = f"🟡 *Dark Pool: Notable selling* — {notional_str}"
    else:
        label = f"⚪ Dark Pool: Mixed — {notional_str}"

    return {
        "score":          score,
        "total_notional": round(total, 0),
        "block_count":    block_count,
        "ask_side_pct":   ask_side_pct,
        "significant":    significant,
        "notable":        notable,
        "show":           show,
        "is_bullish":     is_bullish,
        "label":          label,
    }



def get_max_alloc(ticker): return POSITION_TIERS.get(ticker, POSITION_TIERS["__DEFAULT__"])


# Sector mapping for exposure cap
SECTOR_MAP = {
    "NVDA":"Technology","MSFT":"Technology","AAPL":"Technology",
    "ASML":"Technology","TSM":"Technology","CRDO":"Technology","CLS":"Technology",
    "AMZN":"Consumer","MELI":"Consumer","LULU":"Consumer","NFLX":"Consumer","UBER":"Consumer",
    "GOOGL":"Communication","META":"Communication",
    "IBKR":"Financials","BRK-B":"Financials","IBIT":"Financials",
    "NVO":"Healthcare","VRTX":"Healthcare",
    "MU":"Semiconductors","AMD":"Semiconductors",
    "CPRT":"Industrials","KNX":"Industrials","POWL":"Industrials","FIX":"Industrials","VRT":"Industrials",
    "NOW":"SaaS","DDOG":"SaaS",
    "TSLA":"Automotive","BABA":"China","PLTR":"Defense/AI",
}


def get_sector_exposure(ibkr_positions: dict) -> dict:
    """Calculate current sector exposure as % of portfolio."""
    sector_vals = {}
    for ticker, pos in ibkr_positions.items():
        if pos.get("asset_class") != "STK": continue
        sector = SECTOR_MAP.get(ticker, "Other")
        sector_vals[sector] = sector_vals.get(sector, 0) + abs(pos.get("market_value", 0))
    return {s: round(v / PORTFOLIO_SIZE * 100, 1) for s, v in sector_vals.items()}


def position_check(ticker, ibkr):
    pos    = ibkr.get(ticker,{})
    val    = pos.get("market_value",0)
    qty    = pos.get("quantity",0)
    avg    = pos.get("avg_cost",0)
    pct    = (val / PORTFOLIO_SIZE) * 100
    max_a  = get_max_alloc(ticker)
    room   = max(0, round(PORTFOLIO_SIZE * max_a - val, 0))
    sector = SECTOR_MAP.get(ticker, "Other")

    # Determine tier label
    if ticker in CORE_STOCKS:        tier = "Core"
    elif ticker in GROWTH_STOCKS:    tier = "Growth"
    elif ticker in CYCLICAL_STOCKS:  tier = "Cyclical"
    else:                            tier = "Opportunistic"

    return {
        "current_value": round(val,0), "quantity":qty, "avg_cost":avg,
        "current_pct":   round(pct,2), "max_pct":round(max_a*100,1),
        "room_usd":      room, "tier":tier, "sector":sector,
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
    Full quality gate based on finalized rules document.
    Quality stock → pullback → check option yield (never reverse this)
    """
    price          = md["price"]
    w52h           = md.get("week52_high", price)
    w52l           = md.get("week52_low",  price)
    pullback       = pullback_from_high(price, w52h)
    vol            = md.get("avg_volume", 0)
    ma200          = md.get("ma200", 0)
    ma50           = md.get("ma50", 0)
    above_ma200    = md.get("above_ma200", True)
    pct_above_ma50 = md.get("pct_above_ma50", 0)
    day_change_pct = md.get("day_change_pct", 0)

    checks = {}
    warnings = []

    # ── Hard filters ─────────────────────────────────────
    # Price > $20
    checks["price_ok"] = price >= 20

    # Volume > 1M shares/day
    checks["volume_ok"] = vol >= 1_000_000

    # Gap risk: skip if moved >8% today (distorted premiums)
    checks["no_gap"] = day_change_pct <= GAP_RISK_PCT
    if not checks["no_gap"]:
        warnings.append(f"⚡ Gap risk: {day_change_pct*100:.1f}% move today — premiums distorted")

    # Price location: within 8% of 52w high = skip CSP (near highs, poor risk/reward)
    near_high = pullback < NEAR_HIGH_SKIP
    checks["not_near_high"] = not near_high
    if near_high:
        warnings.append(f"📍 Near 52w high ({pullback*100:.1f}% below) — skip CSP")
    elif pullback < NEAR_HIGH_CAUTION:
        warnings.append(f"⚠️ Caution zone ({pullback*100:.1f}% below high)")

    # MA50: skip CSP if >8% above 50-day MA (extended short-term)
    ma50_extended = pct_above_ma50 > MA50_EXTENDED
    checks["ma50_ok"] = not ma50_extended
    if ma50_extended:
        warnings.append(f"📈 Extended {pct_above_ma50*100:.1f}% above 50MA — wait for pullback")

    # ── Earnings filter ──────────────────────────────────
    days_to_earnings = None
    earnings_status = "ok"
    if earnings_date:
        days_to_earnings = (earnings_date - datetime.now()).days
        if 0 < days_to_earnings < EARNINGS_HARD_STOP:
            checks["earnings_ok"] = False
            earnings_status = "hard_stop"
            warnings.append(f"🚨 Earnings in {days_to_earnings}d — HARD STOP")
        elif 0 < days_to_earnings < EARNINGS_WARNING:
            checks["earnings_ok"] = True   # allowed but flagged
            earnings_status = "warning"
            warnings.append(f"⚠️ Earnings in {days_to_earnings}d — proceed with caution")
        else:
            checks["earnings_ok"] = True
            earnings_status = "ok"
    else:
        checks["earnings_ok"] = True

    # ── Soft checks (affect score but not hard filter) ──
    # Above 200MA (trend health)
    checks["ma200_ok"] = above_ma200 or (ma200 > 0 and price >= ma200 * 0.95)
    if not checks["ma200_ok"]:
        warnings.append("⚠️ Below 200MA — downtrend warning")

    # Pullback in preferred zone
    checks["pullback_ok"] = PULLBACK_MIN <= pullback <= PULLBACK_MAX
    if not checks["pullback_ok"] and pullback >= PULLBACK_MIN:
        pass  # already caught by near_high check above

    # Hard stop conditions
    hard_stop = (not checks["price_ok"] or
                 not checks["volume_ok"] or
                 not checks["no_gap"] or
                 not checks["not_near_high"] or
                 not checks["ma50_ok"] or
                 earnings_status == "hard_stop")

    quality_score = sum(checks.values())

    return {
        "checks":          checks,
        "quality_score":   quality_score,
        "pullback":        pullback,
        "pullback_pct":    round(pullback * 100, 1),
        "days_to_earnings":days_to_earnings,
        "earnings_status": earnings_status,
        "pct_above_ma50":  round(pct_above_ma50 * 100, 1),
        "ma50_extended":   ma50_extended,
        "near_high":       near_high,
        "warnings":        warnings,
        "hard_stop":       hard_stop,
        "passes":          not hard_stop and quality_score >= 3,
    }


# ════════════════════════════════════════════════════════════
# TIMING INTELLIGENCE (IVP + price position)
# ════════════════════════════════════════════════════════════

def timing_score(strategy, pir, ivp, is_spec=False, ivp_override=None) -> dict:
    # Allow per-stock IVP floor override (e.g. PLTR needs IVP > 40)
    effective_ivp_min = ivp_override if ivp_override else IVP_MIN_SELL
    if strategy in ("CSP","CC") and ivp < effective_ivp_min:
        return {"score": 10, "recommend": False,
                "signal": f"❌ SKIP — IVP {ivp:.0f}% below minimum ({effective_ivp_min})"}
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
    # Per-stock IVP minimum (stricter for PLTR/BABA)
    ivp_min = STRICT_IVP.get(ticker, IVP_MIN_SELL)
    timing = timing_score("CSP", pir, ivdata["ivp"], ticker in SPECULATIVE,
                          ivp_override=ivp_min)
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
        # Liquidity filter: OI > 1000, volume > 100, spread < 5%
        oi_val  = int(c.get("open_interest", 0) or 0)
        vol_val = int(c.get("volume", 0) or 0)
        spread  = (ask - bid) / ask if ask > 0 else 1
        if oi_val < MIN_OPEN_INTEREST: continue
        if vol_val < MIN_DAILY_VOLUME: continue
        if spread > MAX_BID_ASK_SPREAD: continue
        # Premium efficiency: must be ≥1.5% of strike for 30-45 DTE
        prem_pct = mid / strike
        min_prem_pct = MIN_PREMIUM_PCT_30_45 if dte <= 45 else MIN_PREMIUM_PCT_45_60
        if prem_pct < min_prem_pct: continue
        delta = estimate_delta(price, strike, dte, atm_iv, "P")
        # Use stricter delta for specific stocks (PLTR, TSLA, BABA)
        d_min, d_max = STRICT_DELTA.get(ticker, (CSP_DELTA_MIN, CSP_DELTA_MAX))
        # Allow up to 0.35 delta when IVP is elevated (>50)
        if ivdata["ivp"] >= IVP_ELEVATED:
            d_max = min(CSP_DELTA_MAX_HIGH_IV, d_max + 0.05)
        if delta is None or not (d_min <= delta <= d_max): continue
        annualized = (mid / strike) * (365 / dte) * 100
        if not (CSP_MIN_ANNUALIZED <= annualized <= MAX_ANNUALIZED): continue
        max_contracts = max(1, int((PORTFOLIO_SIZE * get_max_alloc(ticker)) / (strike * 100)))
        # Options income priority stocks score 20% higher (better liquidity/fills)
        priority_boost = 1.2 if ticker in OPTIONS_INCOME_PRIORITY else 1.0
        score = (timing["score"]/100) * (quality["quality_score"]/5) * mid * (1 + atm_iv) * (1 - abs(dte-37)/37) * priority_boost
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


def fmt_bcs(opp) -> str:
    t = opp["bcs"]["timing"]; b = opp["bcs"]; q = opp["quality"]
    return "\n".join([
        f"📊 *Bull Call Spread — {opp['ticker']} @ ${opp['price']}*",
        f"_{t['signal']}_",
        *([f"  {opp['darkpool']['label']}"]
           if opp.get('darkpool',{}).get('show') else []),
        f"  {fmt_quality(q)}",
        f"  Buy ${b['long_strike']} Call / Sell ${b['short_strike']} Call",
        f"  Expiry: {b['expiry']} | {b['dte']} DTE",
        f"  Debit: ${b['debit']} | Max Profit: ${b['max_profit']}",
        f"  Return on Risk: {b['ror']}% | Breakeven: ${b['breakeven']}",
        f"  IVP: {b['ivp']:.0f}%",
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

    print("📊 IBKR positions...")
    ibkr     = get_ibkr_positions()
    stk_hold = {k:v for k,v in ibkr.items() if v.get("asset_class")=="STK"}

    all_tickers = ALL_TICKERS
    print(f"💹 Market data ({len(all_tickers)} stocks)...")
    mkt = get_market_data(all_tickers)
    ok  = sum(1 for v in mkt.values() if v["price"]>0)
    print(f"   {ok}/{len(all_tickers)} prices ✓")

    print("🌊 Market intelligence...")
    flow       = get_flow_alerts_market()

    print("   📊 Fetching Market Tide...")
    tide       = get_market_tide()
    print(f"   {tide['label']}")

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

    print("   ⚡ Fetching SPIKE...")
    spike_data = get_spike()
    print(f"   {spike_data['label']}")

    print("   📈 Fetching OI Changes...")
    oi_signals = get_oi_change()
    print(f"   OI data for {len(oi_signals)} tickers")

    # ── GO / NO-GO DECISION ──────────────────────────────────
    gng = market_go_nogo(tide, oi_signals, vix_data, spike_data, spy_regime)
    print(f"\n{'='*50}")
    print(f"📡 MARKET QUALITY SCORE: {gng['score']}/100")
    print(f"   {gng['quality']}")
    print(f"   Tide: {gng['tide_score']:+.1f} | Bull OI: {gng['bull_oi_count']} | Bear OI: {gng['bear_oi_count']}")
    print(f"   Sell Premium: {'✅ YES' if gng['sell_premium'] else '❌ NO'}")
    print(f"   Buy LEAPS:    {'✅ YES' if gng['buy_leaps'] else '❌ NO'}")
    print(f"{'='*50}\n")

    # ── MORNING MARKET BRIEFING ─────────────────────────────
    # Structure: 1) Market situation  2) Summary verdict  3) Trades follow
    vix   = gng["vix"]
    spike = gng["spike"]

    spike_line = (
        f"*SPIKE: {spike:.1f}*\n{spike_data['label']}\n"
        f"_SPIKE is like VIX but built from actual options order flow. "
        f"More real-time than VIX. {spike_data.get('action','')}_"
    ) if spike_data.get('available') else "*SPIKE: N/A*"

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
        f"{spike_line}\n"
        f"\n"
        f"*Market Tide: {gng['tide_score']:+.1f}*\n"
        f"{tide['label']}\n"
        f"_Tide = net call minus put premium across the whole market."
        f" Positive = money flowing into calls. Negative = put hedging (fear)._\n"
        f"\n"
        f"OI: {gng['bull_oi_count']} bullish signals | {gng['bear_oi_count']} bearish signals\n"
        f"\n"
        f"*S&P 500 Regime:*\n"
        f"{spy_regime['label']}\n"
        f"_S&P below 200MA = risk regime: smaller sizes, lower delta._\n"
        f"\n"
        f"━━━ TODAY'S VERDICT ━━━\n"
        f"\n"
        f"*Overall Score: {gng['score']}/100*\n"
        f"{gng['quality']}\n"
        f"\n"
        f"Sell Premium (CSP/CC): {'✅ YES' if gng['sell_premium'] else '❌ SKIP TODAY'}\n"
        f"Buy LEAPS: {'✅ YES' if gng['buy_leaps'] else '⏳ WAIT FOR BETTER ENTRY'}\n"
        f"\n"
        f"{'_Trading opportunities follow below ↓_' if (gng['sell_premium'] or gng['buy_leaps']) else '_No new positions today. Preserving capital._'}"
    )
    send_telegram(briefing)
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

        contracts  = get_option_contracts(ticker)
        if not contracts: continue

        ivdata     = calculate_ivp(contracts)
        sizing     = position_check(ticker, ibkr)
        dp_stock   = score_darkpool(get_darkpool(ticker), ticker=ticker)
        dp_leaps   = score_darkpool(get_darkpool(ticker), ticker=ticker, for_leaps=True)
        dp         = dp_stock  # default used for CSP/CC
        dp_boost   = 1.2 if dp.get("significant") else 1.0  # only boost on significant dark pool

        # OI signal for this ticker — warns if puts are being targeted
        oi_sig     = oi_signals.get(ticker, {})
        oi_warning = oi_sig.get("net", 0) < -500  # heavy new put OI = warning

        # Expiry breakdown — find max pain and risky strikes
        exp_bdown  = get_expiry_breakdown(ticker)

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
                csp_opps.append({**base,"csp":csp,
                    "score":csp["timing"]["score"]*quality["quality_score"]*csp["annualized_return"]*dp_boost})
                print(f"  [{tier}] {ticker}: 💰 CSP ${csp['strike']} {csp['annualized_return']}% ann δ{csp['delta']} IVP{ivdata['ivp']:.0f}%")

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
        if gng["buy_leaps"]:
            leaps, leaps_timing = find_best_leaps(ticker, price, contracts, ivdata, pir)
            if leaps is None and ivdata["ivp"] > 0:
                print(f"  [{tier}] {ticker}: LEAPS rejected — IVP {ivdata['ivp']:.0f}% timing: {leaps_timing.get('signal','')[:50]}")
        else:
            leaps = None
            leaps_timing = {}
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

    # ── Claude analysis ───────────────────────────────────
    print("🧠 Claude analysis...")
    analysis = claude_analyze(top_csps,top_ccs,top_leaps,top_pmccs,top_bcss,discoveries)
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

    # 3. Individual trade alerts
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

    print("\n✅ Done!")


if __name__ == "__main__":
    run_scanner()
