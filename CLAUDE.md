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

**`TRADING_PRINCIPLES.md`** (same repo) is the living log of John's real trade
examples and the principles distilled from them. Workflow: John shares trades →
Claude asks follow-ups / challenges from a trader viewpoint → principles
accumulate → system changes happen only after patterns are confirmed (candidates
listed there as C1, C2, …). Read it before proposing changes to entry/exit or
alert logic, and append new examples/principles to it as they come up.

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
- **Move Watcher** (`move_watcher.py` + `move-watcher.yml`): every 15 min
  during market hours, Yahoo-quotes-only check of the watchlist + names with
  open short options (from last `results.json`). Any ≥5% day move → one
  compact Telegram message with buy/sell-target and held-position context.
  **Proximity gate (A13):** a ≥5% move only PINGS when it lands near something
  actionable — within `MOVE_NEAR_TARGET_PCT` (10%) of buy_under/sell_above, or
  `MOVE_NEAR_STRIKE_PCT` (12%) of a held short strike. A big drop that leaves
  the price far above the buy target (e.g. MU −6.7% at 105% above buy) is
  silenced. Dedup: one alert per ticker/direction/day, re-alerts only when the
  move crosses the next 5% bucket (state in `move_watcher_state.json`,
  committed only when an alert fired). NO Schwab/IBKR calls — never burns
  tokens. NOTE: the ≥8% move still triggers a full scan regardless of
  proximity (that path surfaces LEAPS BUY_DIP etc.), so big movers aren't lost
  — only the raw ping is gated.
- **Move-triggered full scan** (A12 + A33, inside the Move Watcher): TWO
  independent reasons to spend a scan, sharing one budget —
  (1) **≥8% move** (`MOVE_SCAN_PCT`), regardless of where it lands (surfaces
  LEAPS BUY_DIP etc.); (2) **≥5% move that LANDS the price AT/NEAR a target**
  (`lands_in_zone()`), which is where a fresh CSP/CC card actually comes from.
  A fresh candidate then lands within ~15 min instead of at the next 3×/day
  slot. IBKR-budget guards (shared by both paths, so A33 cannot raise the
  worst case): one trigger per ticker/direction/day, hard daily cap
  `MOVE_SCAN_MAX_PER_DAY` = 3, skip if a scan ran within `MOVE_SCAN_FRESH_MIN`
  (25) min. State keys `scan_triggered` / `scan_trigger_count`.
  **`lands_in_zone()` imports `CSP_NEAR_PCT`/`CC_NEAR_PCT` from
  `whale_scanner`** — it must use the SCANNER's zone bands (5%/8%), NOT the
  looser `MOVE_NEAR_TARGET_PCT` (10%) ping proximity, or it burns budget
  dispatching scans for rows the dashboard then hides as OUT. The literals in
  `move_watcher.py` are a fallback only; the band source is logged every run.
- **Earnings Watcher** (`earnings_watcher.py` + `earnings-watcher.yml`): twice
  a weekday — **5:15 PM ET** (after-close reporters) and **7:30 AM ET**
  (before-open reporters + overnight catch-up). Both windows were previously
  blind: full scans stop at 2:47 PM ET and the Move Watcher at 16:05 ET, while
  releases land ~4:05-4:30 PM and ~6:00-8:30 AM ET. Sends one Telegram message
  per reporter: EPS actual vs consensus, the after-hours/pre-market gap, YOUR
  position on that name (strike, expiry, where the gap leaves you), then 3-5
  lines of interpretation.
  **Division of labour (deliberate — do not blur it):** every NUMBER is
  computed in Python from a feed (EPS from Nasdaq, price from Yahoo, position
  P&L and strike distance from `results.json`). Claude gets those as
  established facts and is told NOT to restate or recompute them — it supplies
  interpretation only (guidance, segment detail, why it moved) via the
  server-side `web_search` tool. A confident-but-wrong number is the worst
  output this system can produce; the model is never the source of one.
  **Scope:** `EARN_SCOPE_ALL = True` — every watchlist name, which is what John
  tracks by hand. Set it False to narrow to held short options + names within
  `EARN_NEAR_TARGET_PCT` (15%) of buy_under/sell_above; that narrower gate was
  the original default and it silently dropped PLTR on the first live run
  (mid-band between buy 85 / sell 160), so don't re-enable it without checking
  which names it excludes. `EARN_MAX_PER_RUN` (6) caps Claude calls per run.
  Dedup: one report per ticker per earnings date (`earnings_watcher_state.json`).
  Out-of-scope names are deliberately NOT written to that state — recording
  them would make a later scope change unable to surface a skipped report.
  Runtime: ~100s per report (the web-search call dominates).
  NO Schwab/IBKR calls. Needs `ANTHROPIC_API_KEY` (already a repo secret) and
  `pip install anthropic` in the workflow.
- **Earnings calendar** (`earnings_calendar.py`, cache `earnings_calendar.json`):
  Yahoo's `calendarEvents` went silent in Jul 2026 — it now requires a
  cookie+crumb pair. Result: `days_to_earnings` was the 999 sentinel on every
  un-overridden ticker, which reads as SAFE, **silently disabling the CC
  earnings gates (A15/A17, P21/P23) and EARNINGS WARNING**. Source chain, first
  hit wins: `EARNINGS_OVERRIDE` → Nasdaq keyless day calendar (also yields
  report time bmo/amc + consensus EPS) → Yahoo *with* crumb → committed cache.
  The Nasdaq sweep (~35 weekday requests over a −3/+50 day window) runs at most
  once per day; the scanner calls `ecal.refresh()` only when the cache wasn't
  built today, so it is normally free. `get_earnings_date()` delegates here;
  `report_health()` makes a dead calendar loud rather than silent.
  **Broker note:** neither Schwab nor IBKR supplies this. Schwab's Trader API
  has no earnings endpoint (its `instruments` fundamentals give dividend dates,
  not earnings dates); IBKR's earnings calendar lives in TWS/Web API Refinitiv
  fundamentals, not the Flex Query interface this repo uses. Neither broker
  provides earnings *results* at all. Third-party is the only route.
- **Watchdog self-heal** (inside the Move Watcher): GitHub's cron delivered
  every scheduled scan 60-105 min late in Jul 2026 and occasionally dropped
  runs. The watcher checks each expected slot (13:47/16:41/18:47 UTC — keep
  `SCAN_TIMES_UTC` in `move_watcher.py` in sync with `scanner.yml` crons!)
  and fires a `workflow_dispatch` on scanner.yml if a slot is >10 min overdue
  with no scan landed. The scanner's `skip_redundant_scheduled_run()` makes
  the late-arriving cron duplicate exit quietly (schedule-event runs skip if
  a scan completed <100 min ago; manual/dispatch runs ALWAYS execute).
  Worst-case scan lateness: ~20-30 min. If a watchdog dispatch ever returns
  HTTP 403 (GITHUB_TOKEN restriction), swap in a fine-grained PAT secret with
  Actions write — John has made PATs before (push_schwab_secrets.py).

---

## Trading philosophy (the rules that drive the logic)

- **Assignment is NOT a risk to manage away.** John writes CSPs/CCs at prices
  he's comfortable owning/selling. No defensive-close recommendations except
  TAKE PROFIT and the BIG MOVE / earnings alerts below.
- **Profit-taking target: 80–90% of max premium** for routine closes.
- **Zone-first (CCs):** never recommend a CC when the stock is below the
  midpoint of the buy_under/sell_above band. Writing CCs below cost basis locks
  in losses. Applied uniformly — no exceptions for cc_only tickers. **CC
  Telegram alerts go further (A14/P20):** they fire only when the stock is
  AT/ABOVE `sell_above` (not just the midpoint), so no premature CC pings that
  cap upside below the sell target, AND earnings is not inside the option's
  expiry (A15/P21 — don't alert a CC you'd hold through earnings). Both gates
  apply to spike CCs too. cc_only names (MSTR/OWL) exempt. Dashboard still
  shows midpoint-passing CCs.
- **Strict filters surface rare value.** Zero results is an acceptable, expected
  outcome (especially convexity). Do NOT loosen filters to fill the page.
- **Target zones (A32/P30/P31)** — `compute_in_zone()` answers one question on
  every strategy: *where is the stock vs John's own buy/sell target?* Tiers are
  AT / NEAR / OUT / NO_BUY / NO_TARGET / EXEMPT; the dashboard's
  "🎯 At / Near Target" button keeps AT + NEAR. **There is no IV override and
  none may be re-added** — the old one (IVP ≥ 70) cleared 14 of 24 names and
  made 12 of 12 kept rows out-of-zone. NEAR bands: CSP 5% above buy_under, CC 8%
  below sell_above (both shared with the Price Watch panel — keep them in sync
  or the two surfaces disagree about the same ticker, which is exactly the bug
  EX-27 was reported as). LEAPS uses a growth allowance,
  `buy_under × (1+g)**years`, NOT a flat percentage: a flat ×1.10 excluded 23 of
  23 LEAPS names. `g` per bucket (A 10 / B 15 / C 20 / D 25 %/yr) with
  `LEAPS_GROWTH_OVERRIDE` per ticker.
- **`buy_under = 0` means NO BUY — no CSP, no LEAPS, ever.** Use `is_no_buy()`;
  do NOT test `buy_under > 0`. That test used to mean "no restriction", so a
  NO BUY name got an *unlimited* entry price (PATH printed a CSP for months).
  All three CSP paths are gated (dashboard `csp_engine` loop, Telegram
  `find_best_csp`, post-drop) plus LEAPS. A ticker with NO `SYMBOL_SETTINGS`
  entry at all (BABA/META/OWL) is *unconfigured*, not NO BUY — don't collapse
  the two. **Cheap convexity is EXEMPT** (EX-15: a far-OTM long call has no
  assignment risk); it is never hidden by the filter and only carries a
  `no_buy_name` tag.
- **Classification is conviction, not a filter (P33/A36).** Every name carries a
  hand-set `CLASSIFICATION`: **CORE / TRADING / SPECULATIVE / VERY_SPECULATIVE**.
  It says how willing John is to hold the name through volatility, and it
  *modifies* the recommendation rather than deciding whether one appears. Same
  event, different advice: a sharp rally on CORE is a conservative CC, not a
  sell; on SPECULATIVE it is a CC **plus** a partial-profit prompt. A sharp drop
  on CORE/TRADING may be accumulation; on SPECULATIVE it is a thesis check
  first; on VERY SPECULATIVE it is not an averaging-down trigger at all.
  Changes are manual only — nothing reclassifies automatically, and one earnings
  reaction is never a reason to reclassify.
  **Do NOT add a classification-based CSP suppression.** The draft spec proposed
  "avoid routine CSPs on VERY SPECULATIVE"; John struck it (2026-08-14) because
  NBIS puts are among his most-traded setups. Bucket D's 40% annualized floor
  and the delta band already do that risk work.
- **Volatile names (NBIS, CRDO, CLS) are the whole point** of the move-based
  alerts. They jump 10%+ in a day; those are the moments that matter.
- **CCs on explosive winners cap upside.** NBIS covered-call assignments have
  repeatedly created missed upside. Flag this; don't praise CC premium blindly.

---

## Key modules & scanners

- **CSP / CC engine** (`csp_engine`, `find_best_cc`) — bucket-aware premium
  selling. Buckets A–D with annualized-return minimums 12/18/28/40%.
- **Deep-ITM LEAPS** (`find_best_leaps`) — stock-replacement, delta ≥0.75, three
  bands (Conservative / Sweet spot / More leverage). **BUY THE DIP (P12/EX-8):**
  `leaps_trend_action` returns `BUY_DIP` on a ≥8% 1-day or ≥12% 5-day drop when
  IVP ≤50 — the drop day IS the entry, no "wait for stabilization" (that gate
  was removed 2026-07). BUY_DIP reaches Telegram regardless of routine score.
  Constants: `LEAPS_DIP_1D_PCT` (−8), `LEAPS_DIP_5D_PCT` (−12), `LEAPS_DIP_MAX_IVP` (50).
- **Cheap Convexity LEAPS** (`scan_convexity`) — far-OTM long-dated calls,
  STRICT MODE. Only Grade A/B passers shown, one best row per ticker, near-misses
  discarded. Distinct from deep-ITM LEAPS. Grades A+B → Telegram.
  **Filters tightened 2026-08-12 (A31/P29)** after AAPL printed the only
  convexity row on 22 consecutive scans — same expiry, strike just drifting with
  spot. Root cause: every filter was RELATIVE (cheapness vs strike/spot/spread),
  so the cheapest option won, cheapest means lowest IV, and the scanner kept
  landing on the lowest-vol mega-cap while structurally excluding the
  `CVX_AGGR_TICKERS` hypergrowth names it was built for. The new gate is
  `CVX_COV20_MIN = 1.00` — reject any trade that loses money even if the stock
  compounds at 20%/yr for the option's life (AAPL sat at 0.94-0.99). Five other
  thresholds tightened alongside it. **If you change any `CVX_*` hard minimum,
  re-ladder the matching PREF/EXC constant above it** — `_cvx_grade` awards A on
  the PREF thresholds, so raising a hard min without raising PREF makes every
  passer Grade A and *increases* Telegram volume (A and B both send).
- **Spike CC** (`find_spike_cc`) — sell calls into an 8%+ up-spike on ANY held
  100+ share position. Overrides `spreads_only` (a CC on owned shares isn't
  naked). Goes to Telegram — but through the SAME CC gates as routine CCs
  (A15/P21): at/above `sell_above` AND earnings not inside the option expiry.
- **Post-Drop CSP** — sell puts into a drop. Over-gated historically; TRADING
  tier excluded. Known limited.
- **Position management engine** (`position_management_engine`) — per-position
  exit actions. See "Position exit alerts" below.

---

## Position exit alerts (recent, important)

The engine returns ONE action per position, priority order:

1. **BIG MOVE** (priority 0, event-driven) — the main one. Fires when a big
   FAVORABLE move happens on a name you hold a short option:
   - CC + stock drops ≥5% in a day (or ≥ `BIGMOVE_3D`, off by default)
   - CSP + stock rises ≥5% in a day
   - **No profit floor, no strike gate.** The move alone triggers it. Profit %,
     strike distance, cost-to-close are shown as CONTEXT, not gates. This
     replaced two earlier gated alerts (CLOSE NOW + ESCAPE ASSIGNMENT).
   - The reason line STACKS context (P15 in TRADING_PRINCIPLES.md): P&L swing
     since last scan, earnings proximity (⚠ if ≤7d, flags inside-expiry),
     take-profit level reached. Confluence in one message, not a priority pick.
   - Editable constants: `BIGMOVE_1D` (0.05 — was 0.10; a +9.57% CLS
     exit-window day was missed at 0.10), `BIGMOVE_3D` (0.99 = off).
   - **Telegram gate (P17):** BIG MOVE / P&L SWING reach Telegram only under
     decision pressure — P&L SWING itself, earnings ≤7d inside expiry, ≤15%
     from strike, or credible profit ≥60% (`TG_POS_MIN_PROFIT`,
     `TG_POS_NEAR_STRIKE`) — and once per position per day (dedup key
     `tg_position_alerts` in results.json). Dashboard always shows ALL
     actions; the gate is Telegram-only. Calibrated on EX-6 (PATH wanted,
     NBIS 52%/36%-OTM noise).
2. **P&L SWING** — the position itself recovered hard since the last scan
   (≥30 points of premium recovered, or flipped from ≤−15% loss to ≥breakeven)
   even when today's underlying move is under 5%. Catches "hugely negative
   yesterday → positive today" across scans. Uses the previous `results.json`
   (committed each run) as P&L history; only fires on credible (chain) marks.
   Goes to Telegram with BIG MOVE. Constants: `PNLSWING_MIN_IMPROVE` (30),
   `PNLSWING_FLIP_FROM` (−15), `PNLSWING_FLIP_TO` (0).
3. **TAKE PROFIT** — profit ≥ 80% (speculative/trading) or 90% (core/growth).
4. **EARNINGS WARNING** — earnings inside the danger window.
5. **HOLD** — default.

### Mark credibility check (critical — prevents false P&L)
Broker position marks can be STALE on a fast intraday move (e.g. a deep-OTM
call still marked at its pre-drop price). The engine guards against this:
- Option mark is sourced **live chain NBBO first**, position-feed mark only as
  fallback (`mark_src` tracks the source: `chain` / `chain_near` / `position_mv`
  / `none` / `incredible`).
- **Sanity check (position-feed marks ONLY):** if a short option is >20% OTM
  but a `position_mv`/`none` mark implies <60% profit, treat as stale →
  `mark_src = "incredible"`. The alert then says "check the live option price"
  instead of printing a false P&L. Dashboard shows "⚠ price stale — check live".
- **Live chain NBBO is trusted as-is** — on extreme-vol names (NBIS/CRDO/CLS)
  a put 30%+ OTM legitimately holds real value (a 16%/day stock keeps deep-OTM
  premium bid). The guard used to fire on those and hide REAL P&L — that was
  the bug fixed 2026-07 (P2 in TRADING_PRINCIPLES.md).
- If you touch mark logic, preserve both halves. A confident-but-wrong P&L is
  worse than no number; a hidden real P&L is nearly as bad.

---

## Configuration

- **`CLASSIFICATION`** dict in `whale_scanner.py` (just below `SYMBOL_SETTINGS`)
  — ticker → CORE / TRADING / SPECULATIVE / VERY_SPECULATIVE. **The single
  source of truth for conviction tier.** `tier_of()` / `classification_of()` /
  `is_core()` / `is_very_speculative()` / `needs_thesis_check()` read it;
  `CORE_STOCKS`, `TRADING_STOCKS`, `SPECULATIVE_STOCKS`,
  `VERY_SPECULATIVE_STOCKS`, `SPECULATIVE` and `ALL_TICKERS` are all *derived*
  from it — never hand-edit those. Display tiers are `Core` / `Trading` /
  `Speculative` / `Very Speculative`; an unconfigured ticker resolves to
  `Very Speculative` (weight 0, the same treatment the old code gave unknowns).
- **`SYMBOL_SETTINGS`** dict in `whale_scanner.py` (~line 448) — per-ticker
  buy_under / sell_above / delta ranges / flags, grouped by classification.
  All 29 tickers were reconciled against John's own Price Alert table on
  2026-08-14 (A35) and **every name now has a real buy target — none is 0.**
  `buy_under = 0` still means NO BUY and the `is_no_buy()` gate still works;
  it simply has no members today. Do not infer intent from a 0 you find in
  future: the five that carried one (AAPL/NFLX/IBIT/PATH/MSTR) turned out to be
  stale config, not a decision, and an earlier note in this file rationalized
  them as deliberate when they were not. Ask John.
- **`buckets.csv`** — ticker → bucket (A–D) + behavioural flags. **Orthogonal to
  classification:** bucket is about VOLATILITY (premium floors, delta bands,
  DTE), classification is about CONVICTION, and they deliberately disagree in
  places (SPCX is bucket D but TRADING). The `special` column carries only
  WATCHLIST / EXIT_CC_ONLY — it replaced `tier_legacy`, which held a stale
  second copy of the classification. Do not re-add a classification column here.
  Must sit in the same directory as `whale_scanner.py` and `bucket_config.py`.
- **`bucket_config.py`** — bucket loader + `strategy_allowed()`. Must be
  co-located for `load_buckets()` to import. **`strategy_allowed()` and
  `is_leaps_allowed()` are NOT wired into `whale_scanner.py`** — the scanner
  imports only `load_buckets`, `get_bucket`, `get_min_annualized_csp/cc` and the
  `is_*_only` flags, and gates everything else inline. `strategy_allowed()`
  calls itself the "master gate" but is advisory: its LEAPS ban was inert, and
  across 21 archived scans twelve supposedly-banned tickers produced LEAPS rows
  anyway. Audit every rule in it before wiring it in — doing so blind would
  change live behaviour for half the watchlist.
- **Special flags:** `spreads_only` (NBIS, CRDO — block naked CSP/CC, route to
  spreads), `leaps_only` (BABA), `cc_only` (MSTR, OWL — exit-waiting), and
  `watchlist` tier (META).
- **LEAPS are never banned by volatility class (2026-08-14, John's
  instruction).** `leaps_allowed` is TRUE for every bucket and every row.
  Buckets C and D used to default it FALSE, which read as "no LEAPS on volatile
  names" and covered 16 of 29 tickers — including NFLX (CORE) and TSLA, the name
  John bought LEAPS on into a −14% dip (EX-8). Volatility may justify STRICTER
  LEAPS criteria; it must not remove the strategy. The flag remains as a manual
  per-ticker escape hatch with no members. Do not re-default it to FALSE.
- **Feature flags:** `ENABLE_PIO = False` (Position Income Optimization, noisy),
  `STRICT_ZONE_TELEGRAM = False`.
- **Alert-volume levers (A37/P34)** — the dials to turn if Telegram drifts:
  `CC_TELEGRAM_TOL_BY_TIER` (how far below `sell_above` a CC may ping, by
  classification: Core 0%, Trading 2%, Speculative/Very Speculative 3%) and
  `TELEGRAM_MIN_SCORE_PCT` (0.75, global). Current measured volume: ~4.2 trade
  ideas and ~12.4 total notifications per day (up from 0.8 and 7.6). Re-measure
  by replaying, don't estimate — and re-run the replay after ANY change to
  `sell_above`, since that value is what the CC gate compares against.
- **Zone constants:** `CSP_NEAR_PCT` (0.05), `CC_NEAR_PCT` (0.08),
  `LEAPS_GROWTH_BY_BUCKET`, `LEAPS_GROWTH_DEFAULT`, `LEAPS_GROWTH_OVERRIDE`.
- **Editable alert thresholds** (top of file): `BIGMOVE_1D`, `BIGMOVE_3D`,
  `PNLSWING_MIN_IMPROVE` / `PNLSWING_FLIP_FROM` / `PNLSWING_FLIP_TO`,
  convexity `CVX_*` constants, `MAX_CC_COVERAGE_PCT`.

### CHANGING A buy_under / sell_above TARGET (John's standing instruction)

When John asks to change a buy-under or sell-above target, **edit exactly one
place and verify the rest derive from it:**

1. **`SYMBOL_SETTINGS` in `whale_scanner.py` — the ONLY place a target number
   is typed.** Change it here and nowhere else.
2. **Verify nothing else hardcodes it.** Everything else must read the value,
   not restate it. As of A34 the readers are: `move_watcher.py` and
   `earnings_watcher.py` (both load targets from `results.json`), and the
   dashboard's positions-CSV export (reads `data.symbol_settings`, published by
   the scanner). Run this after any target change — it must print nothing:
   ```
   grep -rn "buy_under:\s*[0-9]\|buy_under\"\?:\s*[0-9]" ../whale-dashboard/index.html \
     | grep -v "LAST-RESORT fallback"
   ```
3. **Update the NO BUY list below** if a target moves to/from 0, and say so in
   the reply — it changes whether CSP/LEAPS can fire at all for that name.
4. **State the consequence back to John**: where the stock sits versus the new
   target, and whether that flips the name into or out of the zone.

Why this rule exists: the dashboard used to hand-maintain a second copy of every
target. By 2026-08-13 **all 29 tickers disagreed** with the scanner and 6 were
missing — AAPL 200 vs 0, NBIS 90/190 vs 150/280, PLTR 115 vs 85 — and those
wrong numbers were being exported into a CSV. Fixed in A34 by publishing
`symbol_settings` in `results.json`; the literal left in `index.html` is a
fallback for old data and **must never be edited to change a target**.

Never let a target number exist in two files. If a new consumer needs targets,
it reads `results.json`.

---

## KNOWN GOTCHAS (read before editing)

- **`position_actions` has multiple consumers — update ALL of them when you add
  a position TYPE.** The list in `results.json` was originally short options
  only (`CSP` / `CC`). When PR #2 added `LEAPS_CALL` rows, two consumers written
  against the old assumption broke in the same week: the scanner's held-long-call
  Telegram block pushed 10 trim alerts on LEAPS (EX-24), and the Move Watcher
  printed "You hold **short** LEAPSCALL … ⚠ moving toward your strike" — wrong
  side, wrong direction, and the underscore broke Markdown (EX-25). It also fed
  long strikes into the proximity gate, manufacturing false urgency. Consumers
  to review: the Telegram blocks in `whale_scanner.py`, `move_watcher.py`
  (`build_line` + `near_actionable`), and the dashboard's Actions tab
  (separate repo). Filter by type explicitly — never assume.

- **The dashboard carries its own STALE `SYMBOL_SETTINGS` copy** (`index.html`
  ~line 1432, used by the settings-export table). Its numbers have drifted from
  the scanner's — it lists AAPL `buy_under: 200` while the scanner says 0 (NO
  BUY), NBIS 90 vs 150, PLTR 115 vs 85. Nothing in the opportunity pipeline
  reads it, so it is display-only, but it will mislead anyone who checks targets
  there. Not reconciled as of 2026-08-13 (out of scope for A32) — fix by
  sourcing it from `results.json` rather than by hand-editing the copy.

- **Never write a new `if t in CORE_STOCKS … else "Opportunistic"` ladder.**
  There used to be nine copies of that ladder, and every one of them silently
  collapsed VERY_SPECULATIVE into the bottom tier — so NBIS and MSTR were
  scored, sized and profit-taken exactly like CLS or KNX for months. Call
  `tier_of(ticker)`. Any new tier-keyed map must cover all four display tiers.

- **Tune alert gating against replayed history, never by reading the rules
  (P34).** `results.json` is committed every run, so `git rev-list HEAD --
  results.json` is a replayable archive of real opportunities. Replay any gating
  change over it and report the result to John as **notifications per day**,
  which is the only thing he can now judge. The 2026-08-14 replay found the
  system sending 4 opportunity alerts in 5 days with 18 of 21 scans silent —
  the opposite of the "too noisy" assumption everyone was working from. It also
  showed that classification and scoring moved the count by 0.4/day while three
  specific gates decided everything: the CC sell-target rule (killed 166/176
  CCs and 57/57 spike CCs), the LEAPS trend gate (1,192/1,194), and the CSP
  score bar (unreachable, never once fired).
- **Count MESSAGES, not ideas.** One send per contract plus a section header
  turned 4.2 trade ideas/day into 11.2 Telegram messages. Trade alerts now go
  out as one grouped message via `send_telegram_grouped()`; a ticker producing
  both a routine CC and a spike CC in one scan sends only the spike.

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
  `https://127.0.0.1:8182`. Chrome SSL bypass: "Insecure origins treated as
  secure" flag → enter the callback URL. After renewal, run
  `python push_schwab_secrets.py` to auto-push the new
  `SCHWAB_REFRESH_TOKEN`/`SCHWAB_ACCESS_TOKEN` to GitHub Actions secrets
  (no more copy-paste) — requires a `GITHUB_TOKEN` env var holding a
  fine-grained PAT scoped to this repo with "Secrets: Read and write".
- **Earnings dates are the quiet killer.** A MISSING date is more dangerous
  than a wrong one: it resolves to 999 → "SAFE" → the CC gates and EARNINGS
  WARNING pass everything through with no visible symptom. Check the
  `📅 Earnings calendar:` line in the scan log; if it says 0 tickers cached,
  every earnings gate is off. `EARNINGS_OVERRIDE` remains the manual escape
  hatch and is consulted before any feed.
- **IVP ≠ IV Rank.** The scanner only has IVP (percentile), computed as
  `100 * (1 - exp(-atm_iv / 0.25))`. Never use "IV Rank" language. IVP can be
  stale on weekends.
- **Scan cadence limitation** — full scans run 3×/weekday; the Move Watcher
  (15-min, price-only) now covers spike/drop DETECTION between scans, but
  P&L, chains, and trade candidates still refresh only at full scans. GitHub
  cron has jitter — worst-case detection is ~20-30 min after a move.

---

## WORKFLOW CONVENTIONS (how John wants work done)

- **Read `TRADING_PRINCIPLES.md` BEFORE adding or changing any alert.** It is
  not background reading — it is the spec for what may and may not notify John,
  and it records decisions that are expensive to re-learn. A worked failure:
  P16 ("LEAPS are long-term holds — no trimming logic") was written 2026-07-23
  with the explicit line *"Encoded here so nobody 'improves' LEAPS with exit
  alerts"*, and a later session shipped exactly that, sending 10 LEAPS trim
  pushes in one batch (EX-24). Check the P-list before building; append the new
  example/principle after. Especially relevant: P16 (LEAPS exempt), P17/P22
  (Telegram = decision pressure only, not a browse list), P24 (earnings warns
  convexity, gates CC/CSP), P27 (CSP must respect buy_under), P28 (assignment
  odds drive urgency).

- **One more alert is a cost, not a feature.** John's standing complaint is
  volume: he will stop reading Telegram if it fills with things he can't act
  on. Default to dashboard-only; promote to Telegram only when there is a
  decision to make. Prefer one grouped message per ticker over one per contract.

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

- **Push straight to `main` in BOTH repos.** John's standing instruction
  (2026-08-01): finished work goes to `main` directly — no feature branch, no
  pull request, no merge step for him. Some Claude Code sessions start pinned
  to a `claude/...` branch; when that happens, still land the work on `main`
  (fast-forward merge, then push) rather than leaving it on the branch. The
  dashboard serves GitHub Pages from `main`, so work parked on a branch is
  simply not deployed.
- Push `whale_scanner.py` (+ `bucket_config.py`, `buckets.csv`) to
  `eastbiz/whale-intelligence`.
- Push `index.html` to `eastbiz/whale-dashboard`.
- The dashboard shows nothing new until the next scan writes fresh
  `results.json`.

---

## Open items / backlog

- Classification-driven alerts still to build: partial profit-taking on SHARE
  positions and the earnings-exposure sizing line (C16 / C17 in
  `TRADING_PRINCIPLES.md`). C16 needs a new `position_actions` row type — the
  structure that broke twice with `LEAPS_CALL` (EX-24/EX-25), so it needs all
  four consumers updated together.
- `BABA`, `META` and `OWL` are configured in `buckets.csv` but are NOT in
  `CLASSIFICATION`, so they are not in `ALL_TICKERS` and have never been
  scanned. Their `leaps_only` / `watchlist` / `cc_only` handling is live code
  with no live ticker behind it. Either classify them or drop the rows.
- Spread scanner for CRDO/NBIS on normal (non-spike) days — built standalone
  (`spread_scanner.py`), never integrated.
- PATH / cheap-stock spike-CC filters too strict (premium floor, liquidity).
- Trade journaling + performance analysis (deferred; see the separate
  "Trading Performance Review" handoff John maintains for the analysis spec —
  benchmarks vs SPY/QQQ, CSP/CC efficiency, DTE-bucket comparison).
