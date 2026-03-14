"""
Whale Intelligence — Personal Options Trading Scanner
Scans watchlist for CSP, CC, and LEAPS opportunities
Sends Telegram alerts for exceptional setups
"""

import os
import json
import requests
from datetime import datetime, timedelta
from typing import Optional

# ============================================================
# CONFIGURATION — Edit these
# ============================================================

UNUSUAL_WHALES_API_KEY = os.environ.get("UW_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

PORTFOLIO_SIZE = 7_000_000

# Core stocks — always scan these
CORE_STOCKS = [
    "AAPL", "AMZN", "ASML", "BRK.B", "GOOGL",
    "IBKR", "MELI", "MU", "NVDA", "NVO", "TSM"
]

# Opportunistic — scan only when flow is exceptional
OPPORTUNISTIC_STOCKS = [
    "BABA", "CLS", "CRDO", "DDOG", "FIX", "GRB", "KNX",
    "LULU", "LNFLX", "NOW", "POWL", "UBER", "VNA", "VONOVIA",
    "VRT", "VRTX", "ANGI", "CPRT", "CRSP", "GRAB", "IBIT",
    "NBIS", "PATH", "PLTR", "TSLA"
]

# Options strategy preferences
CSP_DTE_MIN = 25
CSP_DTE_MAX = 45
CSP_DELTA_TARGET = 0.25   # ~25 delta for CSPs
CC_DELTA_TARGET = 0.30    # ~30 delta for CCs
MAX_POSITION_PCT = 0.05   # Max 5% of portfolio per position ($350K)
ALERT_PREMIUM_MIN = 1.0   # Minimum premium worth alerting ($1.00)
IV_RANK_ALERT = 50        # Alert when IV rank above this

UW_BASE = "https://api.unusualwhales.com"
HEADERS = {"Authorization": f"Bearer {UNUSUAL_WHALES_API_KEY}"}


# ============================================================
# UNUSUAL WHALES API
# ============================================================

def get_flow_alerts(ticker: str) -> list:
    """Fetch options flow alerts for a ticker."""
    try:
        r = requests.get(
            f"{UW_BASE}/api/option-trades/flow-alerts",
            headers=HEADERS,
            params={"ticker": ticker},
            timeout=10
        )
        if r.status_code == 200:
            return r.json().get("data", [])
    except Exception as e:
        print(f"  Flow fetch error for {ticker}: {e}")
    return []


def get_stock_info(ticker: str) -> dict:
    """Fetch stock info including IV data."""
    try:
        r = requests.get(
            f"{UW_BASE}/api/stock/{ticker}/info",
            headers=HEADERS,
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            return data.get("data", data)
    except Exception as e:
        print(f"  Stock info error for {ticker}: {e}")
    return {}


def get_option_contracts(ticker: str) -> list:
    """Fetch available option contracts."""
    try:
        r = requests.get(
            f"{UW_BASE}/api/stock/{ticker}/option-contracts",
            headers=HEADERS,
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            return data.get("data", [])
    except Exception as e:
        print(f"  Contracts error for {ticker}: {e}")
    return []


def get_darkpool(ticker: str) -> list:
    """Fetch dark pool trades for a ticker."""
    try:
        r = requests.get(
            f"{UW_BASE}/api/darkpool/{ticker}",
            headers=HEADERS,
            timeout=10
        )
        if r.status_code == 200:
            return r.json().get("data", [])
    except Exception as e:
        print(f"  Darkpool error for {ticker}: {e}")
    return []


# ============================================================
# ANALYSIS HELPERS
# ============================================================

def score_flow(flow_alerts: list, ticker: str) -> dict:
    """Score options flow for a ticker. Returns sentiment score and key trades."""
    if not flow_alerts:
        return {"score": 0, "bullish": 0, "bearish": 0, "top_trades": [], "total_premium": 0}

    bullish_premium = 0
    bearish_premium = 0
    top_trades = []

    for alert in flow_alerts:
        if alert.get("ticker") != ticker:
            continue
        premium = float(alert.get("total_premium", 0))
        trade_type = alert.get("type", "")
        if trade_type == "call":
            bullish_premium += premium
        elif trade_type == "put":
            bearish_premium += premium

        if premium > 100_000:
            top_trades.append({
                "type": trade_type,
                "strike": alert.get("strike"),
                "expiry": alert.get("expiry"),
                "premium": premium,
                "rule": alert.get("alert_rule", ""),
                "iv": float(alert.get("iv_end", 0))
            })

    total = bullish_premium + bearish_premium
    score = 0
    if total > 0:
        score = (bullish_premium - bearish_premium) / total * 100

    return {
        "score": round(score, 1),
        "bullish": bullish_premium,
        "bearish": bearish_premium,
        "top_trades": sorted(top_trades, key=lambda x: x["premium"], reverse=True)[:3],
        "total_premium": total
    }


def find_csp_opportunity(ticker: str, stock_price: float, contracts: list) -> Optional[dict]:
    """Find best CSP opportunity in 30-40 DTE range."""
    if not contracts or stock_price <= 0:
        return None

    today = datetime.now()
    best = None
    best_score = 0

    for contract in contracts:
        try:
            if contract.get("option_type", "").lower() != "put":
                continue

            expiry_str = contract.get("expiry", "")
            if not expiry_str:
                continue
            expiry = datetime.strptime(expiry_str[:10], "%Y-%m-%d")
            dte = (expiry - today).days

            if not (CSP_DTE_MIN <= dte <= CSP_DTE_MAX):
                continue

            strike = float(contract.get("strike", 0))
            if strike <= 0:
                continue

            # Prefer strikes at 5-15% OTM (sweet spot for CSP income)
            otm_pct = (stock_price - strike) / stock_price * 100
            if not (3 <= otm_pct <= 20):
                continue

            mid_price = (float(contract.get("bid", 0)) + float(contract.get("ask", 0))) / 2
            if mid_price < ALERT_PREMIUM_MIN:
                continue

            # Score: prefer 30-40 DTE, 5-15% OTM, higher premium
            dte_score = 1 - abs(dte - 35) / 35
            otm_score = 1 - abs(otm_pct - 10) / 10
            score = dte_score * otm_score * mid_price

            if score > best_score:
                best_score = score
                annualized_return = (mid_price / strike) * (365 / dte) * 100
                max_contracts = int((PORTFOLIO_SIZE * MAX_POSITION_PCT) / (strike * 100))
                best = {
                    "strike": strike,
                    "expiry": expiry_str[:10],
                    "dte": dte,
                    "premium": round(mid_price, 2),
                    "otm_pct": round(otm_pct, 1),
                    "annualized_return": round(annualized_return, 1),
                    "max_contracts": max_contracts,
                    "max_premium": round(mid_price * max_contracts * 100, 0)
                }
        except Exception:
            continue

    return best


def find_leaps_opportunity(ticker: str, stock_price: float, contracts: list) -> Optional[dict]:
    """Find best LEAPS call opportunity (1+ year out)."""
    if not contracts or stock_price <= 0:
        return None

    today = datetime.now()
    best = None
    best_score = 0

    for contract in contracts:
        try:
            if contract.get("option_type", "").lower() != "call":
                continue

            expiry_str = contract.get("expiry", "")
            if not expiry_str:
                continue
            expiry = datetime.strptime(expiry_str[:10], "%Y-%m-%d")
            dte = (expiry - today).days

            if dte < 300:  # LEAPS = 300+ days
                continue

            strike = float(contract.get("strike", 0))
            if strike <= 0:
                continue

            # LEAPS: prefer slightly OTM to ATM (0-20% OTM)
            otm_pct = (strike - stock_price) / stock_price * 100
            if not (-5 <= otm_pct <= 25):
                continue

            mid_price = (float(contract.get("bid", 0)) + float(contract.get("ask", 0))) / 2
            if mid_price < 2.0:
                continue

            iv = float(contract.get("iv", 0))
            score = (1 / (1 + abs(otm_pct - 10))) * (dte / 365)

            if score > best_score:
                best_score = score
                best = {
                    "strike": strike,
                    "expiry": expiry_str[:10],
                    "dte": dte,
                    "premium": round(mid_price, 2),
                    "otm_pct": round(otm_pct, 1),
                    "iv": round(iv * 100, 1) if iv else None,
                    "leverage": round(stock_price / mid_price, 1)
                }
        except Exception:
            continue

    return best


def position_size_check(ticker: str, stock_price: float, ibkr_positions: dict) -> dict:
    """Check if adding position would be over/under weight."""
    current_value = ibkr_positions.get(ticker, {}).get("market_value", 0)
    current_pct = (current_value / PORTFOLIO_SIZE) * 100
    max_value = PORTFOLIO_SIZE * MAX_POSITION_PCT

    return {
        "current_value": current_value,
        "current_pct": round(current_pct, 2),
        "max_pct": MAX_POSITION_PCT * 100,
        "room_usd": max(0, max_value - current_value),
        "status": "OVERWEIGHT" if current_pct > MAX_POSITION_PCT * 100 * 1.2
                  else "FULL" if current_pct > MAX_POSITION_PCT * 100 * 0.9
                  else "ROOM" if current_pct > 0
                  else "NEW"
    }


# ============================================================
# CLAUDE ANALYSIS
# ============================================================

def claude_analyze(opportunities: list, market_context: str) -> str:
    """Send opportunities to Claude for final analysis and ranking."""
    if not ANTHROPIC_API_KEY or not opportunities:
        return ""

    prompt = f"""You are an expert options trader analyzing these opportunities for a $7M portfolio.
The trader sells Cash-Secured Puts (CSP) and Covered Calls (CC) for income (30-40 DTE preferred),
and buys LEAPS on high-conviction stocks.

Market context from options flow: {market_context}

Opportunities found today:
{json.dumps(opportunities, indent=2)}

Please:
1. Rank the top 3 opportunities overall
2. Flag any exceptional setups (high IV rank + bullish flow + good premium)
3. Note any position sizing concerns
4. Give a one-line market sentiment summary

Be concise and direct. Focus on actionable insights."""

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 800,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30
        )
        if r.status_code == 200:
            return r.json()["content"][0]["text"]
    except Exception as e:
        print(f"Claude analysis error: {e}")
    return ""


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message: str):
    """Send message to Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured — printing alert instead:")
        print(message)
        return

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "Markdown"
            },
            timeout=10
        )
        if r.status_code == 200:
            print("✅ Telegram alert sent!")
        else:
            print(f"Telegram error: {r.text}")
    except Exception as e:
        print(f"Telegram send error: {e}")


def format_alert(ticker: str, stock_price: float, flow: dict,
                 csp: dict, leaps: dict, sizing: dict) -> str:
    """Format a Telegram alert message."""
    sentiment = "🟢 Bullish" if flow["score"] > 20 else "🔴 Bearish" if flow["score"] < -20 else "⚪ Neutral"
    lines = [
        f"🐋 *WHALE ALERT — {ticker}* @ ${stock_price:.2f}",
        f"Flow Sentiment: {sentiment} (score: {flow['score']})",
        f"Total Premium Activity: ${flow['total_premium']:,.0f}",
        ""
    ]

    if csp:
        lines += [
            f"💰 *CSP Opportunity*",
            f"  Strike: ${csp['strike']} | Expiry: {csp['expiry']} ({csp['dte']} DTE)",
            f"  Premium: ${csp['premium']} | {csp['otm_pct']}% OTM",
            f"  Annualized: {csp['annualized_return']}% | Max {csp['max_contracts']} contracts",
            ""
        ]

    if leaps:
        lines += [
            f"🚀 *LEAPS Opportunity*",
            f"  Strike: ${leaps['strike']} | Expiry: {leaps['expiry']} ({leaps['dte']} DTE)",
            f"  Cost: ${leaps['premium']} | {leaps['otm_pct']}% OTM | {leaps['leverage']}x leverage",
            ""
        ]

    if sizing["status"] == "OVERWEIGHT":
        lines.append(f"⚠️ Position sizing: *OVERWEIGHT* ({sizing['current_pct']}% of portfolio)")
    elif sizing["status"] == "FULL":
        lines.append(f"📊 Position: Nearly full ({sizing['current_pct']}% of portfolio)")
    elif sizing["room_usd"] > 0:
        lines.append(f"✅ Room to add: ${sizing['room_usd']:,.0f} ({sizing['current_pct']}% currently)")
    else:
        lines.append(f"🆕 New position — up to ${PORTFOLIO_SIZE * MAX_POSITION_PCT:,.0f} (5% max)")

    lines.append(f"\n_Scanned {datetime.now().strftime('%Y-%m-%d %H:%M')} ET_")
    return "\n".join(lines)


# ============================================================
# IBKR (Client Portal API — optional)
# ============================================================

def get_ibkr_positions(gateway_url: str = "https://localhost:5000") -> dict:
    """
    Fetch positions from IBKR Client Portal API.
    Requires IBKR Client Portal Gateway running locally.
    Returns dict of {ticker: {market_value, quantity, avg_cost}}
    """
    positions = {}
    try:
        # Get account ID first
        r = requests.get(f"{gateway_url}/v1/api/portfolio/accounts",
                        verify=False, timeout=5)
        if r.status_code != 200:
            return positions

        accounts = r.json()
        account_id = accounts[0].get("accountId", "")

        # Get positions
        r2 = requests.get(
            f"{gateway_url}/v1/api/portfolio/{account_id}/positions/0",
            verify=False, timeout=5
        )
        if r2.status_code == 200:
            for pos in r2.json():
                ticker = pos.get("ticker", pos.get("contractDesc", "")).split()[0]
                positions[ticker] = {
                    "market_value": float(pos.get("mktValue", 0)),
                    "quantity": float(pos.get("position", 0)),
                    "avg_cost": float(pos.get("avgCost", 0))
                }
    except Exception:
        # IBKR gateway not running — return empty (script continues without it)
        pass
    return positions


# ============================================================
# MAIN SCANNER
# ============================================================

def scan_ticker(ticker: str, ibkr_positions: dict, is_core: bool = True) -> Optional[dict]:
    """Full scan of a single ticker. Returns opportunity dict if notable."""
    print(f"  Scanning {ticker}...")

    # Get flow data
    flow_alerts = get_flow_alerts(ticker)
    flow = score_flow(flow_alerts, ticker)

    # Skip opportunistic stocks with no meaningful flow
    if not is_core and flow["total_premium"] < 200_000:
        return None

    # Get stock info
    info = get_stock_info(ticker)
    stock_price = float(info.get("close", info.get("last_price", 0)))
    if stock_price <= 0:
        # Try to extract from flow data
        for alert in flow_alerts:
            if alert.get("ticker") == ticker:
                p = float(alert.get("underlying_price", 0))
                if p > 0:
                    stock_price = p
                    break

    if stock_price <= 0:
        print(f"    No price data for {ticker}")
        return None

    # Get option contracts
    contracts = get_option_contracts(ticker)

    # Find opportunities
    csp = find_csp_opportunity(ticker, stock_price, contracts)
    leaps = find_leaps_opportunity(ticker, stock_price, contracts)

    # Position sizing
    sizing = position_size_check(ticker, stock_price, ibkr_positions)

    # Skip if no options opportunities found and flow is neutral
    if not csp and not leaps and abs(flow["score"]) < 30:
        return None

    # Skip overweight positions for new additions
    if sizing["status"] == "OVERWEIGHT":
        print(f"    {ticker} is overweight — skipping")
        return None

    result = {
        "ticker": ticker,
        "price": stock_price,
        "is_core": is_core,
        "flow": flow,
        "csp": csp,
        "leaps": leaps,
        "sizing": sizing,
        "score": abs(flow["score"]) + (50 if csp else 0) + (30 if leaps else 0)
    }

    return result


def run_scanner():
    """Main entry point — runs full scan and sends alerts."""
    print(f"\n{'='*60}")
    print(f"🐋 WHALE INTELLIGENCE SCANNER")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ET")
    print(f"   Portfolio: ${PORTFOLIO_SIZE:,.0f}")
    print(f"{'='*60}\n")

    # Try to get IBKR positions (optional — continues if not available)
    print("📊 Fetching IBKR positions...")
    ibkr_positions = get_ibkr_positions()
    if ibkr_positions:
        print(f"   Found {len(ibkr_positions)} positions in IBKR")
    else:
        print("   IBKR gateway not available — position sizing uses estimates")

    opportunities = []

    # Scan core stocks
    print(f"\n🎯 Scanning {len(CORE_STOCKS)} CORE stocks...")
    for ticker in CORE_STOCKS:
        result = scan_ticker(ticker, ibkr_positions, is_core=True)
        if result:
            opportunities.append(result)

    # Scan opportunistic stocks (only if flow is notable)
    print(f"\n🔍 Scanning {len(OPPORTUNISTIC_STOCKS)} OPPORTUNISTIC stocks...")
    for ticker in OPPORTUNISTIC_STOCKS:
        result = scan_ticker(ticker, ibkr_positions, is_core=False)
        if result:
            opportunities.append(result)

    if not opportunities:
        print("\n✅ No exceptional opportunities today. No alert sent.")
        return

    # Sort by score
    opportunities.sort(key=lambda x: x["score"], reverse=True)
    top = opportunities[:5]

    print(f"\n🏆 Found {len(opportunities)} opportunities, top {len(top)} being analyzed...")

    # Claude analysis
    market_flow = get_flow_alerts("")  # market-wide
    market_context = f"Market-wide flow fetched. Top opportunities: {[o['ticker'] for o in top]}"
    analysis = claude_analyze(top, market_context)

    # Send Telegram alerts for top opportunities
    for opp in top[:3]:  # Max 3 alerts per run
        if opp["flow"]["total_premium"] > 100_000 or opp["csp"] or opp["leaps"]:
            msg = format_alert(
                opp["ticker"], opp["price"],
                opp["flow"], opp["csp"], opp["leaps"], opp["sizing"]
            )
            send_telegram(msg)

    # Send Claude's summary analysis
    if analysis:
        summary = f"🧠 *Claude's Daily Summary*\n\n{analysis}"
        send_telegram(summary)

    print("\n✅ Scan complete!")
    return opportunities


if __name__ == "__main__":
    run_scanner()
