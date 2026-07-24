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
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

MOVE_ALERT_PCT   = 5.0                        # |day move| that triggers an alert
STATE_FILE       = "move_watcher_state.json"  # dedup state, committed on change
RESULTS_FILE     = "results.json"             # last full scan (for held positions)
ET               = ZoneInfo("America/New_York")
PT               = ZoneInfo("America/Los_Angeles")

# ── Watchdog: rescue late/dropped scheduled scans ────────────────────────
# GitHub's cron ran 60-105 min late on EVERY scheduled scan in Jul 2026 and
# occasionally dropped runs entirely. Since this watcher wakes every 15 min
# anyway, it checks whether each expected scan actually happened and fires a
# workflow_dispatch on scanner.yml if not. The scanner itself skips
# late-arriving schedule duplicates (see skip_redundant_scheduled_run there).
SCAN_TIMES_UTC     = ["13:47", "16:41", "18:47"]  # KEEP IN SYNC with scanner.yml crons
WATCHDOG_GRACE_MIN = 10                            # give the real cron this head start
SCANNER_WORKFLOW   = "scanner.yml"
GITHUB_TOKEN       = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPOSITORY  = os.environ.get("GITHUB_REPOSITORY", "eastbiz/whale-intelligence")

# ── Move-triggered full scan (A12) ───────────────────────────────────────
# A ≥5% move only pings the price (above). A BIG move (≥8%) means John wants a
# FRESH trade candidate now — LEAPS BUY_DIP, refreshed P&L — not at the next
# 3x/day slot. So the watcher dispatches a FULL scan on a big mover. Guards to
# protect the ~10/day IBKR Flex budget: (1) one trigger per ticker/direction
# per day, (2) a hard daily cap, (3) skip if a scan already ran very recently
# (data still fresh). The 3 scheduled scans + up to 3 watchdog rescues + this
# cap stay within budget; the scanner's own IBKR cache absorbs the rest.
MOVE_SCAN_PCT         = 8.0   # |day move| that warrants a fresh full scan
MOVE_SCAN_MAX_PER_DAY = 3     # hard cap on move-triggered scans (IBKR budget)
MOVE_SCAN_FRESH_MIN   = 25    # skip if a full scan completed within this many min

# ── Proximity gate for the Telegram ping (A13) ───────────────────────────
# A >=5% move alone is NOT enough to ping — John doesn't care that MU dropped
# 6.7% when it's still 105% above his buy target. A name earns a ping only when
# the move brings the price CLOSE to something actionable: within
# NEAR_TARGET_PCT of his buy-under (on a drop) or sell-above (on a rise), or
# within NEAR_STRIKE_PCT of a strike he actually holds. Everything else is
# suppressed. (The >=8% move still triggers a FULL scan regardless — that path
# surfaces LEAPS BUY_DIP etc. independent of these targets.)
MOVE_NEAR_TARGET_PCT  = 10.0  # price within 10% of buy_under / sell_above
MOVE_NEAR_STRIKE_PCT  = 12.0  # price within 12% of a held short strike

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


def near_actionable(tk, price, chg, targets, short_pos):
    """A13 proximity gate: does this move bring the price close enough to
    something John cares about to be worth a ping? Returns (bool, reasons)."""
    reasons = []
    tgt = targets.get(tk, {})
    bu, sa = tgt.get("buy_under", 0), tgt.get("sell_above", 0)
    # Drop toward the buy-under target (a CSP could get actionable)
    if chg < 0 and bu > 0 and price <= bu * (1 + MOVE_NEAR_TARGET_PCT / 100):
        reasons.append("near buy-under")
    # Rise toward the sell-above target (a CC could get actionable)
    if chg > 0 and sa > 0 and price >= sa * (1 - MOVE_NEAR_TARGET_PCT / 100):
        reasons.append("near sell-above")
    # Near a strike John actually holds (getting tested, either direction)
    for p in short_pos.get(tk, []):
        strike = p.get("strike", 0) or 0
        if strike > 0 and abs(price - strike) / strike <= MOVE_NEAR_STRIKE_PCT / 100:
            reasons.append(f"near {p.get('type','')} ${strike:g}")
    return (bool(reasons), reasons)


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


def last_scan_utc():
    """When the last full scan completed, as UTC. None if unknown.
    NOTE: results.json scan_time strings end in 'ET' but the scanner's
    now_et() actually produces Pacific time — parse with PT."""
    try:
        with open(RESULTS_FILE) as f:
            st = json.load(f).get("scan_time", "")
        return (datetime.strptime(st, "%Y-%m-%d %H:%M ET")
                .replace(tzinfo=PT).astimezone(timezone.utc))
    except Exception:
        return None


def dispatch_scanner(reason: str) -> bool:
    """Fire scanner.yml via workflow_dispatch. Returns True on success."""
    if not GITHUB_TOKEN:
        print("watchdog: no GITHUB_TOKEN — cannot dispatch")
        return False
    try:
        r = requests.post(
            f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/"
            f"workflows/{SCANNER_WORKFLOW}/dispatches",
            headers={"Authorization": f"Bearer {GITHUB_TOKEN}",
                     "Accept": "application/vnd.github+json"},
            json={"ref": "main", "inputs": {"reason": reason}},
            timeout=15,
        )
        ok = r.status_code == 204
        print(f"watchdog dispatch: HTTP {r.status_code}"
              + ("" if ok else f" — {r.text[:200]}"))
        return ok
    except Exception as e:
        print(f"watchdog dispatch failed: {e}")
        return False


def run_watchdog(state: dict) -> bool:
    """Check each expected scan slot; dispatch a replacement if missed.
    Returns True if state changed (a dispatch was recorded)."""
    now_utc = datetime.now(timezone.utc)
    last = last_scan_utc()
    changed = False
    dispatched = state.setdefault("dispatched", {})
    for hhmm in SCAN_TIMES_UTC:
        h, m = map(int, hhmm.split(":"))
        slot = now_utc.replace(hour=h, minute=m, second=0, microsecond=0)
        if now_utc < slot + timedelta(minutes=WATCHDOG_GRACE_MIN):
            continue                       # slot not due yet (or within grace)
        if dispatched.get(hhmm):
            continue                       # already rescued this slot today
        if last is not None and last >= slot - timedelta(minutes=5):
            continue                       # a scan at/after this slot exists
        last_txt = f"last scan {last:%H:%M} UTC" if last else "no scan on record"
        print(f"watchdog: scan slot {hhmm} UTC missed ({last_txt}) — dispatching scanner")
        if dispatch_scanner(f"watchdog: {hhmm} UTC slot missed"):
            dispatched[hhmm] = True
            changed = True
    return changed


def run_move_trigger(state: dict, big_movers: list) -> bool:
    """Dispatch ONE full scan when a big mover (>=MOVE_SCAN_PCT) appears, so a
    fresh trade candidate (LEAPS BUY_DIP, P&L) lands within ~15 min instead of
    waiting for the next 3x/day slot. Budget-guarded. Returns True if state
    changed. big_movers: list of (ticker, chg, key) already past MOVE_SCAN_PCT."""
    if not big_movers:
        return False
    triggered = state.setdefault("scan_triggered", {})     # key -> True (per day)
    count = state.setdefault("scan_trigger_count", 0)
    # Only names not already used to trigger a scan today
    fresh = [(tk, chg, key) for tk, chg, key in big_movers if key not in triggered]
    if not fresh:
        return False
    if count >= MOVE_SCAN_MAX_PER_DAY:
        print(f"move-trigger: {len(fresh)} big mover(s) but daily cap "
              f"({MOVE_SCAN_MAX_PER_DAY}) reached — IBKR budget guard")
        # still record them so we don't log this every 15 min
        for tk, chg, key in fresh:
            triggered[key] = True
        return True
    last = last_scan_utc()
    if last is not None:
        age = (datetime.now(timezone.utc) - last).total_seconds() / 60
        if age < MOVE_SCAN_FRESH_MIN:
            print(f"move-trigger: big mover(s) but a scan ran {age:.0f} min ago "
                  f"(<{MOVE_SCAN_FRESH_MIN}) — data still fresh, not dispatching")
            for tk, chg, key in fresh:
                triggered[key] = True     # this scan already covers them
            return True
    names = ", ".join(f"{tk} {chg:+.1f}%" for tk, chg, key in fresh)
    print(f"move-trigger: big mover(s) [{names}] — dispatching full scan")
    if dispatch_scanner(f"big move: {names}"):
        for tk, chg, key in fresh:
            triggered[key] = True
        state["scan_trigger_count"] = count + 1
        return True
    return False


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
    big_movers = []          # (ticker, chg, key) for names past MOVE_SCAN_PCT
    changed = False
    for tk in watch:
        price, chg = yahoo_quote(tk)
        if price is None:
            continue
        if abs(chg) < MOVE_ALERT_PCT:
            continue
        key = f"{tk}:{'up' if chg > 0 else 'down'}"
        # Big moves still trigger a full scan regardless of proximity — that
        # path surfaces LEAPS BUY_DIP etc. independent of these targets.
        if abs(chg) >= MOVE_SCAN_PCT:
            big_movers.append((tk, chg, key))
        # A13 proximity gate: only PING when the move lands near something
        # actionable (buy-under / sell-above / a held strike). Applied before
        # the dedup mark so a name that later moves INTO range still alerts.
        worthy, why = near_actionable(tk, price, chg, targets, short_pos)
        if not worthy:
            continue
        bucket = int(abs(chg) // 5) * 5          # 5, 10, 15, ...
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

    # Move-triggered full scan: a big mover gets a fresh candidate now
    changed = run_move_trigger(state, big_movers) or changed

    # Watchdog: rescue late/dropped scheduled scans
    changed = run_watchdog(state) or changed

    if changed:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
        print("state updated")


if __name__ == "__main__":
    main()
