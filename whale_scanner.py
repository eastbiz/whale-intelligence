"""
Whale Intelligence — Personal Options Trading Scanner
v2 — Fixed price extraction via Yahoo Finance, IBKR Flex integrated
"""

import os
import json
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional

UNUSUAL_WHALES_API_KEY = os.environ.get("UW_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
IBKR_FLEX_TOKEN = os.environ.get("IBKR_FLEX_TOKEN", "")
IBKR_FLEX_QUERY_ID = os.environ.get("IBKR_FLEX_QUERY_ID", "")

PORTFOLIO_SIZE = 7_000_000
CSP_DTE_MIN = 25
CSP_DTE_MAX = 45
MAX_POSITION_PCT = 0.05
ALERT_PREMIUM_MIN = 1.0

CORE_STOCKS = ["AAPL","AMZN","ASML","BRK-B","GOOGL","IBKR","MELI","MU","NVDA","NVO","TSM"]
OPPORTUNISTIC_STOCKS = ["BABA","CLS","CRDO","DDOG","FIX","KNX","LULU","NFLX","NOW","POWL",
                        "UBER","VRT","VRTX","CPRT","CRSP","GRAB","IBIT","NBIS","PATH","PLTR","TSLA"]

UW_BASE = "https://api.unusualwhales.com"
UW_HEADERS = {"Authorization": f"Bearer {UNUSUAL_WHALES_API_KEY}"}


def get_prices(tickers):
    prices = {}
    for ticker in tickers:
        yf_ticker = ticker.replace("BRK-B","BRK-B").replace("BRK.B","BRK-B")
        try:
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_ticker}",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10
            )
            data = r.json()
            price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
            prices[ticker] = round(float(price), 2)
        except Exception as e:
            prices[ticker] = 0
    return prices


def get_ibkr_positions():
    positions = {}
    if not IBKR_FLEX_TOKEN or not IBKR_FLEX_QUERY_ID:
        return positions
    try:
        r = requests.get(
            f"https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest"
            f"?t={IBKR_FLEX_TOKEN}&q={IBKR_FLEX_QUERY_ID}&v=3",
            timeout=15
        )
        root = ET.fromstring(r.text)
        ref_code = root.findtext("ReferenceCode")
        if root.findtext("Status") != "Success" or not ref_code:
            return positions
        import time; time.sleep(5)
        r2 = requests.get(
            f"https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement"
            f"?t={IBKR_FLEX_TOKEN}&q={ref_code}&v=3",
            timeout=15
        )
        root2 = ET.fromstring(r2.text)
        for pos in root2.iter("OpenPosition"):
            ticker = pos.get("symbol","")
            if not ticker: continue
            positions[ticker] = {
                "market_value": float(pos.get("positionValue",0) or 0),
                "quantity": float(pos.get("position",0) or 0),
                "avg_cost": float(pos.get("costBasisPrice",0) or 0),
                "pct_nav": float(pos.get("percentOfNAV",0) or 0),
                "asset_class": pos.get("assetClass","")
            }
        print(f"   IBKR: {len(positions)} positions loaded")
    except Exception as e:
        print(f"   IBKR error: {e}")
    return positions


def get_option_contracts(ticker):
    try:
        r = requests.get(f"{UW_BASE}/api/stock/{ticker}/option-contracts",
                        headers=UW_HEADERS, timeout=15)
        if r.status_code == 200:
            return r.json().get("data", [])
    except: pass
    return []


def get_darkpool(ticker):
    try:
        r = requests.get(f"{UW_BASE}/api/darkpool/{ticker}",
                        headers=UW_HEADERS, timeout=10)
        if r.status_code == 200:
            return r.json().get("data", [])
    except: pass
    return []


def find_best_csp(ticker, stock_price, contracts):
    if not contracts or stock_price <= 0: return None
    today = datetime.now()
    best = None; best_score = 0
    for c in contracts:
        try:
            sym = c.get("option_symbol","")
            if "P" not in sym: continue
            exp_str = sym[len(ticker):len(ticker)+6]
            expiry = datetime.strptime("20"+exp_str, "%Y%m%d")
            dte = (expiry - today).days
            if not (CSP_DTE_MIN <= dte <= CSP_DTE_MAX): continue
            strike = int(sym[-8:]) / 1000
            otm_pct = (stock_price - strike) / stock_price * 100
            if not (3 <= otm_pct <= 20): continue
            bid = float(c.get("nbbo_bid",0) or 0)
            ask = float(c.get("nbbo_ask",0) or 0)
            mid = (bid + ask) / 2
            if mid < ALERT_PREMIUM_MIN: continue
            iv = float(c.get("implied_volatility",0) or 0)
            annualized = (mid / strike) * (365 / dte) * 100
            max_contracts = int((PORTFOLIO_SIZE * MAX_POSITION_PCT) / (strike * 100))
            score = (1 - abs(dte-35)/35) * (1 - abs(otm_pct-10)/10) * mid * (1+iv)
            if score > best_score:
                best_score = score
                best = {"strike": strike, "expiry": expiry.strftime("%Y-%m-%d"),
                        "dte": dte, "bid": bid, "ask": ask, "premium": round(mid,2),
                        "otm_pct": round(otm_pct,1), "iv": round(iv*100,1),
                        "annualized_return": round(annualized,1),
                        "max_contracts": max_contracts,
                        "collateral": round(strike*100*max_contracts,0)}
        except: continue
    return best


def find_best_leaps(ticker, stock_price, contracts):
    if not contracts or stock_price <= 0: return None
    today = datetime.now()
    best = None; best_score = 0
    for c in contracts:
        try:
            sym = c.get("option_symbol","")
            if "C" not in sym: continue
            exp_str = sym[len(ticker):len(ticker)+6]
            expiry = datetime.strptime("20"+exp_str, "%Y%m%d")
            dte = (expiry - today).days
            if dte < 300: continue
            strike = int(sym[-8:]) / 1000
            otm_pct = (strike - stock_price) / stock_price * 100
            if not (-5 <= otm_pct <= 25): continue
            bid = float(c.get("nbbo_bid",0) or 0)
            ask = float(c.get("nbbo_ask",0) or 0)
            mid = (bid + ask) / 2
            if mid < 2.0: continue
            score = (1/(1+abs(otm_pct-10))) * (dte/365)
            if score > best_score:
                best_score = score
                best = {"strike": strike, "expiry": expiry.strftime("%Y-%m-%d"),
                        "dte": dte, "premium": round(mid,2),
                        "otm_pct": round(otm_pct,1),
                        "leverage": round(stock_price/mid,1) if mid > 0 else 0}
        except: continue
    return best


def score_darkpool(trades):
    if not trades: return {"score": 50, "total_notional": 0}
    total = bullish = 0
    for t in trades[:20]:
        notional = float(t.get("size",0)) * float(t.get("price",0))
        total += notional
        if float(t.get("price",0)) >= float(t.get("vwap", t.get("price",0))):
            bullish += notional
    score = (bullish/total*100) if total > 0 else 50
    return {"score": round(score,1), "total_notional": round(total,0)}


def position_check(ticker, ibkr_positions):
    pos = ibkr_positions.get(ticker, {})
    val = pos.get("market_value", 0)
    pct = (val / PORTFOLIO_SIZE) * 100
    max_val = PORTFOLIO_SIZE * MAX_POSITION_PCT
    return {
        "current_value": val, "current_pct": round(pct,2),
        "room_usd": max(0, round(max_val - val, 0)),
        "status": "OVERWEIGHT" if pct > MAX_POSITION_PCT*100*1.2
                  else "FULL" if pct > MAX_POSITION_PCT*100*0.9
                  else "HAS ROOM" if val > 0 else "NEW POSITION"
    }


def claude_analyze(opportunities):
    if not ANTHROPIC_API_KEY or not opportunities: return ""
    prompt = f"""Expert options trader analyzing opportunities for $7M portfolio.
Strategy: Sell CSPs/CCs for income (25-45 DTE), buy LEAPS on conviction.

Top opportunities found today:
{json.dumps(opportunities, indent=2)}

Rank top 3 trades with specific reasoning. Note exceptional setups.
Flag any position sizing concerns. Give overall market sentiment.
Be direct and actionable."""
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"},
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 600,
                  "messages": [{"role":"user","content":prompt}]},
            timeout=30)
        if r.status_code == 200:
            return r.json()["content"][0]["text"]
    except Exception as e:
        print(f"Claude error: {e}")
    return ""


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[TELEGRAM]\n{message}\n")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"},
            timeout=10)
        print("✅ Telegram sent!" if r.status_code == 200 else f"Telegram error: {r.text}")
    except Exception as e:
        print(f"Telegram error: {e}")


def format_alert(opp):
    t = opp["ticker"]; p = opp["price"]
    csp = opp["csp"]; leaps = opp["leaps"]
    dp = opp["darkpool"]; sz = opp["sizing"]
    lines = [f"🐋 *{t}* @ ${p}"]
    if dp["total_notional"] > 0:
        sent = "🟢 Bullish" if dp["score"]>55 else "🔴 Bearish" if dp["score"]<45 else "⚪ Neutral"
        lines.append(f"Dark Pool: {sent} | ${dp['total_notional']:,.0f}")
    lines.append("")
    if csp:
        lines += [f"💰 *CSP — Sell Put*",
                  f"  ${csp['strike']} | {csp['expiry']} | {csp['dte']} DTE",
                  f"  Premium: ${csp['premium']} | {csp['otm_pct']}% OTM | IV: {csp['iv']}%",
                  f"  Annualized: {csp['annualized_return']}% | Max {csp['max_contracts']} contracts",
                  f"  Collateral: ${csp['collateral']:,.0f}", ""]
    if leaps:
        lines += [f"🚀 *LEAPS — Buy Call*",
                  f"  ${leaps['strike']} | {leaps['expiry']} | {leaps['dte']} DTE",
                  f"  Cost: ${leaps['premium']} | {leaps['otm_pct']}% OTM | {leaps['leverage']}x leverage", ""]
    emoji = "⚠️" if sz["status"]=="OVERWEIGHT" else "✅"
    lines.append(f"{emoji} {sz['status']} | Current: ${sz['current_value']:,.0f} | Room: ${sz['room_usd']:,.0f}")
    lines.append(f"\n_Scanned {datetime.now().strftime('%b %d %H:%M')} ET_")
    return "\n".join(lines)


def run_scanner():
    print(f"\n{'='*60}")
    print(f"🐋 WHALE INTELLIGENCE SCANNER v2")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ET")
    print(f"   Portfolio: ${PORTFOLIO_SIZE:,.0f}")
    print(f"{'='*60}\n")

    print("📊 Fetching IBKR positions...")
    ibkr = get_ibkr_positions()
    if not ibkr:
        print("   IBKR not available — using estimates")

    all_tickers = CORE_STOCKS + OPPORTUNISTIC_STOCKS
    print(f"\n💹 Fetching prices ({len(all_tickers)} stocks)...")
    prices = get_prices(all_tickers)
    found = sum(1 for v in prices.values() if v > 0)
    print(f"   Got prices for {found}/{len(all_tickers)} stocks")

    opportunities = []

    print(f"\n🎯 Scanning CORE stocks...")
    for ticker in CORE_STOCKS:
        price = prices.get(ticker, 0)
        print(f"  {ticker} @ ${price}...")
        if price <= 0:
            print(f"    No price, skipping")
            continue
        contracts = get_option_contracts(ticker)
        if not contracts:
            print(f"    No contracts")
            continue
        csp = find_best_csp(ticker, price, contracts)
        leaps = find_best_leaps(ticker, price, contracts)
        dp = score_darkpool(get_darkpool(ticker))
        sz = position_check(ticker, ibkr)
        if sz["status"] == "OVERWEIGHT":
            print(f"    OVERWEIGHT — skipping")
            continue
        if csp or leaps:
            score = (50 if csp else 0) + (30 if leaps else 0) + dp["score"]
            print(f"    ✅ CSP={csp is not None} LEAPS={leaps is not None} score={score:.0f}")
            opportunities.append({"ticker":ticker,"price":price,"is_core":True,
                                  "csp":csp,"leaps":leaps,"darkpool":dp,"sizing":sz,"score":score})
        else:
            print(f"    No qualifying opportunities")

    print(f"\n🔍 Scanning OPPORTUNISTIC stocks...")
    for ticker in OPPORTUNISTIC_STOCKS:
        price = prices.get(ticker, 0)
        if price <= 0: continue
        contracts = get_option_contracts(ticker)
        if not contracts: continue
        csp = find_best_csp(ticker, price, contracts)
        leaps = find_best_leaps(ticker, price, contracts)
        if not csp and not leaps: continue
        dp = score_darkpool(get_darkpool(ticker))
        sz = position_check(ticker, ibkr)
        if sz["status"] == "OVERWEIGHT": continue
        score = (50 if csp else 0) + (30 if leaps else 0) + dp["score"]
        print(f"  {ticker} @ ${price} ✅ score={score:.0f}")
        opportunities.append({"ticker":ticker,"price":price,"is_core":False,
                              "csp":csp,"leaps":leaps,"darkpool":dp,"sizing":sz,"score":score})

    if not opportunities:
        print("\n✅ No qualifying opportunities today.")
        return

    opportunities.sort(key=lambda x: x["score"], reverse=True)
    top = opportunities[:5]

    print(f"\n🏆 TOP {len(top)} OPPORTUNITIES:")
    for o in top:
        ann = f" | CSP {o['csp']['annualized_return']}% ann." if o['csp'] else ""
        print(f"   {o['ticker']} @ ${o['price']}{ann} | score={o['score']:.0f}")

    print("\n🧠 Claude analysis...")
    analysis = claude_analyze(top)
    if analysis:
        print(f"\n{analysis}")

print("\n📱 Sending alerts...")
    import time
    for opp in top[:3]:
        send_telegram(format_alert(opp))
        time.sleep(2)
    if analysis:
        print("📱 Sending Claude summary...")
        send_telegram(f"🧠 *Claude's Summary*\n\n{analysis}")
    else:
        send_telegram("🧠 *No Claude summary available*")

    print("\n✅ Done!")


if __name__ == "__main__":
    run_scanner()
