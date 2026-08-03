"""
Earnings Watcher — post-report results + commentary to Telegram.

WHAT IT DOES
------------
Twice a day it asks "did anything I care about just report?" and, if so, sends
one compact Telegram message per reporter containing:
  - the headline numbers (EPS actual vs consensus) from a structured feed
  - the actual after-hours / pre-market price reaction
  - YOUR position on that name (strike, expiry, where the gap leaves you)
  - a few lines of interpretation

WHY TWO RUNS
------------
The 3x/day full scans land 9:47 / 12:41 / 2:47 PM ET and the Move Watcher stops
at 16:05 ET — so both earnings windows are currently blind. Releases land
~4:05-4:30 PM ET (after close) and ~6:00-8:30 AM ET (before open). Hence:
  - after-close run ~5:15 PM ET  → that evening's reporters
  - pre-open run    ~7:30 AM ET  → morning reporters + anything the evening
                                   run missed (it re-checks yesterday's AMC names)

DIVISION OF LABOUR (deliberate)
-------------------------------
Every NUMBER in the message is computed here, in Python, from a feed:
EPS/consensus from Nasdaq, price reaction from Yahoo, position P&L and
strike distance from the last scan's results.json. Claude is handed those
figures as established facts and asked only for INTERPRETATION — it is told
not to restate or recompute them. A confident-but-wrong number is the worst
outcome this system can produce, so the model is never the source of one.

SCOPE
-----
Names where a short option is open (earnings is a position event) plus
watchlist names trading within EARN_NEAR_TARGET_PCT of buy_under / sell_above
(earnings is an entry event). Everything else is noise — widen by raising the
constant or setting EARN_SCOPE_ALL.

NO Schwab / IBKR CALLS. Yahoo + Nasdaq + Anthropic only, so this never burns
broker tokens or the ~10/day IBKR Flex budget.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import earnings_calendar as ecal

# ── tunables ─────────────────────────────────────────────────────────────
EARN_NEAR_TARGET_PCT = 15.0   # watchlist name within this % of a target is in scope
EARN_SCOPE_ALL       = False  # True = report on every watchlist ticker
EARN_MAX_PER_RUN     = 6      # hard cap on Claude calls per run (cost guard)

STATE_FILE   = "earnings_watcher_state.json"
RESULTS_FILE = "results.json"
ET           = ZoneInfo("America/New_York")
PT           = ZoneInfo("America/Los_Angeles")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")

COMMENTARY_MODEL = "claude-opus-5"
# max_tokens caps thinking + text together on this model — leave real headroom
# or the commentary truncates mid-sentence.
COMMENTARY_MAX_TOKENS = 8000


# ── helpers ──────────────────────────────────────────────────────────────

def now_et() -> datetime:
    return datetime.now(ET)


def send_telegram(msg: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("(no telegram creds — printing instead)\n" + msg)
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=15,
        )
        if r.status_code != 200:
            print(f"telegram HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"telegram send failed: {e}")


def yahoo_quote(ticker: str) -> dict:
    """Regular + pre/post-market price. {} on failure.

    The extended-hours print is the whole point here: an after-close report
    shows up in postMarketPrice hours before regularMarketPrice catches up.
    """
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            headers={"User-Agent": "Mozilla/5.0"},
            params={"interval": "1d", "range": "1d", "includePrePost": "true"},
            timeout=12,
        )
        meta = ((r.json() or {}).get("chart") or {}).get("result") or [{}]
        meta = (meta[0] or {}).get("meta") or {}
        close = meta.get("regularMarketPrice")
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        post = meta.get("postMarketPrice")
        pre = meta.get("preMarketPrice")
        if not close:
            return {}
        latest = post or pre or close
        return {
            "close": float(close),
            "prev_close": float(prev) if prev else None,
            "post": float(post) if post else None,
            "pre": float(pre) if pre else None,
            "latest": float(latest),
            # Move measured from the regular-session close — that is the gap
            # the earnings release itself caused.
            "ext_move_pct": (float(latest) / float(close) - 1.0) * 100.0,
            "day_move_pct": ((float(close) / float(prev) - 1.0) * 100.0) if prev else None,
        }
    except Exception as e:
        print(f"  quote failed for {ticker}: {e}")
        return {}


def fmt_expiry(exp: str) -> str:
    """'20260821' -> '8/21'. Passes anything else through unchanged."""
    try:
        d = datetime.strptime(str(exp), "%Y%m%d")
        return f"{d.month}/{d.day}"
    except Exception:
        return str(exp)


def load_universe():
    """(targets, short_positions) — same shape/sources as the Move Watcher."""
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
                    targets[row["ticker"].strip().upper()] = {"buy_under": 0, "sell_above": 0}
        except Exception as e2:
            print(f"buckets.csv fallback failed too: {e2}")

    short_pos = {}
    try:
        with open(RESULTS_FILE) as f:
            res = json.load(f)
        for pa in res.get("position_actions", []):
            tk = (pa.get("ticker") or "").upper()
            if tk:
                short_pos.setdefault(tk, []).append(pa)
    except Exception as e:
        print(f"no positions from {RESULTS_FILE} ({e})")

    return targets, short_pos


def in_scope(tk: str, targets: dict, short_pos: dict, quote: dict) -> tuple:
    """Is this name worth a report? Returns (bool, reason)."""
    if EARN_SCOPE_ALL:
        return True, "watchlist"
    if tk in short_pos:
        return True, "open short option"
    tgt = targets.get(tk) or {}
    price = quote.get("latest") or quote.get("close")
    if not price:
        return False, ""
    bu, sa = tgt.get("buy_under", 0) or 0, tgt.get("sell_above", 0) or 0
    if bu > 0 and price <= bu * (1 + EARN_NEAR_TARGET_PCT / 100):
        return True, f"near buy-under ${bu:g}"
    if sa > 0 and price >= sa * (1 - EARN_NEAR_TARGET_PCT / 100):
        return True, f"near sell-above ${sa:g}"
    return False, ""


# ── position context (all arithmetic done here, not by the model) ────────

def position_lines(tk: str, short_pos: dict, quote: dict) -> list:
    """Human-readable position context, with post-gap strike distance."""
    lines = []
    latest = quote.get("latest")
    for p in short_pos.get(tk, []):
        ptype = p.get("type", "")
        strike = p.get("strike", 0) or 0
        if not strike:
            continue
        bits = [f"short {ptype} ${strike:g} {fmt_expiry(p.get('expiry',''))}"]
        dte = p.get("dte")
        if dte is not None:
            bits.append(f"{dte}d left")
        prof = p.get("profit_pct")
        src = p.get("mark_src", "")
        if src in ("incredible", "none"):
            bits.append("⚠ price stale — check live")
        elif prof is not None:
            # "-1% profit" reads wrong on a losing position.
            bits.append(f"{prof:.0f}% profit" if prof >= 0 else f"down {abs(prof):.0f}%")
        line = f"   You hold {', '.join(bits)}"

        # One decimal: a position 4.7% ITM should not print as "5%".
        if latest and strike:
            gap = (latest - strike) / strike * 100.0
            if ptype == "CSP":
                line += (f"\n   → after the move ${latest:,.2f} is "
                         + (f"{gap:.1f}% ABOVE your put strike"
                            if gap >= 0 else f"{abs(gap):.1f}% BELOW your put strike — ITM"))
            elif ptype == "CC":
                line += (f"\n   → after the move ${latest:,.2f} is "
                         + (f"{gap:.1f}% ABOVE your call strike — ITM"
                            if gap >= 0 else f"{abs(gap):.1f}% below your call strike"))
        lines.append(line)
    return lines


def target_line(tk: str, targets: dict, quote: dict) -> str:
    """Target context for an unheld name.

    Shows the target the price is actually NEAR — quoting "buy-under $85: 109%
    above target" on a name that just ran into its sell-above is technically
    true and completely useless. Falls back to the nearer of the two.
    """
    tgt = targets.get(tk) or {}
    bu, sa = tgt.get("buy_under", 0) or 0, tgt.get("sell_above", 0) or 0
    price = quote.get("latest")
    if not price:
        return ""

    def buy_line():
        rel = (price - bu) / bu * 100.0
        return (f"   Buy-under ${bu:g}: now {abs(rel):.1f}% "
                f"{'BELOW' if rel < 0 else 'above'} target"
                + (" — CSP territory" if rel < 5 else ""))

    def sell_line():
        rel = (price - sa) / sa * 100.0
        return (f"   Sell-above ${sa:g}: now {abs(rel):.1f}% "
                f"{'ABOVE' if rel > 0 else 'below'} target"
                + (" — CC territory" if rel > -5 else ""))

    near_buy  = bu > 0 and price <= bu * (1 + EARN_NEAR_TARGET_PCT / 100)
    near_sell = sa > 0 and price >= sa * (1 - EARN_NEAR_TARGET_PCT / 100)
    if near_buy and not near_sell:
        return buy_line()
    if near_sell and not near_buy:
        return sell_line()
    if near_buy and near_sell:
        return buy_line() + "\n" + sell_line()
    # Neither is close — show whichever the price sits nearer to.
    if bu > 0 and sa > 0:
        return buy_line() if abs(price - bu) <= abs(price - sa) else sell_line()
    return buy_line() if bu > 0 else (sell_line() if sa > 0 else "")


# ── commentary (interpretation only — never the source of a number) ──────

def get_commentary(tk: str, facts: str) -> str:
    """Ask Claude to interpret an already-established set of facts.

    Uses the server-side web_search tool so it can read the actual release and
    guidance rather than answering from training data. Returns "" on any
    failure — a missing commentary block degrades the message, it doesn't
    break it, and that is much better than inventing one.
    """
    if not ANTHROPIC_API_KEY:
        print("  (no ANTHROPIC_API_KEY — skipping commentary)")
        return ""
    try:
        import anthropic
    except ImportError:
        print("  (anthropic package not installed — skipping commentary)")
        return ""

    system = (
        "You brief an active options-income trader on earnings results. He sells "
        "cash-secured puts and covered calls, buys deep-ITM LEAPS, and holds a "
        "~32-name watchlist. He is blunt and time-poor: facts first, no filler, "
        "no hedging language, no restating the question.\n\n"
        "RULES:\n"
        "1. The FACTS block below is already verified. Do NOT restate the EPS, "
        "revenue, price, or position numbers given to you — they are printed "
        "elsewhere in the message. Do not recompute or contradict them.\n"
        "2. Use web search to find what the FACTS do not cover: forward guidance, "
        "segment detail, why the stock moved the way it did, any notable "
        "management commentary.\n"
        "3. If you cannot verify something, say so plainly ('guidance not yet "
        "available'). Never guess or infer a figure. 'Uncertain' is an "
        "acceptable and expected answer.\n"
        "4. Assignment is not a risk he manages away — he sells at prices he is "
        "happy to own or sell at. Do NOT recommend defensive closes. Do flag "
        "when a result changes the underlying thesis.\n"
        "5. Plain English over jargon. Be accurate: a percentage-point gap is "
        "not a percentage.\n\n"
        "FORMAT: 3-5 short lines of plain prose. No headers, no bullet symbols, "
        "no markdown bold. End with one line starting 'Source: ' giving the "
        "single most useful URL. Nothing else."
    )
    prompt = (
        f"{tk} just reported earnings. Search for the results and guidance, then "
        f"write the briefing.\n\nFACTS ALREADY ESTABLISHED (do not restate):\n{facts}"
    )

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        messages = [{"role": "user", "content": prompt}]
        resp = None
        for _ in range(4):                       # bounded pause_turn resume loop
            resp = client.messages.create(
                model=COMMENTARY_MODEL,
                max_tokens=COMMENTARY_MAX_TOKENS,
                output_config={"effort": "medium"},
                system=system,
                tools=[{"type": "web_search_20260209", "name": "web_search"}],
                messages=messages,
            )
            if resp.stop_reason != "pause_turn":
                break
            # Server-tool loop hit its iteration cap — resend to continue.
            messages = [{"role": "user", "content": prompt},
                        {"role": "assistant", "content": resp.content}]

        if resp is None:
            return ""
        if resp.stop_reason == "refusal":
            print(f"  commentary refused for {tk}")
            return ""
        text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
        return text
    except Exception as e:
        print(f"  commentary failed for {tk}: {type(e).__name__}: {e}")
        return ""


# ── message assembly ─────────────────────────────────────────────────────

def build_report(tk: str, info: dict, quote: dict, actuals: dict,
                 targets: dict, short_pos: dict, reason: str) -> str:
    when = {"amc": "after close", "bmo": "before open"}.get(info.get("time", ""), "")
    head = f"📊 *{tk}* — earnings{f' ({when})' if when else ''}"

    num_lines, facts = [], []

    # EPS: actual vs consensus. Consensus alone still beats nothing.
    cons = actuals.get("eps_consensus") if actuals else None
    if cons is None:
        cons = ecal.consensus_eps(tk)
    act = actuals.get("eps_actual") if actuals else None
    if act is not None and cons is not None:
        beat = act - cons
        verdict = "beat" if beat > 0 else ("miss" if beat < 0 else "in line")
        num_lines.append(f"EPS ${act:.2f} vs ${cons:.2f} est — {verdict} by ${abs(beat):.2f}")
        facts.append(f"EPS actual ${act:.2f} vs consensus ${cons:.2f} ({verdict})")
    elif act is not None:
        num_lines.append(f"EPS ${act:.2f} (no consensus on file)")
        facts.append(f"EPS actual ${act:.2f}; consensus unknown")
    elif cons is not None:
        num_lines.append(f"EPS consensus was ${cons:.2f} — actual not yet on the feed")
        facts.append(f"EPS consensus ${cons:.2f}; actual not yet published to the data feed")
    else:
        num_lines.append("EPS figures not yet available on the feed")
        facts.append("No EPS figures available from the structured feed yet")

    if actuals and actuals.get("quarter"):
        facts.append(f"Fiscal quarter {actuals['quarter']}")

    # Price reaction — from Yahoo, measured off the regular-session close.
    if quote:
        latest = quote["latest"]
        ext = quote.get("ext_move_pct") or 0.0
        tag = ("after hours" if quote.get("post")
               else "pre-market" if quote.get("pre") else "at the close")
        if abs(ext) >= 0.05:
            arrow = "🟢▲" if ext > 0 else "🔴▼"
            num_lines.append(f"{arrow} ${latest:,.2f}, {ext:+.1f}% {tag} "
                             f"(close ${quote['close']:,.2f})")
            facts.append(f"Stock ${latest:,.2f}, {ext:+.1f}% {tag} vs "
                         f"the ${quote['close']:,.2f} close")
        else:
            num_lines.append(f"${latest:,.2f} — no meaningful move yet {tag}")
            facts.append(f"Stock ${latest:,.2f}, essentially flat {tag}")

    body = [head, ""] + num_lines

    pos = position_lines(tk, short_pos, quote)
    if pos:
        body += [""] + pos
        for p in short_pos.get(tk, []):
            facts.append(f"Trader is short a {p.get('type')} ${p.get('strike'):g} "
                         f"expiring {fmt_expiry(p.get('expiry',''))} "
                         f"({p.get('dte')}d), {p.get('contracts')} contracts")
    else:
        tl = target_line(tk, targets, quote)
        if tl:
            body += ["", tl]
        facts.append(f"No open option position; on the watchlist ({reason})")

    commentary = get_commentary(tk, "\n".join(f"- {f}" for f in facts))
    if commentary:
        body += ["", commentary]
    else:
        body += ["", "_(commentary unavailable — numbers above are from the feed)_"]

    return "\n".join(body)


# ── window / dedup ───────────────────────────────────────────────────────

def resolve_window(dt: datetime) -> str:
    """'after_close' or 'pre_open', overridable via EARN_WINDOW / argv."""
    forced = (os.environ.get("EARN_WINDOW") or "").strip().lower()
    if len(sys.argv) > 1 and sys.argv[1] in ("after_close", "pre_open"):
        forced = sys.argv[1]
    if forced in ("after_close", "pre_open"):
        return forced
    return "after_close" if dt.hour >= 12 else "pre_open"


def due_reporters(window: str, dt: datetime, universe=None) -> list:
    """Who reported in this window? Returns [(ticker, info)].

    after_close → today's AMC names (and today's unknown-time names).
    pre_open    → today's BMO names, PLUS yesterday's AMC names as a catch-up
                  so a failed or skipped evening run does not lose a report.
    """
    today = dt.strftime("%Y-%m-%d")
    out = []
    if window == "after_close":
        for tk, info in ecal.reporters_on(today, universe):
            if info.get("time") in ("amc", ""):
                out.append((tk, info))
    else:
        for tk, info in ecal.reporters_on(today, universe):
            if info.get("time") in ("bmo", ""):
                out.append((tk, info))
        prev = dt - timedelta(days=1)
        for tk, info in ecal.reporters_on(prev.strftime("%Y-%m-%d"), universe):
            if info.get("time") in ("amc", ""):
                out.append((tk, info))
    # de-duplicate while preserving order
    seen, uniq = set(), []
    for tk, info in out:
        if tk not in seen:
            seen.add(tk)
            uniq.append((tk, info))
    return uniq


def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
        return s if isinstance(s, dict) else {}
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2, sort_keys=True)
    except Exception as e:
        print(f"could not write {STATE_FILE}: {e}")


# ── main ─────────────────────────────────────────────────────────────────

def main():
    dt = now_et()
    window = resolve_window(dt)
    print(f"{dt:%Y-%m-%d %H:%M ET} — earnings watcher ({window})")

    if dt.weekday() >= 5:
        print("weekend — nothing reports, exiting")
        return

    targets, short_pos = load_universe()
    watch = sorted(set(targets) | set(short_pos))
    if not watch:
        print("empty universe — nothing to watch")
        return

    # Keep the calendar current. No-ops unless it wasn't built today, so the
    # ~35-request Nasdaq sweep happens once daily (normally on the pre-open run).
    ecal.refresh(tickers=watch)
    ecal.report_health()

    due = due_reporters(window, dt, universe=watch)
    if not due:
        print("no watched names reported in this window")
        return
    print(f"candidates: {', '.join(tk for tk, _ in due)}")

    state = load_state()
    alerted = state.setdefault("alerted", {})

    reports, changed, used = [], False, 0
    for tk, info in due:
        key = f"{tk}:{info.get('date','')}"
        if alerted.get(key):
            print(f"  {tk}: already reported for {info.get('date')} — skipping")
            continue
        if used >= EARN_MAX_PER_RUN:
            print(f"  {tk}: per-run cap ({EARN_MAX_PER_RUN}) reached — deferring to next run")
            continue

        quote = yahoo_quote(tk)
        ok, reason = in_scope(tk, targets, short_pos, quote)
        if not ok:
            print(f"  {tk}: reported but not in scope (no position, far from targets)")
            # Mark it so we don't re-evaluate every run for the same report.
            alerted[key] = "out_of_scope"
            changed = True
            continue

        print(f"  {tk}: in scope ({reason}) — building report")
        actuals = ecal.fetch_reported_actuals(tk) or {}
        reports.append(build_report(tk, info, quote, actuals, targets, short_pos, reason))
        alerted[key] = True
        changed = True
        used += 1

    if reports:
        header = ("🔔 *EARNINGS — "
                  + ("after the close" if window == "after_close" else "before the open")
                  + f", {dt:%a %b %-d}*\n"
                  "_Numbers from the data feed; commentary is web-sourced._")
        send_telegram(header + "\n\n" + "\n\n———\n\n".join(reports))
        print(f"sent {len(reports)} earnings report(s)")
    else:
        print("nothing new to report")

    if changed:
        save_state(state)
        print("state updated")


if __name__ == "__main__":
    main()
