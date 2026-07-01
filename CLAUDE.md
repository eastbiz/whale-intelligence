# Whale Intelligence — Claude Code Project Guide

This file is read automatically at the start of every Claude Code session. It is
the single source of truth for how this project works, what the trading
philosophy is, and the conventions to follow. Keep it updated as the system
changes.

---

## What this system is

A proprietary options-trading intelligence platform for a multi-account
portfolio (Schwab IRA / CRT / Personal + IBKR). It scans a ~32-ticker watchlist
3× per weekday, surfaces CSP / CC / LEAPS / Convexity opportunities, monitors
open positions for exits, and delivers everything to a dashboard + Telegram.

**Owner:** John. Active options trader. Direct, blunt communication preferred —
no filler, facts first, say "uncertain" rather than guess.

### Two repositories (tightly coupled)
- **`eastbiz/whale-intelligence`** — Python scanner. `whale_scanner.py` (~6,700
  lines) is the core. This is where ~95% of work happens.
- **`eastbiz/whale-dashboard`** — `index.html`, a single-page JS dashboard
  deployed on GitHub Pages. Reads `results.json` from the scanner repo via
  `raw.githubusercontent.com` (bypasses CDN caching).

**Data flow:** scanner builds per-mode lists → merged into `results.json` →
dashboard reads it, filters by `mode` / `action`, renders cards or tables.
Dashboard is authoritative; **Telegram is derived from it, not computed
independently.**

### Automation
- GitHub Actions runs the scan ~3× per weekday (approx 6:43, 9:37, 11:43 AM ET).
- Results publish to `results.json`; alerts fire via Telegram (the primary
  action channel — John does NOT check the dashboard daily).

---

## Trading philosophy (the rules that drive the logic)

- **Assignment is NOT a risk to manage away.** John writes CSPs/CCs at prices
  he's comfortable owning/selling. No defensive-close recommendations except
  TAKE PROFIT and the BIG MOVE / earnings alerts below.
- **Profit-taking target: 80–90% of max premium** for routine closes.
- **Zone-first (CCs):** never recommend a CC when the stock is below the
  midpoint of the buy_under/sell_above band. Writing CCs below cost basis locks
  in losses. Applied uniformly — no exceptions for cc_only tickers.
- **Strict filters surface rare value.** Zero results is an acceptable, expected
  outcome (especially convexity). Do NOT loosen filters to fill the page.
- **Volatile names (NBIS, CRDO, CLS) are the whole point** of the move-based
  alerts. They jump 10%+ in a day; those are the moments that matter.
- **CCs on explosive winners cap upside.** NBIS covered-call assignments have
  repeatedly created missed upside. Flag this; don't praise CC premium blindly.

---

## Key modules & scanners

- **CSP / CC engine** (`csp_engine`, `find_best_cc`) — bucket-aware premium
  selling. Buckets A–D with annualized-return minimums 12/18/28/40%.
- **Deep-ITM LEAPS** (`find_best_leaps`) — stock-replacement, delta ≥0.75, three
  bands (Conservative / Sweet spot / More leverage).
- **Cheap Convexity LEAPS** (`scan_convexity`) — far-OTM long-dated calls,
  STRICT MODE. Only Grade A/B passers shown, one best row per ticker, near-misses
  discarded. Distinct from deep-ITM LEAPS. Grade A → Telegram.
- **Spike CC** (`find_spike_cc`) — sell calls into an 8%+ up-spike on ANY held
  100+ share position. Overrides `spreads_only` (a CC on owned shares isn't
  naked). Goes to Telegram.
- **Post-Drop CSP** — sell puts into a drop. Over-gated historically; TRADING
  tier excluded. Known limited.
- **Position management engine** (`position_management_engine`) — per-position
  exit actions. See "Position exit alerts" below.

---

## Position exit alerts (recent, important)

The engine returns ONE action per position, priority order:

1. **BIG MOVE** (priority 0, event-driven) — the main one. Fires when a big
   FAVORABLE move happens on a name you hold a short option:
   - CC + stock drops ≥10% in a day (or ≥ `BIGMOVE_3D`, off by default)
   - CSP + stock rises ≥10% in a day
   - **No profit floor, no strike gate.** The move alone triggers it. Profit %,
     strike distance, cost-to-close are shown as CONTEXT, not gates. This
     replaced two earlier gated alerts (CLOSE NOW + ESCAPE ASSIGNMENT).
   - Goes to Telegram. Editable constants: `BIGMOVE_1D` (0.10), `BIGMOVE_3D`
     (0.99 = off).
2. **TAKE PROFIT** — profit ≥ 80% (speculative/trading) or 90% (core/growth).
3. **EARNINGS WARNING** — earnings inside the danger window.
4. **HOLD** — default.

### Mark credibility check (critical — prevents false P&L)
Broker position marks can be STALE on a fast intraday move (e.g. a deep-OTM
call still marked at its pre-drop price). The engine guards against this:
- Option mark is sourced **live chain NBBO first**, position-feed mark only as
  fallback (`mark_src` tracks the source: `chain` / `chain_near` / `position_mv`
  / `none` / `incredible`).
- **Sanity check:** if a short option is >20% OTM but the mark implies <60%
  profit, that's not physically credible → `mark_src = "incredible"`. The alert
  then says "check the live option price (mark may be stale)" instead of printing
  a false P&L. Dashboard shows "⚠ price stale — check live".
- If you touch mark logic, preserve this. A confident-but-wrong P&L is worse
  than no number.

---

## Configuration

- **`SYMBOL_SETTINGS`** dict in `whale_scanner.py` (~line 164) — per-ticker
  buy_under / sell_above / delta ranges / flags. `buy_under = 0` means NO BUY
  (currently AAPL, NFLX, IBIT, PATH, MSTR).
- **`buckets.csv`** — ticker → bucket (A–D) + special flags. Must sit in the
  same directory as `whale_scanner.py` and `bucket_config.py`.
- **`bucket_config.py`** — bucket loader + `strategy_allowed()` gate. Must be
  co-located for `load_buckets()` to import.
- **Special flags:** `spreads_only` (NBIS, CRDO — block naked CSP/CC, route to
  spreads), `leaps_only` (BABA), `cc_only` (MSTR, OWL — exit-waiting), and
  `watchlist` tier (META).
- **Feature flags:** `ENABLE_PIO = False` (Position Income Optimization, noisy),
  `STRICT_ZONE_TELEGRAM = False`.
- **Editable alert thresholds** (top of file): `BIGMOVE_1D`, `BIGMOVE_3D`,
  convexity `CVX_*` constants, `MAX_CC_COVERAGE_PCT`.

---

## KNOWN GOTCHAS (read before editing)

- **Multiple CC code paths.** CC logic exists in ≥3 places: `find_best_cc()`
  (~2834), the inline CC scanner (~5108), and the inline PIO scanner (~5217).
  **Any CC behavior change must be applied to ALL paths** or unpatched paths keep
  firing stale behavior.
- **Stale option marks** — see mark credibility check above. The #1 source of
  wrong alerts historically.
- **`strikeCount: 50`** on the Schwab chain fetch — for high-priced or
  far-OTM strikes (wide-priced names, deep convexity strikes), the strike may
  fall outside the 50-strike window and not be fetched. Watch for this when a
  held position or convexity candidate silently produces nothing.
- **Cheap-stock filters** — `find_spike_cc` has a `mid < 0.50` premium floor and
  liquidity minimums that can block legitimate CCs on low-priced names like PATH.
  Open issue.
- **Yahoo weekend price inversion** — price fields swap on weekends, inverting
  apparent moves. Move logic must account for it. Prefer weekday live data.
- **IBKR Flex rate limit** — ~10 requests/day per token. Don't exhaust it with
  repeated manual scans while debugging. Cache fallback:
  `ibkr_positions_cache.json` when fresh data < 50% of cached count; XML fallback
  `ibkr_positions.xml`. Flex Query ID: 1434153.
- **Schwab token expires every 7 days.** Refresh on Windows only:
  `python refresh_token.py` in `C:\Users\John\scanner`, App Key
  `ZMZSlpMaNaFGSbIvJFb3pxNlOxwFFUPzgPtOevHgrj3zmAHj`, callback
  `https://127.0.0.1:8182`. After renewal, update both `SCHWAB_REFRESH_TOKEN`
  and `SCHWAB_ACCESS_TOKEN` in GitHub Actions secrets. Chrome SSL bypass:
  "Insecure origins treated as secure" flag → enter the callback URL.
- **IVP ≠ IV Rank.** The scanner only has IVP (percentile), computed as
  `100 * (1 - exp(-atm_iv / 0.25))`. Never use "IV Rank" language. IVP can be
  stale on weekends.
- **Scan cadence limitation** — 3×/weekday cron misses fast intraday moves. A
  10% spike can happen and fade between scans. Open architectural item (an
  intraday lightweight move-watcher was discussed, not built).

---

## WORKFLOW CONVENTIONS (how John wants work done)

- **Discuss design before implementing.** Confirm scope and parameters first,
  especially for risk logic. Walk through real trade examples before coding new
  alert rules.
- **Deliver complete files**, never partial diffs alone (this mattered in the
  chat workflow; in Claude Code, normal edits are fine — but always show what
  changed).
- **Syntax-check before declaring done:**
  - Python: `python3 -c "import ast; ast.parse(open('whale_scanner.py').read())"`
  - Dashboard JS: extract inline `<script>` to a temp file, run `node --check`.
- **Verify arithmetic carefully.** John has flagged arithmetic errors and
  inconsistent numbers as confidence-killers. Double-check every figure.
- **Validate new alert logic against real historical trade examples** with
  explicit expected outputs before finalizing.
- **Plain-English labels** over jargon ("better/worse", not "pp"; "cheaper" over
  technical terms) — but be accurate (don't call a percentage-point diff a "%").
- **Test commands:** `python3 bucket_config.py` should print the loaded bucket
  count and pass its self-tests.

---

## Deployment

- Push `whale_scanner.py` (+ `bucket_config.py`, `buckets.csv`) to
  `eastbiz/whale-intelligence`.
- Push `index.html` to `eastbiz/whale-dashboard`.
- The dashboard shows nothing new until the next scan writes fresh
  `results.json`.

---

## Open items / backlog

- Spread scanner for CRDO/NBIS on normal (non-spike) days — built standalone
  (`spread_scanner.py`), never integrated.
- Grade B convexity → Telegram (currently Grade A only).
- PATH / cheap-stock spike-CC filters too strict (premium floor, liquidity).
- Intraday move-watcher for faster spike/drop detection.
- Trade journaling + performance analysis (deferred; see the separate
  "Trading Performance Review" handoff John maintains for the analysis spec —
  benchmarks vs SPY/QQQ, CSP/CC efficiency, DTE-bucket comparison).
