"""
Move Watcher — lightweight intraday price-move alerts (A7, was backlog item C4).

Runs every 15 minutes during market hours via GitHub Actions
(.github/workflows/move-watcher.yml). Fills the gap between the 3x/day full
scans: a big move no longer waits hours for the next scan to be noticed.

What it does — and deliberately nothing more:
  - One Yahoo quote per watched ticker (watchlist + names with open short
    options from the last scan's results.json). NO Schwab calls, NO IBKR
    calls, NO option chains — nothing that burns tokens or rate limits.
  - If a name moved >=5% today, send ONE compact Telegram message listing
    all movers, with buy/sell-target and held-short-position context.
  - Dedup via move_watcher_state.json (committed back to the repo only when
    an alert fires): one alert per ticker/direction/day, escalating again
    only when the move crosses the next 5% bucket (5% -> 10% -> 15%...).

The full scans remain the source of truth for actual trade candidates and
P&L (BIG MOVE / P&L SWING alerts) — this is just the early-warning bell.
"""

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

MOVE_ALERT_PCT   = 5.0                        # |day move| that triggers an alert
STATE_FILE       = "move_watcher_state.json"  # dedup state, committed on change
RESULTS_FILE     = "results.json"             # last full scan (for held positions)
ET               = ZoneInfo("America/New_York")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")


def now_et() -> datetime:
    return datetime.now(ET)


def market_hours(dt: datetime) -> bool:
    """Mon-Fri, 9:25-16:05 ET (small buffer both sides; cron has jitter)."""
    if dt.weekday() >= 5:
        return False
    hm = dt.hour * 60 + dt.minute
    return (9 * 60 + 25) <= hm <= (16 * 60 + 5)


def yahoo_quote(ticker: str):
    """Return (price, day_change_pct) from Yahoo, or (None, None) on failure."""
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            headers={"User-Agent": "Mozilla/5.0"},
            params={"interval": "1d", "range": "1d"},
            timeout=10,
        )
        meta = r.json()["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice")
        prev  = meta.get("chartPreviousClose")
        if not price or not prev:
            return None, None
        return float(price), (float(price) / float(prev) - 1.0) * 100.0
    except Exception:
        return None, None


def send_telegram(msg: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("(no telegram creds — printing instead)\n" + msg)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg,
                  "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        print(f"telegram send failed: {e}")


def load_universe():
    """Watchlist tickers + per-ticker targets, plus open short options."""
    # SYMBOL_SETTINGS import executes whale_scanner's top level (constants +
    # bucket load only — main() is guarded). Fallback: parse buckets.csv.
    targets = {}
    try:
        from whale_scanner import SYMBOL_SETTINGS
        targets = {t: {"buy_under": s.get("buy_under", 0),
                       "sell_above": s.get("sell_above", 0)}
                   for t, s in SYMBOL_SETTINGS.items()}
    except Exception as e:
        print(f"whale_scanner import failed ({e}) — falling back to buckets.csv")
        try:
            import csv
            with open("buckets.csv") as f:
                for row in csv.DictReader(f):
                    targets[row["ticker"].strip().upper()] = {
                        "buy_under": 0, "sell_above": 0}
        except Exception as e2:
            print(f"buckets.csv fallback failed too: {e2}")

    # Open short options from the last full scan
    short_pos = {}
    try:
        with open(RESULTS_FILE) as f:
            res = json.load(f)
        for pa in res.get("position_actions", []):
            tk = pa.get("ticker", "")
            if tk:
                short_pos.setdefault(tk, []).append(pa)
    except Exception as e:
        print(f"no positions from {RESULTS_FILE} ({e})")

    return targets, short_pos


def build_line(tk, price, chg, targets, short_pos):
    """One alert line: move + target context + held-position context."""
    arrow = "🟢▲" if chg > 0 else "🔴▼"
    line = f"{arrow} *{tk}* {chg:+.1f}% today (${price:,.2f})"

    tgt = targets.get(tk, {})
    bu, sa = tgt.get("buy_under", 0), tgt.get("sell_above", 0)
    if chg < 0 and bu > 0:
        rel = (price - bu) / bu * 100
        line += (f"\n   Buy-under ${bu:g}: now {abs(rel):.1f}% "
                 f"{'BELOW' if rel < 0 else 'above'} target"
                 + (" — CSP territory" if rel < 5 else ""))
    if chg > 0 and sa > 0:
        rel = (price - sa) / sa * 100
        line += (f"\n   Sell-above ${sa:g}: now {abs(rel):.1f}% "
                 f"{'ABOVE' if rel > 0 else 'below'} target"
                 + (" — CC territory" if rel > -5 else ""))

    for p in short_pos.get(tk, []):
        ptype = p.get("type", "")
        favorable = (ptype == "CSP" and chg > 0) or (ptype == "CC" and chg < 0)
        tag = ("💰 favorable — review close"
               if favorable else "⚠ moving toward your strike")
        line += (f"\n   You hold short {ptype} ${p.get('strike', 0):g} "
                 f"{p.get('expiry','')} — {tag}")
    return line


def main():
    dt = now_et()
    if not market_hours(dt):
        print(f"{dt:%Y-%m-%d %H:%M ET} — market closed, exiting")
        return

    targets, short_pos = load_universe()
    watch = sorted(set(targets) | set(short_pos))
    if not watch:
        print("empty universe — nothing to watch")
        return

    today = dt.strftime("%Y-%m-%d")
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except Exception:
        state = {}
    if state.get("date") != today:
        state = {"date": today, "alerted": {}}

    lines = []
    changed = False
    for tk in watch:
        price, chg = yahoo_quote(tk)
        if price is None:
            continue
        if abs(chg) < MOVE_ALERT_PCT:
            continue
        bucket = int(abs(chg) // 5) * 5          # 5, 10, 15, ...
        key = f"{tk}:{'up' if chg > 0 else 'down'}"
        if state["alerted"].get(key, 0) >= bucket:
            continue                              # already alerted this bucket
        state["alerted"][key] = bucket
        changed = True
        lines.append(build_line(tk, price, chg, targets, short_pos))

    if lines:
        msg = (f"⚡ *INTRADAY MOVES — {dt:%H:%M} ET*\n"
               f"_Move watcher (15-min). Full detail at next scan._\n\n"
               + "\n\n".join(lines))
        send_telegram(msg)
        print(f"alerted {len(lines)} mover(s)")
    else:
        print("no new movers")

    if changed:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
        print("state updated")


if __name__ == "__main__":
    main()
