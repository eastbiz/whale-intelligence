"""
Whale Intelligence — Personal Options Trading Scanner
v3 — Delta filter, CC on holdings, quality over quantity,
     market timing intelligence, 52w positioning
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

PORTFOLIO_SIZE    = 7_000_000
CSP_DTE_MIN       = 25
CSP_DTE_MAX       = 45
CC_DTE_MIN        = 25
CC_DTE_MAX        = 45
LEAPS_DTE_MIN     = 300
MAX_POSITION_PCT  = 0.05        # 5% max per position
CSP_DELTA_MAX     = 0.32        # Hard cap — no CSP above this delta
CC_DELTA_MAX      = 0.35        # Hard cap for CC
ALERT_PREMIUM_MIN = 1.00        # Min $1 premium for CSP/CC
MIN_ANNUALIZED    = 15.0        # Min 15% annualized to bother alerting
MAX_ANNUALIZED    = 120.0       # Cap — above this is likely bad data

CORE_STOCKS = ["AAPL","AMZN","ASML","BRK-B","GOOGL","IBKR","MELI","MU","NVDA","NVO","TSM"]
OPPORTUNISTIC_STOCKS = [
    "BABA","CLS","CRDO","DDOG","FIX","KNX","LULU","NFLX","NOW","POWL",
    "UBER","VRT","VRTX","CPRT","CRSP","GRAB","IBIT","NBIS","PATH","PLTR","TSLA"
]

UW_BASE    = "https://api.unusualwhales.com"
UW_HEADERS = {"Authorization": f"Bearer {UNUSUAL_WHALES_API_KEY}"}


# ─────────────────────────────────────────────
# PRICE + MARKET DATA  (Yahoo Finance)
# ─────────────────────────────────────────────

def get_market_data(tickers: list) -> dict:
    """Fetch price, 52w high/low, avg volume for all tickers."""
    data = {}
    for ticker in tickers:
        yf = ticker.replace("BRK-B","BRK-B").replace("BRK.B","BRK-B")
        try:
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{yf}",
                headers={"User-Agent":"Mozilla/5.0"}, timeout=10
            )
            j = r.json()
            meta = j["chart"]["result"][0]["meta"]
            data[ticker] = {
                "price":       round(float(meta.get("regularMarketPrice", 0)), 2),
                "week52_high": round(float(meta.get("fiftyTwoWeekHigh", 0)), 2),
                "week52_low":  round(float(meta.get("fiftyTwoWeekLow", 0)), 2),
                "avg_volume":  int(meta.get("averageDailyVolume3Month", 0)),
            }
        except Exception as e:
            data[ticker] = {"price":0,"week52_high":0,"week52_low":0,"avg_volume":0}
    return data


def position_in_range(price, w52_low, w52_high):
    """
    Returns 0.0 (at 52w low) to 1.0 (at 52w high).
    0.0-0.30 = near lows, 0.70-1.0 = near highs
    """
    if w52_high <= w52_low or price <= 0:
        return 0.5
    return round((price - w52_low) / (w52_high - w52_low), 2)


# ─────────────────────────────────────────────
# IBKR FLEX REPORT
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
        r2   = requests.get(
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
        stocks = [k for k,v in positions.items() if v["asset_class"]=="STK"]
        opts   = [k for k,v in positions.items() if v["asset_class"]=="OPT"]
        print(f"   IBKR: {len(stocks)} stock positions, {len(opts)} option positions")
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


# ─────────────────────────────────────────────
# OPTION MATH
# ─────────────────────────────────────────────

def parse_option_symbol(sym: str):
    """Parse OCC symbol → (expiry, 'C'|'P', strike) or None."""
    try:
        m = re.match(r'^([A-Z.\-]+)(\d{6})([CP])(\d{8})$', sym)
        if not m: return None
        expiry = datetime.strptime("20" + m.group(2), "%Y%m%d")
        return expiry, m.group(3), int(m.group(4)) / 1000
    except: return None


def estimate_delta(otm_pct: float, dte: int, iv: float, opt_type: str) -> Optional[float]:
    """Approximate delta via normal distribution."""
    try:
        if iv <= 0 or dte <= 0: return None
        t   = dte / 365
        sst = iv * math.sqrt(t)
        if sst == 0: return None
        moneyness = -otm_pct / 100
        d1  = moneyness / sst + 0.5 * sst
        cdf = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
        delta = (1 - cdf) if opt_type == "P" else cdf
        return round(delta, 2)
    except: return None


def score_darkpool(trades: list) -> dict:
    if not trades:
        return {"score": 50, "total_notional": 0, "label": "No data"}
    total = bullish = 0
    for t in trades[:20]:
        n = float(t.get("size",0)) * float(t.get("price",0))
        total += n
        if float(t.get("price",0)) >= float(t.get("vwap", t.get("price",0))):
            bullish += n
    score = (bullish / total * 100) if total > 0 else 50
    label = ("🟢 Institutions buying" if score > 55
             else "🔴 Institutions selling" if score < 45
             else "⚪ Mixed activity")
    return {"score": round(score,1), "total_notional": round(total,0), "label": label}


# ─────────────────────────────────────────────
# MARKET TIMING INTELLIGENCE
# ─────────────────────────────────────────────

def timing_score(strategy: str, pos_in_range: float, iv: float) -> dict:
    """
    Score timing for a strategy given price position and IV.
    pos_in_range: 0=52w low, 1=52w high
    iv: implied volatility as decimal (e.g. 0.40 = 40%)

    Returns {"score": 0-100, "signal": str, "recommend": bool}
    """
    high_iv  = iv > 0.40
    low_iv   = iv < 0.25
    near_low = pos_in_range < 0.30   # Near 52w low — potential support
    near_high= pos_in_range > 0.70   # Near 52w high — stretched

    if strategy == "CSP":
        if high_iv and near_low:
            return {"score": 95, "signal": "🔥 EXCELLENT — High IV + near lows = ideal CSP", "recommend": True}
        elif high_iv and not near_high:
            return {"score": 80, "signal": "✅ GOOD — High IV, reasonable price level", "recommend": True}
        elif high_iv and near_high:
            return {"score": 55, "signal": "⚠️ CAUTION — High IV but stock near 52w high", "recommend": True}
        elif low_iv and near_low:
            return {"score": 45, "signal": "⚠️ WEAK — Low IV (poor premium) despite low price", "recommend": False}
        elif low_iv:
            return {"score": 25, "signal": "❌ POOR — Low IV means thin premium, skip CSP", "recommend": False}
        else:
            return {"score": 65, "signal": "✅ OK — Moderate timing", "recommend": True}

    elif strategy == "CC":
        if near_high and not high_iv:
            return {"score": 90, "signal": "🔥 EXCELLENT — Stock near highs, good CC level", "recommend": True}
        elif near_high and high_iv:
            return {"score": 85, "signal": "✅ GREAT — High + elevated IV = fat CC premium", "recommend": True}
        elif near_low:
            return {"score": 20, "signal": "❌ AVOID — Don't sell CC in a downtrend/near lows", "recommend": False}
        elif high_iv and not near_low:
            return {"score": 70, "signal": "✅ GOOD — Elevated IV boosts CC premium", "recommend": True}
        else:
            return {"score": 50, "signal": "⚠️ NEUTRAL — Mediocre CC timing", "recommend": False}

    elif strategy == "LEAPS":
        if low_iv and near_low:
            return {"score": 95, "signal": "🔥 EXCEPTIONAL — Cheap options + depressed price", "recommend": True}
        elif low_iv and not near_high:
            return {"score": 80, "signal": "✅ GOOD — Low IV = cheap LEAPS entry", "recommend": True}
        elif high_iv and near_low:
            return {"score": 55, "signal": "⚠️ MIXED — Low price but IV expensive", "recommend": True}
        elif high_iv and near_high:
            return {"score": 15, "signal": "❌ POOR — Expensive options at high price", "recommend": False}
        elif near_high:
            return {"score": 30, "signal": "❌ AVOID — Stock at highs, LEAPS expensive", "recommend": False}
        else:
            return {"score": 60, "signal": "✅ OK — Neutral timing for LEAPS", "recommend": True}

    return {"score": 50, "signal": "Neutral", "recommend": True}


# ─────────────────────────────────────────────
# OPPORTUNITY FINDERS
# ─────────────────────────────────────────────

def find_best_csp(ticker, stock_price, contracts, pos_in_range, iv_market):
    timing = timing_score("CSP", pos_in_range, iv_market)
    if not timing["recommend"]:
        return None, timing
    if not contracts or stock_price <= 0:
        return None, timing

    today = datetime.now()
    best  = None; best_score = 0

    for c in contracts:
        parsed = parse_option_symbol(c.get("option_symbol",""))
        if not parsed: continue
        expiry, opt_type, strike = parsed
        if opt_type != "P": continue
        dte = (expiry - today).days
        if not (CSP_DTE_MIN <= dte <= CSP_DTE_MAX): continue
        otm_pct = (stock_price - strike) / stock_price * 100
        if not (3 <= otm_pct <= 18): continue
        bid = float(c.get("nbbo_bid",0) or 0)
        ask = float(c.get("nbbo_ask",0) or 0)
        mid = (bid + ask) / 2
        if mid < ALERT_PREMIUM_MIN: continue
        if mid > strike * 0.25: continue   # sanity check
        iv  = float(c.get("implied_volatility",0) or 0)
        delta = estimate_delta(otm_pct, dte, iv, "P")
        if delta and delta > CSP_DELTA_MAX: continue   # hard delta filter
        annualized = (mid / strike) * (365 / dte) * 100
        if not (MIN_ANNUALIZED <= annualized <= MAX_ANNUALIZED): continue
        max_contracts = max(1, int((PORTFOLIO_SIZE * MAX_POSITION_PCT) / (strike * 100)))
        score = (timing["score"] / 100) * (1 - abs(dte-35)/35) * (1 - abs(otm_pct-10)/10) * mid * (1 + iv)
        if score > best_score:
            best_score = score
            best = {
                "strike": strike, "expiry": expiry.strftime("%Y-%m-%d"),
                "dte": dte, "bid": round(bid,2), "ask": round(ask,2),
                "premium": round(mid,2), "otm_pct": round(otm_pct,1),
                "iv": round(iv*100,1), "delta": delta,
                "annualized_return": round(annualized,1),
                "max_contracts": max_contracts,
                "collateral": round(strike*100*max_contracts,0),
                "timing": timing
            }
    return best, timing


def find_best_cc(ticker, stock_price, quantity, avg_cost, contracts, pos_in_range, iv_market):
    """Find best CC for a stock we already own."""
    timing = timing_score("CC", pos_in_range, iv_market)
    if not timing["recommend"]:
        return None, timing
    if not contracts or stock_price <= 0 or quantity <= 0:
        return None, timing

    today = datetime.now()
    max_contracts = int(quantity / 100)  # 1 contract per 100 shares
    if max_contracts < 1:
        return None, timing

    best = None; best_score = 0

    for c in contracts:
        parsed = parse_option_symbol(c.get("option_symbol",""))
        if not parsed: continue
        expiry, opt_type, strike = parsed
        if opt_type != "C": continue
        dte = (expiry - today).days
        if not (CC_DTE_MIN <= dte <= CC_DTE_MAX): continue
        otm_pct = (strike - stock_price) / stock_price * 100
        if not (1 <= otm_pct <= 15): continue
        bid = float(c.get("nbbo_bid",0) or 0)
        ask = float(c.get("nbbo_ask",0) or 0)
        mid = (bid + ask) / 2
        if mid < ALERT_PREMIUM_MIN: continue
        if mid > stock_price * 0.20: continue
        iv    = float(c.get("implied_volatility",0) or 0)
        delta = estimate_delta(otm_pct, dte, iv, "C")
        if delta and delta > CC_DELTA_MAX: continue
        annualized = (mid / stock_price) * (365 / dte) * 100
        if not (MIN_ANNUALIZED <= annualized <= MAX_ANNUALIZED): continue
        # Only suggest CC if strike is above avg cost (don't lock in a loss)
        if avg_cost > 0 and strike < avg_cost: continue
        score = (timing["score"] / 100) * (1 - abs(dte-35)/35) * mid * (1 + iv)
        if score > best_score:
            best_score = score
            best = {
                "strike": strike, "expiry": expiry.strftime("%Y-%m-%d"),
                "dte": dte, "bid": round(bid,2), "ask": round(ask,2),
                "premium": round(mid,2), "otm_pct": round(otm_pct,1),
                "iv": round(iv*100,1), "delta": delta,
                "annualized_return": round(annualized,1),
                "max_contracts": max_contracts,
                "avg_cost": round(avg_cost,2),
                "timing": timing
            }
    return best, timing


def find_best_leaps(ticker, stock_price, contracts, pos_in_range, iv_market):
    timing = timing_score("LEAPS", pos_in_range, iv_market)
    if not timing["recommend"]:
        return None, timing
    if not contracts or stock_price <= 0:
        return None, timing

    today = datetime.now()
    best  = None; best_score = 0

    for c in contracts:
        parsed = parse_option_symbol(c.get("option_symbol",""))
        if not parsed: continue
        expiry, opt_type, strike = parsed
        if opt_type != "C": continue
        dte = (expiry - today).days
        if dte < LEAPS_DTE_MIN: continue
        otm_pct = (strike - stock_price) / stock_price * 100
        if not (-5 <= otm_pct <= 20): continue
        bid = float(c.get("nbbo_bid",0) or 0)
        ask = float(c.get("nbbo_ask",0) or 0)
        mid = (bid + ask) / 2
        if mid < 2.0: continue
        iv    = float(c.get("implied_volatility",0) or 0)
        delta = estimate_delta(otm_pct, dte, iv, "C")
        score = (timing["score"] / 100) * (1 / (1 + abs(otm_pct - 10))) * (dte / 365)
        if score > best_score:
            best_score = score
            best = {
                "strike": strike, "expiry": expiry.strftime("%Y-%m-%d"),
                "dte": dte, "premium": round(mid,2),
                "otm_pct": round(otm_pct,1), "delta": delta,
                "leverage": round(stock_price / mid, 1) if mid > 0 else 0,
                "timing": timing
            }
    return best, timing


# ─────────────────────────────────────────────
# POSITION CHECK
# ─────────────────────────────────────────────

def position_check(ticker, ibkr_positions):
    pos = ibkr_positions.get(ticker, {})
    val = pos.get("market_value", 0)
    qty = pos.get("quantity", 0)
    avg = pos.get("avg_cost", 0)
    pct = (val / PORTFOLIO_SIZE) * 100
    room = max(0, round(PORTFOLIO_SIZE * MAX_POSITION_PCT - val, 0))
    return {
        "current_value": round(val, 0),
        "quantity": qty,
        "avg_cost": avg,
        "current_pct": round(pct, 2),
        "room_usd": room,
        "status": ("OVERWEIGHT" if pct > MAX_POSITION_PCT * 100 * 1.2
                   else "FULL"     if pct > MAX_POSITION_PCT * 100 * 0.9
                   else "HAS ROOM" if val > 0
                   else "NEW POSITION")
    }


# ─────────────────────────────────────────────
# CLAUDE ANALYSIS
# ─────────────────────────────────────────────

def claude_analyze(csps, ccs, leaps_list):
    if not ANTHROPIC_API_KEY:
        print("Claude: no API key")
        return ""
    all_opps = csps + ccs + leaps_list
    if not all_opps:
        return ""
    print(f"Claude: analyzing {len(csps)} CSPs, {len(ccs)} CCs, {len(leaps_list)} LEAPS...")
    prompt = f"""You are an expert options income trader managing a $7M portfolio.
Strategy: Sell CSPs and CCs (25-45 DTE) for income, buy LEAPS on conviction.
Preferred CSP/CC delta: 0.20-0.30. Higher delta only in strong dip situations.

Here are today's screened opportunities (already filtered for quality):

CSP OPPORTUNITIES:
{json.dumps(csps, indent=2)}

COVERED CALL OPPORTUNITIES:
{json.dumps(ccs, indent=2)}

LEAPS OPPORTUNITIES:
{json.dumps(leaps_list, indent=2)}

Please provide:
1. Top CSP trade with specific reasoning and exact execution price
2. Top CC trade (if any) with reasoning
3. Top LEAPS trade (if any) with reasoning
4. One-line market timing assessment
5. Any red flags or concerns

Be direct and specific. No fluff. Focus on what to actually execute today."""

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY,
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 700,
                  "messages": [{"role":"user","content": prompt}]},
            timeout=30
        )
        print(f"Claude API: {r.status_code}")
        if r.status_code == 200:
            return r.json()["content"][0]["text"]
        else:
            print(f"Claude error: {r.text[:200]}")
    except Exception as e:
        print(f"Claude exception: {e}")
    return ""


# ─────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────

def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[TELEGRAM]\n{message}\n")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message,
                  "parse_mode": "Markdown"},
            timeout=10
        )
        print("✅ Telegram sent!" if r.status_code == 200 else f"Telegram error: {r.text[:100]}")
    except Exception as e:
        print(f"Telegram error: {e}")


def format_csp(ticker, price, csp, sizing):
    t = csp["timing"]
    d = f" | δ {csp['delta']}" if csp.get("delta") else ""
    lines = [
        f"💰 *CSP — {ticker} @ ${price}*",
        f"{t['signal']}",
        f"  Sell Put ${csp['strike']} | {csp['expiry']} | {csp['dte']} DTE",
        f"  Bid/Ask: ${csp['bid']}/${csp['ask']} | Mid: ${csp['premium']}",
        f"  {csp['otm_pct']}% OTM | IV: {csp['iv']}%{d}",
        f"  Annualized: {csp['annualized_return']}% | {csp['max_contracts']} contracts max",
        f"  Collateral: ${csp['collateral']:,.0f}",
        f"  Position: {sizing['status']} | Room: ${sizing['room_usd']:,.0f}",
        f"_Scanned {datetime.now().strftime('%b %d %H:%M')} ET_"
    ]
    return "\n".join(lines)


def format_cc(ticker, price, cc, sizing):
    t = cc["timing"]
    d = f" | δ {cc['delta']}" if cc.get("delta") else ""
    lines = [
        f"📈 *CC — {ticker} @ ${price}*",
        f"{t['signal']}",
        f"  You hold: {int(sizing['quantity'])} shares @ ${cc['avg_cost']} avg",
        f"  Sell Call ${cc['strike']} | {cc['expiry']} | {cc['dte']} DTE",
        f"  Bid/Ask: ${cc['bid']}/${cc['ask']} | Mid: ${cc['premium']}",
        f"  {cc['otm_pct']}% OTM | IV: {cc['iv']}%{d}",
        f"  Annualized: {cc['annualized_return']}% | {cc['max_contracts']} contracts",
        f"_Scanned {datetime.now().strftime('%b %d %H:%M')} ET_"
    ]
    return "\n".join(lines)


def format_leaps(ticker, price, leaps, sizing):
    t = leaps["timing"]
    d = f" | δ {leaps['delta']}" if leaps.get("delta") else ""
    lines = [
        f"🚀 *LEAPS — {ticker} @ ${price}*",
        f"{t['signal']}",
        f"  Buy Call ${leaps['strike']} | {leaps['expiry']} | {leaps['dte']} DTE",
        f"  Cost: ${leaps['premium']} | {leaps['otm_pct']}% OTM{d}",
        f"  Leverage: {leaps['leverage']}x",
        f"  Position: {sizing['status']} | Room: ${sizing['room_usd']:,.0f}",
        f"_Scanned {datetime.now().strftime('%b %d %H:%M')} ET_"
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────
# MAIN SCANNER
# ─────────────────────────────────────────────

def run_scanner():
    print(f"\n{'='*60}")
    print(f"🐋 WHALE INTELLIGENCE SCANNER v3")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ET")
    print(f"   Portfolio: ${PORTFOLIO_SIZE:,.0f}")
    print(f"{'='*60}\n")

    # IBKR positions
    print("📊 Fetching IBKR positions...")
    ibkr = get_ibkr_positions()
    stock_holdings = {k: v for k, v in ibkr.items() if v.get("asset_class") == "STK"}

    # Prices + market data
    all_tickers = CORE_STOCKS + OPPORTUNISTIC_STOCKS
    print(f"\n💹 Fetching market data ({len(all_tickers)} stocks)...")
    mkt = get_market_data(all_tickers)
    found = sum(1 for v in mkt.values() if v["price"] > 0)
    print(f"   Got data for {found}/{len(all_tickers)} stocks")

    csp_opps   = []
    cc_opps    = []
    leaps_opps = []

    all_scan = [(t, True) for t in CORE_STOCKS] + [(t, False) for t in OPPORTUNISTIC_STOCKS]

    print(f"\n🔍 Scanning {len(all_scan)} stocks...")
    for ticker, is_core in all_scan:
        md    = mkt.get(ticker, {})
        price = md.get("price", 0)
        if price <= 0:
            continue

        w52h  = md.get("week52_high", price)
        w52l  = md.get("week52_low",  price)
        pir   = position_in_range(price, w52l, w52h)

        contracts = get_option_contracts(ticker)
        if not contracts:
            continue

        # Use median IV from contracts as market IV proxy
        ivs = [float(c.get("implied_volatility",0) or 0)
               for c in contracts[:20] if float(c.get("implied_volatility",0) or 0) > 0]
        iv_market = round(sum(ivs)/len(ivs), 3) if ivs else 0.30

        sizing = position_check(ticker, ibkr)

        # ── CSP ──────────────────────────────
        if sizing["status"] != "OVERWEIGHT":
            csp, csp_timing = find_best_csp(ticker, price, contracts, pir, iv_market)
            if csp:
                csp_opps.append({
                    "ticker": ticker, "price": price,
                    "pir": pir, "iv_market": round(iv_market*100,1),
                    "week52_low": w52l, "week52_high": w52h,
                    "csp": csp, "sizing": sizing,
                    "score": csp_timing["score"] + csp["annualized_return"] * 0.5
                })
                print(f"  {ticker}: ✅ CSP ${csp['strike']} | {csp['annualized_return']}% ann | δ{csp['delta']} | timing {csp_timing['score']}")

        # ── CC (only if we hold the stock) ───
        holding = stock_holdings.get(ticker, {})
        qty  = holding.get("quantity", 0)
        avg  = holding.get("avg_cost", 0)
        if qty >= 100:
            cc, cc_timing = find_best_cc(ticker, price, qty, avg, contracts, pir, iv_market)
            if cc:
                cc_opps.append({
                    "ticker": ticker, "price": price,
                    "pir": pir, "iv_market": round(iv_market*100,1),
                    "cc": cc, "sizing": sizing,
                    "score": cc_timing["score"] + cc["annualized_return"] * 0.5
                })
                print(f"  {ticker}: ✅ CC  ${cc['strike']} | {cc['annualized_return']}% ann | δ{cc['delta']} | timing {cc_timing['score']}")

        # ── LEAPS ────────────────────────────
        leaps, leaps_timing = find_best_leaps(ticker, price, contracts, pir, iv_market)
        if leaps:
            leaps_opps.append({
                "ticker": ticker, "price": price,
                "pir": pir, "iv_market": round(iv_market*100,1),
                "week52_low": w52l, "week52_high": w52h,
                "leaps": leaps, "sizing": sizing,
                "score": leaps_timing["score"] + leaps["leverage"] * 2
            })
            print(f"  {ticker}: ✅ LEAPS ${leaps['strike']} | {leaps['dte']}DTE | δ{leaps['delta']} | timing {leaps_timing['score']}")

    # Sort and take top 3 each
    csp_opps.sort(   key=lambda x: x["score"], reverse=True)
    cc_opps.sort(    key=lambda x: x["score"], reverse=True)
    leaps_opps.sort( key=lambda x: x["score"], reverse=True)

    top_csps  = csp_opps[:3]
    top_ccs   = cc_opps[:3]
    top_leaps = leaps_opps[:3]

    total = len(top_csps) + len(top_ccs) + len(top_leaps)
    if total == 0:
        print("\n✅ No qualifying opportunities today. No alert sent.")
        return

    print(f"\n🏆 RESULTS: {len(top_csps)} CSPs | {len(top_ccs)} CCs | {len(top_leaps)} LEAPS")

    # Claude analysis
    print("\n🧠 Getting Claude analysis...")
    analysis = claude_analyze(top_csps, top_ccs, top_leaps)
    if analysis:
        print(f"\n{analysis}")

    # Send Telegram — grouped by strategy
    print("\n📱 Sending Telegram alerts...")

    if top_csps:
        send_telegram("📋 *TODAY'S TOP CSP OPPORTUNITIES*")
        time.sleep(1)
        for opp in top_csps:
            send_telegram(format_csp(opp["ticker"], opp["price"], opp["csp"], opp["sizing"]))
            time.sleep(2)

    if top_ccs:
        send_telegram("📋 *TODAY'S TOP COVERED CALL OPPORTUNITIES*")
        time.sleep(1)
        for opp in top_ccs:
            send_telegram(format_cc(opp["ticker"], opp["price"], opp["cc"], opp["sizing"]))
            time.sleep(2)

    if top_leaps:
        send_telegram("📋 *TODAY'S TOP LEAPS OPPORTUNITIES*")
        time.sleep(1)
        for opp in top_leaps:
            send_telegram(format_leaps(opp["ticker"], opp["price"], opp["leaps"], opp["sizing"]))
            time.sleep(2)

    if analysis:
        time.sleep(2)
        send_telegram(f"🧠 *Claude's Summary*\n\n{analysis}")

    print("\n✅ Scan complete!")


if __name__ == "__main__":
    run_scanner()
