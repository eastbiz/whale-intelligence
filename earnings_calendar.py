"""
Earnings calendar — date lookup with a real fallback chain and an on-disk cache.

WHY THIS EXISTS
---------------
Yahoo's `quoteSummary?modules=calendarEvents` feed went silent (it now requires
a cookie + crumb pair Yahoo added to block scraping). Every ticker without a
manual EARNINGS_OVERRIDE entry therefore resolved to the 999 "unknown" sentinel,
which the scanner reads as SAFE — so the CC earnings gates (A15/A17, P21/P23)
and EARNINGS WARNING were silently passing everything through.

A missing earnings date is the dangerous failure here, not a wrong one: it
disables risk logic without any visible symptom. So this module tries several
independent sources and, when they all fail, falls back to a committed cache
rather than reporting "no earnings known".

SOURCE ORDER (first hit wins)
  1. Manual override      — caller-supplied (whale_scanner.EARNINGS_OVERRIDE)
  2. Nasdaq calendar      — keyless; ALSO gives report time + consensus EPS
  3. Yahoo (cookie+crumb) — per-ticker fallback for names Nasdaq missed
  4. Cache                — last known good, committed to the repo

The Nasdaq sweep is a per-day calendar, so one pass builds the whole watchlist
at once. It runs at most once per day (see `refresh_needed`); every other caller
reads the cache for free.

NOTE: none of these endpoints were reachable from the environment this module
was written in (egress policy blocked them), so the parsers are deliberately
defensive — any unexpected shape degrades to "unknown" rather than raising, and
`report_health()` makes a dead source loud instead of silent.
"""

import json
import os
import time
from datetime import datetime, timedelta
from typing import Optional

import requests

CALENDAR_CACHE_FILE = "earnings_calendar.json"
CACHE_STALE_DAYS    = 3      # older than this and we re-sweep even mid-day
SWEEP_DAYS_AHEAD    = 50     # CC gate needs DTE(<=45) + 5-day buffer of lookahead
SWEEP_DAYS_BACK     = 3      # so a just-reported name still resolves (catch-up)
NASDAQ_PAUSE_SEC    = 0.4    # be polite; the sweep is ~35 weekday requests

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
_NASDAQ_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nasdaq.com/",
}

# Health counters — surfaced by report_health() so a dead source is never silent
_STATS = {
    "nasdaq_days_ok": 0, "nasdaq_days_failed": 0,
    "yahoo_ok": set(), "yahoo_failed": set(),
    "from_cache": set(), "unknown": set(),
    "last_error": "",
}

_CACHE = None          # lazily loaded; {"built": "YYYY-MM-DD", "tickers": {...}}


# ── cache ────────────────────────────────────────────────────────────────

def load_cache() -> dict:
    """Read the committed calendar cache. Never raises."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        with open(CALENDAR_CACHE_FILE) as f:
            c = json.load(f)
        _CACHE = c if isinstance(c, dict) and "tickers" in c else {"built": "", "tickers": {}}
    except Exception:
        _CACHE = {"built": "", "tickers": {}}
    return _CACHE


def save_cache(cache: dict) -> None:
    global _CACHE
    _CACHE = cache
    try:
        with open(CALENDAR_CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2, sort_keys=True)
    except Exception as e:
        print(f"   ⚠️ could not write {CALENDAR_CACHE_FILE}: {e}")


def refresh_needed(cache: Optional[dict] = None) -> bool:
    """True when the cache was not built today (or is missing/stale)."""
    cache = cache if cache is not None else load_cache()
    built = cache.get("built", "")
    if not built:
        return True
    try:
        age = (datetime.now().date() - datetime.strptime(built, "%Y-%m-%d").date()).days
    except Exception:
        return True
    return age >= 1 or age > CACHE_STALE_DAYS


# ── source 2: Nasdaq day-by-day calendar (keyless) ───────────────────────

def fetch_nasdaq_day(day: datetime) -> dict:
    """One day of the Nasdaq earnings calendar.

    Returns {TICKER: {"date","time","eps_forecast","name"}}; {} on any failure.
    `time` is normalised to "bmo" (before open) / "amc" (after close) / "" .
    """
    out = {}
    try:
        r = requests.get(
            "https://api.nasdaq.com/api/calendar/earnings",
            params={"date": day.strftime("%Y-%m-%d")},
            headers=_NASDAQ_HEADERS, timeout=15,
        )
        if r.status_code != 200:
            _STATS["nasdaq_days_failed"] += 1
            _STATS["last_error"] = f"nasdaq calendar HTTP {r.status_code}"
            return {}
        rows = ((r.json() or {}).get("data") or {}).get("rows") or []
        for row in rows:
            sym = (row.get("symbol") or "").strip().upper()
            if not sym:
                continue
            raw_time = (row.get("time") or "").lower()
            when = ("bmo" if "pre-market" in raw_time or "before" in raw_time
                    else "amc" if "after-hours" in raw_time or "after" in raw_time
                    else "")
            out[sym] = {
                "date": day.strftime("%Y-%m-%d"),
                "time": when,
                "eps_forecast": _money(row.get("epsForecast")),
                "name": (row.get("name") or "").strip(),
            }
        _STATS["nasdaq_days_ok"] += 1
    except Exception as e:
        _STATS["nasdaq_days_failed"] += 1
        _STATS["last_error"] = f"{type(e).__name__}: {e}"
    return out


def _money(v):
    """'$0.20' / '($0.05)' -> 0.20 / -0.05. None when unparseable."""
    if v is None:
        return None
    s = str(v).strip().replace("$", "").replace(",", "")
    if not s or s in ("N/A", "-", "--"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    try:
        f = float(s)
    except ValueError:
        return None
    return -f if neg else f


def sweep_nasdaq(days_back: int = SWEEP_DAYS_BACK,
                 days_ahead: int = SWEEP_DAYS_AHEAD) -> dict:
    """Walk the Nasdaq calendar across a date window, weekdays only.

    Earliest date wins per ticker so we record the NEXT report, not a later one.
    """
    found = {}
    start = datetime.now() - timedelta(days=days_back)
    for i in range(days_back + days_ahead + 1):
        day = start + timedelta(days=i)
        if day.weekday() >= 5:            # no earnings calendar on weekends
            continue
        for sym, info in fetch_nasdaq_day(day).items():
            if sym not in found or info["date"] < found[sym]["date"]:
                found[sym] = info
        time.sleep(NASDAQ_PAUSE_SEC)
    return found


# ── source 3: Yahoo with cookie + crumb (per-ticker fallback) ────────────

_YAHOO = {"session": None, "crumb": ""}


def _yahoo_session():
    """Cookie+crumb pair Yahoo now requires on quoteSummary. Cached per process."""
    if _YAHOO["session"] is not None:
        return _YAHOO["session"], _YAHOO["crumb"]
    try:
        s = requests.Session()
        s.headers.update({"User-Agent": _UA})
        # Priming call sets the A1/A3 consent cookies the crumb is bound to.
        s.get("https://fc.yahoo.com", timeout=8)
        r = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=8)
        crumb = (r.text or "").strip()
        if r.status_code == 200 and crumb and "<" not in crumb:
            _YAHOO["session"], _YAHOO["crumb"] = s, crumb
            return s, crumb
        _STATS["last_error"] = f"yahoo crumb HTTP {r.status_code}"
    except Exception as e:
        _STATS["last_error"] = f"yahoo crumb {type(e).__name__}: {e}"
    _YAHOO["session"], _YAHOO["crumb"] = False, ""   # False = tried and failed
    return None, ""


def fetch_yahoo_earnings_date(ticker: str) -> Optional[datetime]:
    tk = ticker.upper()
    s, crumb = _yahoo_session()
    if not s:
        _STATS["yahoo_failed"].add(tk)
        return None
    try:
        r = s.get(
            f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{tk}",
            params={"modules": "calendarEvents", "crumb": crumb}, timeout=10,
        )
        if r.status_code != 200:
            _STATS["yahoo_failed"].add(tk)
            return None
        res = ((r.json() or {}).get("quoteSummary") or {}).get("result") or []
        ts = (((res[0] if res else {}).get("calendarEvents") or {})
              .get("earnings") or {}).get("earningsDate") or []
        if ts:
            raw = ts[0].get("raw") if isinstance(ts[0], dict) else None
            if raw:
                _STATS["yahoo_ok"].add(tk)
                return datetime.fromtimestamp(raw)
        _STATS["yahoo_failed"].add(tk)
    except Exception as e:
        _STATS["yahoo_failed"].add(tk)
        _STATS["last_error"] = f"yahoo {type(e).__name__}: {e}"
    return None


# ── source: last reported actuals (keyless, structured) ──────────────────

def fetch_reported_actuals(ticker: str) -> Optional[dict]:
    """Most recent reported quarter: actual EPS vs consensus, from Nasdaq.

    Structured numbers beat model-summarised ones — these are what the Telegram
    message quotes when available. Returns None when unavailable (which is a
    normal outcome shortly after a release; the feed lags by minutes to hours).
    """
    try:
        r = requests.get(
            f"https://api.nasdaq.com/api/company/{ticker.upper()}/earnings-surprise",
            headers=_NASDAQ_HEADERS, timeout=15,
        )
        if r.status_code != 200:
            return None
        table = ((r.json() or {}).get("data") or {}).get("earningsSurpriseTable") or {}
        rows = table.get("rows") or []
        if not rows:
            return None
        row = rows[0]                      # newest quarter first
        eps    = _money(row.get("eps"))
        cons   = _money(row.get("consensusForecast"))
        if eps is None and cons is None:
            return None
        return {
            "date_reported": (row.get("dateReported") or "").strip(),
            "quarter":       (row.get("fiscalQtrEnding") or "").strip(),
            "eps_actual":    eps,
            "eps_consensus": cons,
            "surprise_pct":  _money(row.get("percentageSurprise")),
        }
    except Exception:
        return None


# ── public API ───────────────────────────────────────────────────────────

def refresh(tickers=None, force: bool = False) -> dict:
    """Rebuild the calendar cache from Nasdaq (+ Yahoo for gaps). Returns cache.

    Cheap to call: no-ops unless the cache wasn't built today or `force`.
    `tickers` limits the Yahoo gap-fill pass; the Nasdaq sweep is universe-wide
    anyway, so passing it only saves per-ticker fallback requests.
    """
    cache = load_cache()
    if not force and not refresh_needed(cache):
        return cache

    swept = sweep_nasdaq()
    merged = dict(cache.get("tickers") or {})
    today = datetime.now().strftime("%Y-%m-%d")

    if swept:
        # A swept ticker's date supersedes cache; untouched names keep their
        # cached entry (Nasdaq occasionally omits a name for a day).
        for sym, info in swept.items():
            merged[sym] = {**info, "source": "nasdaq", "fetched": today}

    # Yahoo gap-fill: only for requested names the sweep produced no FUTURE date for.
    for tk in sorted({t.upper() for t in (tickers or [])}):
        cur = merged.get(tk)
        if cur and cur.get("date", "") >= today:
            continue
        d = fetch_yahoo_earnings_date(tk)
        if d:
            merged[tk] = {"date": d.strftime("%Y-%m-%d"), "time": "",
                          "eps_forecast": None, "name": "",
                          "source": "yahoo", "fetched": today}

    # Only claim "built today" if something actually came back — otherwise a
    # dead network would freeze a stale cache in place looking fresh.
    built = today if (swept or _STATS["yahoo_ok"]) else cache.get("built", "")
    out = {"built": built, "tickers": merged}
    save_cache(out)
    return out


def next_earnings_date(ticker: str, override: Optional[str] = None) -> Optional[datetime]:
    """Next earnings date for `ticker`, or None if genuinely unknown.

    A date in the PAST is not returned — a stale date reading as "earnings
    already passed" would silence a real warning (same rule the scanner's
    override handling uses).
    """
    tk = ticker.upper()
    today = datetime.now().date()

    if override:
        try:
            d = datetime.strptime(override, "%Y-%m-%d")
            if d.date() >= today:
                return d
        except Exception:
            pass

    info = (load_cache().get("tickers") or {}).get(tk)
    if info:
        try:
            d = datetime.strptime(info["date"], "%Y-%m-%d")
            if d.date() >= today:
                if info.get("source") == "cache-hit":
                    _STATS["from_cache"].add(tk)
                return d
        except Exception:
            pass

    d = fetch_yahoo_earnings_date(tk)
    if d and d.date() >= today:
        return d

    _STATS["unknown"].add(tk)
    return None


def report_time(ticker: str) -> str:
    """'bmo' | 'amc' | '' for the cached next report."""
    info = (load_cache().get("tickers") or {}).get(ticker.upper()) or {}
    return info.get("time", "")


def consensus_eps(ticker: str):
    info = (load_cache().get("tickers") or {}).get(ticker.upper()) or {}
    return info.get("eps_forecast")


def reporters_on(date_str: str, universe=None) -> list:
    """Tickers whose cached earnings date == date_str, optionally filtered.

    Returns [(ticker, info)] sorted by ticker.
    """
    uni = {t.upper() for t in universe} if universe else None
    rows = []
    for sym, info in (load_cache().get("tickers") or {}).items():
        if info.get("date") != date_str:
            continue
        if uni is not None and sym not in uni:
            continue
        rows.append((sym, info))
    return sorted(rows)


def report_health() -> None:
    """Print source health. A silently dead calendar reads as SAFE on every
    position — the same failure mode that motivated this module. Make it loud."""
    c = load_cache()
    n = len(c.get("tickers") or {})
    print(f"   📅 Earnings calendar: {n} tickers cached (built {c.get('built') or 'never'}) | "
          f"nasdaq {_STATS['nasdaq_days_ok']} days ok / {_STATS['nasdaq_days_failed']} failed | "
          f"yahoo {len(_STATS['yahoo_ok'])} ok / {len(_STATS['yahoo_failed'])} failed")
    if _STATS["unknown"]:
        print(f"      ⚠️ no earnings date for: {', '.join(sorted(_STATS['unknown']))}")
    if _STATS["last_error"]:
        print(f"      last error: {_STATS['last_error']}")
    if not n:
        print("      ⚠️ calendar EMPTY — every position reads as SAFE. "
              "Check the Nasdaq/Yahoo sources before trusting any earnings gate.")


if __name__ == "__main__":
    # Self-test / manual refresh:  python3 earnings_calendar.py [TICKER ...]
    import sys
    syms = [s.upper() for s in sys.argv[1:]] or ["PLTR", "POWL", "NBIS", "MU", "CRDO"]
    print(f"Refreshing calendar (gap-fill for {', '.join(syms)})…")
    refresh(tickers=syms, force=True)
    report_health()
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"\nReporting today ({today}): "
          f"{', '.join(t for t, _ in reporters_on(today)) or 'none'}")
    for s in syms:
        d = next_earnings_date(s)
        print(f"  {s:6s} next={d.strftime('%Y-%m-%d') if d else 'UNKNOWN':10s} "
              f"when={report_time(s) or '?':4s} consensus={consensus_eps(s)}")
