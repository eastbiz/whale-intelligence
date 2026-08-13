# Trading Principles & Trade Examples — Living Log

**Purpose.** A running record of how John actually trades, captured from real
trade examples. We collect examples here first, distill them into principles,
and only change the scanner once a pattern is clear and confirmed. This is the
source doc that future system changes should trace back to.

**How we use it.**
- John shares real trades (entries, exits, "I would have closed here" moments).
- Claude asks follow-ups or challenges anything that doesn't make sense from a
  trader viewpoint — before writing it down as a principle.
- Principles accumulate. When enough examples support a change, it graduates
  from *Candidate* → *Actioned* with a code reference.
- Nothing here is auto-implemented. Changes to risk/alert logic still get an
  explicit go-ahead (per CLAUDE.md).

---

## Principles (distilled from examples)

Each principle links to the example(s) that support it and its system status.

### P1 — On volatile names, a big favorable swing is an EXIT signal, not just a hold
When a name I'm short moves hard in my favor (CSP: stock jumps; CC: stock drops),
the swing can pull the option back toward — or deep into — profit in a single day.
If I don't specifically want the assignment, that spike is a **window to close
before it reverses**. Volatility that made the position scary is the same
volatility that hands me the exit.
- Evidence: EX-1 (NBIS $180 put swung from deep underwater to ~breakeven on a
  +16% day), EX-2 (NBIS $140 put at +36% after one +16% day).
- System status: **Actioned** — BIG MOVE (A6) + P&L SWING (A6) + real P&L on
  volatile names (A5). Validated end-to-end by EX-16 (CRDO 46% loss → 10%
  profit alert → closed at $10, +$900 realized).

### P2 — Deep-OTM options on extreme-vol names retain REAL value
A put 30%+ OTM on NBIS is still worth ~$10 because the stock can move 16% in a
day. "Deep OTM = nearly worthless" is false for high-IVP names. Any P&L / mark
logic must not assume deep-OTM means cheap on these names.
- Evidence: EX-2 (NBIS $140 put, 34% OTM, still marked ~$10.27, legitimately).
- System status: **Candidate C1** — mark-credibility guard violates this.

### P3 — Volatile names are the best CSP/CC opportunities, but targets must respect the zone
The volatility is the point — it produces rich premium. But I must not write
strikes too close to the current market price, and the effective entry/exit must
stay inside my buy-under / sell-above band.
- Evidence: earlier discussion (CLS/POWL/CRDO/NBIS unblocking); general rule.
- System status: **Actioned** — see A1, A2.

### P4 — CSP entry workflow
Trigger: I see a 5%+ drop. Then I check the 5-day, and often 30-day / 6-month
trend. If I have good confidence the stock has dropped, it's **below my buy
price**, it's a stock I like, and IVP is elevated/high → I strongly consider
writing a CSP.
- Evidence: stated workflow (2026-07-21).
- System status: aligns with CSP engine direction; at-lows hard-skip removed (A3).

### P5 — CC entry workflow
Trigger: I see the stock rise. I check IVP and my sell-above target.
- If I **don't** particularly want to sell → look for rich premium at **low
  delta 0.20–0.25**.
- If I **do** want to sell → accept **delta 0.25–0.30**.
- Evidence: stated workflow (2026-07-21).
- System status: not yet mapped to CC engine deltas — Candidate C3.

### P6 — Close before DTE, timed to the best swing — even at a loss sometimes
Some positions I close before expiry, timing the close to the most favorable
price swing for profitability. Even closing at a **loss** can be worth it when a
swing has substantially reduced the loss (vs. risking it widening again).
- Evidence: stated workflow (2026-07-21); EX-1/EX-3 (closed the $180 put at
  ~breakeven on the spike — a completed example, was −35% the day before).
- System status: **Candidate C2** — swing-aware close framing / loss-reduction
  exit prompts. Needs more examples to define thresholds.

### P7 — React to huge moves; don't wait for stabilization
In the current market, waiting for prices to stabilize is not the ideal
strategy. Big moves ARE the opportunities — react to them. What separates a
real opportunity from noise: how substantial the price move is, how high IVP
is, the stock's own history of price movements, and **distance from my
buy-below / sell-above targets** (that's why I maintain those targets — they're
the anchor the system should measure moves against).
- Evidence: stated 2026-07-21 (rejecting "wait for day-3 stabilization"
  framing); EX-2 ($140 put written into a −19% day, +37% one day later).
- System status: consistent with A3 (at-lows hard-skip removed). Open question:
  whether WAIT labels should downgrade high-IVP big-drop setups at all —
  collect more examples (Candidate C5).

### P8 — The spike-close → re-write-lower cycle (rolling with the swings)
On volatile names the position is not one trade, it's a cycle: write the CSP,
and if a big favorable spike pulls it back to breakeven/profit — close it if I
don't specifically want assignment at that strike. Then when the stock drops
again (likely, at this volatility), write a NEW put at a LOWER strike. Each
swing ratchets the strike down and harvests premium twice. Strike choice is
always "how much do I want to own at this price" — high vol argues for lower
strikes, not no trade.
- Evidence: EX-3 (closed $180 put on +17% day, already holding the $140 written
  the prior day; expects to write below $180 on the next drop — logged as a
  prediction to check).
- System status: not built. The scanner treats entries and exits as unrelated
  events. Candidate C6: after a spike-close on a name, watch for the next drop
  and surface the lower-strike re-entry.

### P9 — Carry earnings risk only at strikes I'd happily own through the event
Confirmed 2026-07-21 (EX-3 follow-up): the $180 close wasn't about the $180
put's own earnings exposure (it expired Jul 31, before Aug 6 earnings) — it was
about locking the recovery at a strike I didn't love, while **deliberately
keeping** the Aug 21 $140/$150 puts that DO sit through earnings, because at
30%+ lower strikes I'm comfortable owning through any outcome. Earnings risk
is acceptable — but only at strikes where assignment is welcome.
- Evidence: EX-3 (closed $180/Jul31; kept $140+$150/Aug21 through 8/6 earnings).
- System status: not encoded. The scanner's earnings logic is a blanket
  "warn/skip near earnings" — it doesn't distinguish by strike depth.

### P10 — The daily funnel: big movers → trend context → IVP → trades; quiet day = no trades
How a trading day actually starts: check the brokerage for **daily moves ≥5%**
(ideally more on the volatile names). Only those names get attention for
CSPs/CCs. Then check **5-day and 30-day** movement for context. Then **IVP —
50%+ preferred**. Only then look at actual chains. The same big-mover list
drives the review of open positions for closes. **If the market isn't moving,
I don't trade at all** — and I only want to be notified on days when the
opportunity conditions are actually there.
- Evidence: stated workflow 2026-07-21.
- System status: partially aligned (BIG MOVE for positions). The scanner scans
  everything every run and the dashboard always fills; there's no "today is
  (not) a trading day" gate on notifications. See C8.
- Tension RESOLVED (2026-07-21): **IVP is a quick reference, not a gate — if
  the price is right, price overrides IVP.** Any future encoding of P10 must
  treat IVP as a soft ranking input, never a hard filter.

### P11 — Direction disqualifiers: don't sell INTO the move that just paid
On a big up-day, no CSP on that name (premium is momentarily poor + reversal
risk); a deeply negative 30-day trend disqualifies the CC side. Worked example
(2026-07-21): NBIS +7.5% today but 5d −25% → **disqualifies BOTH**: no CSP
(just rose 7.5% today), no CC (30d −25%).
- Evidence: stated workflow with NBIS example, 2026-07-21.
- System status: largely built — csp_engine's rebound suppression (skip CSP on
  ≥7% up-day, downgrade at ≥5%) covers the CSP side; zone-first CC gating
  covers the CC side. Keep both when refactoring.
- **Gap (EX-7):** the CSP disqualifier is 1-DAY only. A multi-day RALLY
  (NBIS +31%/5d) is not caught and even gets promoted to BUY. Extension in
  P19.

### P19 — Measure moves in units of the stock's own normal movement (IV-scaled), soft not hard
The core design principle for "is this a good day to trade this name." Do NOT
hard-code percentage thresholds — they can't fit both a 90%-IV NBIS and a
25%-IV MSFT. Instead: options price a "normal" move for each name right now
(≈ IV × √(days/252)). Judge the recent move as a MULTIPLE of that normal.
- NBIS ~90% IV → normal 5d ≈ ±13%; a +31% run is ~2.4× normal → wrong side.
- MSFT ~25% IV → normal 5d ≈ ±3.5%; a +8% run is ~2.3× normal → same rule
  catches it, with no MSFT-specific number.
- One relative rule ("recent up-move ≳ 1.2× normal → not a CSP day"),
  self-adjusting per name and per regime; nothing to re-tune as vol changes.
Two firm sub-decisions (2026-07-22):
  (a) **Soft, never hard.** A post-rally setup is downgraded to WAIT with a
      plain flag ("+31%/5d ≈ 2.4× normal — wrong side of the swing"), never
      SKIP-hidden. A wrong WAIT costs nothing (John sees it, overrides); a
      wrong SKIP hides a deal (the CRDO/NBIS bug we spent a week undoing).
      Timing = a visible judgment; strict filters stay only for premium
      QUALITY, not deletion.
  (b) **Calibrate by John's verdicts, not invented boundaries.** John reviews
      real cards and says "I'd take this / not today because…"; each is logged
      as an example; any proposed rule must reproduce ALL logged verdicts
      before shipping (same method that produced the 9/9-validated P17 gate).
      Rules become regression-tested descriptions of John's judgment.
- Evidence: EX-7 (NBIS certain no-CSP at +31%/5d); generalizes P7 ("judge by
  the stock's own history of price movements") and P11 (1-day → multi-day).
- System status: **Candidate C10** — agreed direction, NOT yet built. Needs a
  handful more "good day / bad day" verdicts across DIFFERENT-vol names to
  confirm the ~1.2× multiple and the WAIT vs SKIP line before coding.

### P12 — LEAPS: low IVP + recent price drop (buy the dip, don't wait for the bounce)
LEAPS candidates come from the opposite screen as premium selling: stocks with
LOW IVP that dropped recently — buy cheap optionality on quality names after
the fall, sell expensive premium elsewhere. **And the drop day itself is the
entry — P7 applies to LEAPS too.** A big drop on cheap IV is a buy signal NOW;
I take the risk if it drops again tomorrow. Do NOT wait for the falling knife
to stabilize (that was the old LEAPS-engine philosophy, explicitly removed).
- Evidence: stated workflow 2026-07-21; EX-8 (TSLA −14%, IVP 28 → bought $180
  + $240 LEAPS on the drop, 2026-07-23); sheet shows 99 LEAP entries.
- System status: **Actioned — A11.** `leaps_trend_action` now returns a
  BUY_DIP action (≥8% 1-day or ≥12% 5-day drop AND IVP ≤50), checked before
  the old "wait for stabilization" states; it reaches Telegram regardless of
  routine score (event-driven, like spike/drop alerts). Low-IVP gate keeps the
  LEAP cheap. Constants `LEAPS_DIP_1D_PCT` / `LEAPS_DIP_5D_PCT` /
  `LEAPS_DIP_MAX_IVP` at top of file.

- **A12 — Move-triggered full scan.** The 15-min Move Watcher now dispatches a
  FULL scan (not just a price ping) when a name moves ≥8% on the day, so the
  actual trade candidate — LEAPS BUY_DIP, refreshed P&L, CSP/CC cards — lands
  within ~15 min instead of waiting for the next 3×/day slot. Closes the gap
  John flagged: awareness was fast (A7) but the candidate still waited.
  IBKR-budget guards: one trigger per ticker/direction/day, hard daily cap (3),
  skip if a scan ran within 25 min. Validated 7/7 (dispatch, dedup, cap,
  fresh-skip, failure-safe). Built 2026-07-23.
- **A13 — Move Watcher proximity gate.** The 15-min ping now fires only when a
  ≥5% move lands NEAR something actionable — within 10% of buy_under /
  sell_above, or 12% of a held short strike — instead of on every ≥5% mover.
  Calibrated on EX-9 (John: MU −6.7% at 105% above his buy target = noise;
  CRDO −8.8% at 7.8% above buy + on his $200 strike = the one that mattered).
  Validated on the real screenshot: 5 movers → 2 pings (CRDO, POWL kept; MU,
  CLS, NBIS silenced). The ≥8% full-scan trigger is unchanged, so big movers
  still refresh candidates. Constants `MOVE_NEAR_TARGET_PCT` (10),
  `MOVE_NEAR_STRIKE_PCT` (12). Built 2026-07-23.
- **A14 — CC Telegram gate: at/above sell target.** A covered-call alert now
  reaches Telegram only when the underlying is ≥ the ticker's sell_above (was:
  zone-first midpoint). Stops premature CC alerts that cap upside below the
  sell target (NVDA at $209.69 vs $225 — EX-10). cc_only exit-waiting names
  (MSTR/OWL) exempt. Dashboard unchanged. Validated 8/8. Built 2026-07-24.
- **A15 — CC gates extended to the spike-CC path + earnings-inside-expiry.**
  `_cc_telegram_ok` (shared by regular CC and spike CC) now suppresses a CC
  ping when the stock is below sell_above OR earnings falls on/before the
  option expiry. Closes the spike-CC bypass of A14 and adds the earnings rule
  (P21). cc_only exempt; only fires on known earnings dates. Validated 8/8.
  Built 2026-07-24.
- **A16 — "GO SIGNALS" replace "NOTABLE MOVES" (P22).** The scan's move block
  now fires only when a move lands the stock IN an actionable zone (below buy /
  at-or-above sell); removed "consider CSP/LEAPS/CC" and "X% away" nudges.
  Clear GO language + earnings tag. Built 2026-07-24.
- **A17 — CC earnings buffer (P23).** CC gate extended from "earnings inside
  expiry" to "earnings within DTE + 5 days" — a CC must clear earnings by a
  buffer. Plus `_earnings_tag` reminder line on CSP/CC/spike Telegram alerts.
  Validated 4/4. Built 2026-07-24.
- **A18 — Notable-move redefine: net-5d + bucket + target-OR (C11).** Replaces
  A16's too-strict IN_ZONE gate. A move is notable when the NET 5-day move
  clears a BUCKET-scaled bar (A −8 / B −10 / C −13 / D −18%, stable, not the
  hand-maintained targets) OR the price is actually in the buy/sell target
  zone. Triggering on 5d (not 1d) cancels round-trips (a −7% day undoing a +7%
  day nets ~0). 1-day % shown for urgency only; target shown as a clear GO
  only when in-zone (never "consider"/"X% away"); earnings tag included.
  Removed the stale SPECULATIVE_TICKERS drop-skip (bucket thresholds now do
  that job correctly). `NOTABLE_5D_BY_BUCKET` constant. Validated 10/10
  incl. John's round-trip case. Built 2026-07-24.
- **A19 — Earnings warning on convexity alerts (P24).** `_convex_earnings_note`
  adds "⚠️ Earnings TODAY — you'd pay pre-earnings IV; consider waiting a day"
  (0d), a similar note with the date (1-3d), or a plain "📅 Earnings M/D (Nd)"
  (4-21d) to CHEAP CONVEXITY Telegram alerts. Deliberately a warning, NOT a
  gate — earnings doesn't threaten an 800+ DTE far-OTM thesis, it only affects
  entry price. Validated across all branches. Built 2026-07-28.
- **A20 — Telegram CC used an IBKR-only share count (EX-17).** The CC block fed
  `find_best_cc` from `stk_hold` (IBKR positions only), so Schwab-held stock
  (the AMZN IRA position) read as 0 shares and never produced a Telegram CC
  even when the dashboard showed a perfect-score one. Now uses the same
  `position_check` qty/avg the dashboard uses, `stk_hold` as fallback.
  Built 2026-07-31.
- **A21 — past earnings dates no longer read as "earnings today" (C12).**
  `days_to_earnings` was `max(0, delta)`, clamping a past date to 0 so the A17
  CC gate suppressed post-earnings CCs — the single best CC window. Now a past
  date yields 999 (no upcoming earnings); delta == 0 still suppresses. C12
  graduated. Built 2026-07-31.
- **A22 — CC DTE floor of 30, window 30-50 (P25).** `CC_DTE_MIN/MAX` 30/45 →
  30/60 (sweet spot 35-50); the inline dashboard CC scanner was independently
  using 20-60 (the source of John's 20-DTE AMZN card) and now uses the shared
  constants; spike CC `OPP_CC_DTE_MIN/MAX` 14-30 → 30-60. All CC paths aligned. Built 2026-07-31.
- **A23 — honest IV labels + real-IV-percentile groundwork (P26).** All cards
  and Telegram alerts now show `ATM IV x%` via `atm_iv_from_ivp()` instead of
  the fake "IVP". Each scan banks that ticker's ATM IV to `iv_history.json`
  (committed by the workflow, one sample/ticker/day, ~1.5yr retained). Once a
  ticker has ≥60 samples, `real_iv_percentile()` activates automatically and
  labels become "IV 22% (IVP 33% of 1yr)" — no further change needed. Scoring
  still consumes the legacy `ivp` field unchanged (deliberate: relabel first,
  re-tune scoring only after real percentiles exist). Built 2026-07-31.
- **A32 — Target-zone filter rebuilt; NO BUY enforced at source (P30/P31/EX-27).**
  `compute_in_zone()` now returns `(in_zone, tier, reason)` with tiers
  AT / NEAR / OUT / NO_BUY / NO_TARGET / EXEMPT, and the IV override is gone.
  Bands: CSP at ≤ buy_under, near within `CSP_NEAR_PCT` (5%); CC at ≥ sell_above,
  near within `CC_NEAR_PCT` (8%) — both reused from the Price Watch panel so the
  two surfaces agree; LEAPS at ≤ buy_under, near inside
  `buy_under × (1+g)**years` with `g` from `LEAPS_GROWTH_BY_BUCKET`
  (A .10 / B .15 / C .20 / D .25) plus `LEAPS_GROWTH_OVERRIDE` per ticker.
  New `is_no_buy()` blocks CSP (all three paths: dashboard `csp_engine` loop,
  Telegram `find_best_csp`, and post-drop) and LEAPS on `buy_under = 0` — and
  deliberately treats a ticker with NO `SYMBOL_SETTINGS` entry (BABA/META/OWL)
  as unconfigured, NOT as NO BUY. Convexity uses the `CONVEXITY` strategy: never
  hidden by the filter (EX-15), tagged `no_buy_name` when applicable; its
  `STRICT_ZONE_TELEGRAM` gate was removed as it contradicted EX-15 whenever the
  flag was flipped. LEAPS rows now carry `implied_growth_pct` /
  `growth_allowed_pct`, shown on the card as "Assumes/yr". Dashboard button
  renamed "🎯 At / Near Target" with AT/NEAR/NO BUY/Off-target badges.
  Verified against the 2026-08-13 scan: filter goes from 12 rows (all out of
  zone) to 27 rows across 17 ticker+mode combos (all genuinely at/near), and 11
  NO BUY rows disappear at source (PATH CSP; AAPL/MSTR/NFLX/PATH LEAPS).
  Telegram volume unchanged — `STRICT_ZONE_TELEGRAM` is still False. Built 2026-08-13.
- **A31 — Cheap Convexity hard filters tightened (P29/EX-26).** Six thresholds
  raised plus one new gate, all in the `CVX_*` block — no new plumbing, no
  behavior changes outside `_cvx_hard_filters`:
  `CVX_COV20_MIN = 1.00` (NEW — the honesty gate: reject anything that loses
  money at a 20%/yr growth scenario), `CVX_CAGR_MAX` 0.25→0.18,
  `CVX_SCORE_MIN` 20→30, `CVX_PREM_MAX` 0.12→0.05, `CVX_COV30_MIN` 1.05→1.20,
  `CVX_BURDEN_MAX` 0.05→0.025. The PREF/EXC tiers were re-laddered above the
  new hard minimums (CAGR_PREF 0.15, SCORE_PREF/EXC 40/50, PREM_PREF/EXC
  0.04/0.03, COV30_PREF 1.25) — without that, every passer would have graded A
  and Telegram volume would have gone UP, since `fmt_convex` sends A and B alike.
  Validated by replaying all 28 historical convexity rows: 26 dropped, 2 kept
  (the PYPL $85 Grade-A rows, now graded B under the stricter ladder). Each of
  the six gates independently rejects all 22 AAPL rows and independently keeps
  both PYPL Grade-A rows, so AAPL must improve on every dimension at once to
  reappear — i.e. only if it becomes a genuinely good trade. Built 2026-08-12.
- **A30 — Move Watcher no longer mislabels owned LEAPS (EX-25).**
  `build_line()` skips non-CSP/CC types instead of printing "You hold short
  LEAPSCALL … ⚠ moving toward your strike" (wrong direction, wrong side, and
  the underscore broke Markdown). `near_actionable()` likewise ignores long-call
  strikes so a long strike near spot can't trigger a false-urgency ping.
  Built 2026-08-07.
- **A29 — LEAPS trim alerts restricted to real decisions (P16/EX-24).**
  Telegram now sends a held-long-call alert ONLY for `SELL TARGET HIT` on a
  DEEP-ITM call (`strike <= spot x LONG_CALL_ITM_MAX_STRIKE_PCT`, 0.90), and
  groups them one message per ticker. "NEAR 52W HIGH" and "EXPIRING SOON"
  remain on the dashboard but no longer push. Far-OTM convexity LEAPS are
  excluded entirely. Verified against the real 2026-08-07 batch: 10 pushes → 1.
  Built 2026-08-07.
- **A28 — assignment odds on position alerts (P28/EX-23).** Captures |delta|
  from the position's chain contract (both CSP and CC paths) and surfaces it as
  "Assignment odds ~X%" in the alert reason line — visible on Telegram AND the
  dashboard Actions tab. Estimated via `estimate_delta()` and marked "(est)"
  when no chain contract is available. Display only; no gating changed.
  Built 2026-08-07.
- **A27 — scan timeout raised + phase instrumentation + calendar cache
  persisted (EX-22).** `timeout-minutes` 15 → 25 in scanner.yml; `_phase()`
  prints elapsed time at start / IBKR done / market data done / per-ticker
  chain scan / dashboard pass / results write, so a future hang is located
  from the last line printed; scanner.yml now commits `earnings_calendar.json`
  (previously only earnings-watcher.yml did, so a scanner-first-of-day run
  redid the whole Nasdaq sweep and discarded it). Built 2026-08-06.
- **A26 — NOTABLE MOVES tightened + capped (EX-21).** Bucket 5-day bars raised
  A8/B10/C13/D18 → A12/B15/C20/D28; hard cap `NOTABLE_MAX_LINES` = 5 (IN_ZONE
  target-triggered rows always kept, remainder = largest moves, "+N smaller
  moves" footer); deduped against the CSP/CC opportunity sections above.
  14 → 5 lines on the Aug 5 message. Built 2026-08-05.
- **A24 — buy_under gate on the Telegram CSP path (P27/EX-20).**
  `find_best_csp()` had no buy_under awareness, so Telegram recommended CSPs
  with assignment prices above John's target (TSM $358 effective vs $320
  target). Now enforces `strike − premium ≤ buy_under × 1.03`, matching
  csp_engine exactly so the two pipelines agree. Built 2026-08-03.
- **A25 — alert display fixes.** The 999 "earnings unknown" sentinel no longer
  prints as "Earnings in 999d — safe" (now "Earnings date unknown — verify
  before trading" — honest, and doesn't claim safety it can't verify);
  "Core Core tier" duplication removed from CSP/LEAPS alerts. Built 2026-08-03.

### P13 — Past trades on the same name are entry context (the "personal premium book")
When repeating an action (CSP/CC on a name I've traded before), I look at my
history: what premium did I get last time, at what delta, at what stock price.
It doesn't produce hard rules, but it's the context for judging whether
today's premium is rich or poor **for this name**. The scanner is isolated at
the moment of scan — it has no memory of what I've been paid before.
- Evidence: stated 2026-07-21; the Options Trades sheet is maintained largely
  for this purpose.
- System status: not built. See C7. First fruits of the analysis in EX-4 —
  e.g., the Jul 20 CRDO $175 CSPs (74–81% ann, IVP 94) are richer than ALL six
  prior CRDO CSP entries (46–64% ann, IVP 27–35): the history would have
  screamed "take this one."

### P14 — Intent to exit overrides IV-richness on the CC side
When I WANT to sell the shares (exit-waiting), I write CCs even at very low
IVP — the premium is a bonus on a sale I want anyway, not the reason for the
trade. IV-richness rules apply to income CCs, not exit CCs.
- Evidence: confirmed 2026-07-21 — the 10 IBIT CCs at median IVP 13 (EX-4)
  were deliberate rule-breaks because "IBIT I want to sell."
- System status: partially encoded — cc_only tickers (MSTR, OWL) already skip
  zone gating. But IBIT is NOT cc_only, so the scanner can't tell John's exit
  CCs from income CCs on regular names. Relates to C3 (posture: income vs
  exit changes acceptable delta AND acceptable IVP).

### P15 — "Good day to close" is a confluence, not a single threshold
The CLS case (EX-5): what made 2026-07-21 a flag-worthy exit day was the
COMBINATION — a big spike day (+9.6%, the puts lost ~half their value in one
session), earnings inside the expiry window (5 days away), a strike close to
the money (10.9% OTM), decent profit available (+34%/+48%), and genuine
directional uncertainty (analysts calling the name overpriced). None of these
alone; together they say "if you want out, today is the day."
- Evidence: EX-5.
- System status: NOT served today. Verified against the real engine: both CLS
  puts fire a generic EARNINGS WARNING with no mention of the spike; BIG MOVE
  misses because +9.57% < the hard 10.0% threshold — and had it been +10.1%,
  BIG MOVE would have fired and SUPPRESSED the earnings context (engine
  returns one action per position). See C2 (extended).
- UPDATE: built same day as A6 — 5% threshold, stacked reason lines, and the
  P&L SWING action all shipped 2026-07-21.

### P17 — Position alerts need decision pressure; the move alone is name-level news
The Move Watcher tells me a NAME moved (once/day) — that's enough awareness.
A POSITION alert on Telegram is only valuable when there's something to
decide: profit high enough to close and be done (~60%+), price near the
strike (~15%), earnings inside the option's window, or a big P&L swing.
A favorable move on a position I'm comfortable holding is noise, even at
+52% profit — and repeating it every scan makes it worse.
- Evidence: EX-6 — PATH CC alert (65% profit) acted on within minutes;
  NBIS $140 (+52%, 36% OTM) and $150 (−30%, 31% OTM) alerts explicitly
  called "not valuable"; NBIS re-alerted every scan for 2 days.
- System status: **Actioned** — A9 (Telegram gate + once-per-position-per-day
  dedup; dashboard unchanged, shows everything).

### P18 — Card text: short, judged against MY targets, exceptional-or-nothing
Opportunity card text should let me see in one glance whether something is
exceptional. "Stock at 79% of band" tells me nothing; "Sale $468.93 ≥ your
sell target $450" tells me everything. Cost-basis lines: remove from all
cards. Generic zone narration: remove. What stays: price vs my buy/sell
target, IVP only when it's a signal (high or warning-low), and the income
line (annualized + $/day).
- Evidence: stated 2026-07-22 (TSM CC example). Related preference, logged
  not yet actioned: Telegram opportunity pings should carry only conviction
  trades — "I would probably consider only the two BUY (RISKY) cards"
  (NBIS/CRDO CSPs at 110-117% annualized); LEAPS to Telegram only when
  genuinely exceptional ("CLS borderline... or none") → Candidate C9.
- System status: **Actioned** (A10) for CC/PIO card text; convexity → 
  Telegram now includes Grade B (A10). C9 pending design.

### P20 — A CC only pings when the stock is AT/ABOVE my sell target
Don't alert me to write a covered call while the stock is below my sell-above
target — writing a CC there caps upside below where I'd actually let the shares
go (the exact NVDA missed-upside trap in CLAUDE.md). The zone-first midpoint
gate is fine for the dashboard, but a Telegram CC ping needs the full sell
target met. Exit-waiting cc_only names (MSTR/OWL) are exempt — they WANT to be
called away.
- Evidence: EX-10 (NVDA CC pinged at $209.69 with a $225 sell target — John:
  "should not fire unless above the sell target").
- System status: **Actioned — A14.** Telegram-only gate; dashboard unchanged.

### P21 — Don't alert a CC I'd have to hold through earnings
Even on a spike, near earnings is a bad time to write a covered call: the
post-earnings pop can take the stock through the strike and I get called away,
capping upside (the same NVDA/AAPL missed-upside trap). So a CC ping is
suppressed when earnings falls inside the option's life (earnings on/before
expiry) — the CC analog of P9. Applies to spike CCs too, not just routine CCs.
cc_only exit-waiting names (MSTR/OWL) exempt — a call-away is the goal there.
- Evidence: EX-11 (AAPL spike CC pinged at $332.67 with earnings days away,
  20 DTE — John: "earnings is a few days away, bad time, even with a spike").
- System status: **Actioned — A15.** Telegram-only; only fires when the
  earnings date is actually known (unfetched date can't be gated — flagged).

### P22 — Telegram = "go sit at the computer," not "check these 7 names"
I already spend too much time checking/trading. A Telegram signal must be a
clear GO: the stock is actually below my buy target (write CSP) or at/above my
sell target (write CC), or a genuinely serious LEAPS dip. NOT "X% away →
consider CSP/LEAPS" nudges across seven names. Zero signals on a quiet day is
the correct, wanted outcome.
- Evidence: EX-12 (NOTABLE MOVES block listing CRDO/MU/TSLA/NOW as "consider,"
  all still 4–9% ABOVE buy — John: "I do not want to check seven stocks").
- System status: **Actioned — A16.** NOTABLE MOVES → "GO SIGNALS" fires only
  on IN_ZONE (below buy on a drop / at-or-above sell on a rise); "consider" and
  "X% away" removed. Serious LEAPS dips still ping via the separate BUY_DIP
  alert (A11).

### P23 — A CC must clear earnings by a buffer, not just avoid holding through it
Extends P21. Even a CC that expires a few days BEFORE earnings is risky — the
premium is pumped by pre-earnings IV and a run-up can call me away right before
the pop. So a CC ping requires the option to expire at least a buffer (5 days)
before earnings — else earnings is "too close." Post-earnings CCs (event
passed) flow freely.
- Evidence: 2026-07-24 discussion ("risky to write CC with DTE very close to
  earnings"); John agreed to the buffer approach, 5 days.
- System status: **Actioned — A17.** `_cc_telegram_ok` suppresses when
  `days_to_earnings ≤ DTE + CC_EARNINGS_BUFFER_DAYS` (5). cc_only exempt.

### P24 — Earnings is a WARNING for convexity, not a gate
Earnings matters very differently by instrument. For a short CSP/CC (20-40 DTE)
it's a real risk — one gap can blow through the strike, force assignment, or cap
upside — so it GATES the alert (A15/A17). For a far-OTM convexity LEAP (800+
DTE) it does NOT threaten the thesis: there are ~10 more earnings before expiry
and no single one decides it. Gap risk is even two-sided for a buyer (a
down-gap = cheaper entry later; an up-gap = the call gains). What earnings
actually costs is ENTRY PRICE — buying the day before means paying elevated
pre-earnings IV on a vega-heavy option. So: warn, never suppress. Suppressing a
rare Grade A/B convexity find over an event that doesn't threaten a 2.4-year
bet would throw away signal.
- Evidence: EX-15 (PYPL convexity alert on its earnings day, 2026-07-28).
- System status: **Actioned — A19.** `_convex_earnings_note` adds a warning
  line to convexity alerts (⚠ for 0-3 days, 📅 date for 4-21 days). No gating.

### P25 — Never write a CC under 30 DTE; sweet spot 35-50
Short-dated CCs aren't worth it: too little premium for the assignment risk and
the constant management. Minimum 30 DTE, range 30-60, with 35-50 the preferred window.
- Evidence: 2026-07-31 — the AMZN CC surfaced at 20 DTE. John: "I only see this
  CC with 20 days DTE. I do not think I should write any CC less than 30 days.
  Sweet spot seems 35-50 days."
- System status: **Actioned — A22.** `CC_DTE_MIN/MAX` = 30/60 (sweet spot 35-50) and the inline
  dashboard CC scanner (which was independently using a 20-60 window — the
  source of the 20-DTE card) now uses the same constants. Spike CCs too
  (`OPP_CC_DTE_MIN/MAX` was 14-30 → 30-60).

### P26 — "IVP" was never a real IV percentile — show honest IV
The `ivp` field is a fixed transform of current ATM IV
(`100*(1-exp(-atm_iv/0.25))`), with NO historical comparison. "IVP 58%" only
ever meant "ATM IV ≈ 22%". That reads as premium-rich when it may be the
opposite, and it misled a live decision. I want a REAL IV percentile (IV today
vs this ticker's own past year) — that's the number I'm used to and trade on.
- Evidence: EX-19 — AMZN card showed "✅ IVP 58%" while John's broker showed a
  true IV percentile of 29%: post-earnings IV crush, thin premium. He was right
  ("premium will not be so exciting"); the scanner label said the opposite.
- System status: **Actioned — A23.** Displays are now honest ("ATM IV 22%"),
  and daily ATM IV is banked per ticker so a TRUE percentile switches on
  automatically at 60 samples/ticker: "IV 22% (IVP 33% of 1yr)".

### P27 — A CSP alert is only valid if assignment respects my buy target
The whole point of a CSP is getting paid to buy at a price I want. If the
effective entry (strike − premium) sits above my buy_under, the trade
contradicts its own purpose — no matter how good the annualized return looks.
Being near 52-week highs makes it worse, not better.
- Evidence: EX-20 — Telegram pushed a TSM $370 put (effective entry $358.45)
  while John's TSM buy target is $320, 12% lower. John: "it's not close to buy
  below. It is actually near the highs, as you comment yourself. Why is this a
  good opportunity to write a CSP?"
- System status: **Actioned — A24.** `find_best_csp` (the Telegram path) now
  enforces the same buy_under rule csp_engine always had.

### P28 — Triage position alerts by assignment odds (delta), not distance
The first question on any position alert is "how likely am I actually to be
assigned / called away?" High odds = urgency, worth analysing. Low odds = don't
waste time, even after a big price move. Worked example: a CSP written low, the
stock gaps 30% on earnings, then adds 5% — that extra 5% is NOT a signal,
because assignment was already off the table.
- **Delta is that number.** |delta| is the market's own probability the option
  finishes ITM, folding distance, volatility and time-remaining into one figure
  — which raw `dist_to_strike` cannot: 15% OTM on NBIS at 30 DTE is ~20% odds,
  the same 15% on MSFT at 10 DTE is ~2%. John already tracks "Prob. ITM" at
  entry in his sheet; the alerts simply never carried it forward.
- Evidence: EX-23 (request). Retro-fits his real calls: NBIS $140 at 36% OTM
  which he dismissed as noise = ~3%; CRDO $200 ITM at expiry which he closed =
  ~88%; CLS $300 into earnings which he closed = ~25%.
- System status: **Actioned — A28** (display, both surfaces). The delta-based
  urgency GATE (replacing P17's crude "within 15% of strike") is proposed but
  NOT built — awaiting John's read on the displayed numbers first.

### P16 — LEAPS are long-term investments, exempt from event-day logic
The deep-ITM LEAPS (e.g. 10× CLS Jan'28 $180) are stock replacement held for
years. Earnings calls don't factor into them — no trimming logic, no P15
confluence application. Event-day exit thinking applies to SHORT premium
positions only.
- Evidence: stated 2026-07-21 in response to Claude's challenge on the CLS
  LEAPS riding through earnings.
- System status: **VIOLATED then corrected.** PR #2 (2026-08-07) added
  `long_call_management_engine`, which pushed 10 Telegram trim prompts on
  John's LEAPS in one batch — exactly the "improvement" this principle warned
  against (EX-24). Resolved by A29: NEAR 52W HIGH is dashboard-only; Telegram
  keeps only the one genuine decision — a DEEP-ITM (stock-replacement) call
  whose underlying reached the sell target — grouped one message per ticker.
- Refinement (2026-08-07): P16 is not "never alert on LEAPS". It is "don't
  prompt trimming on a long-term hold because of short-term price action."
  A deep-ITM LEAP at/above the sell target IS a real decision (it's standing in
  for shares John would sell there). A far-OTM convexity LEAP never is — the
  stock rising is the THESIS, not an exit signal.

### P29 — A convexity trade must not lose money at a plausible growth rate
The Cheap Convexity filters were all RELATIVE — cheapness against the strike,
against spot, against the spread. Nothing asked the absolute question: *is the
growth rate this trade requires one the company can plausibly deliver?* Because
`prem_pct` and `strike/ask` both reward a cheap option, and a cheap long-dated
option means low IV, the filter systematically selected the lowest-vol mega-cap
on the watchlist and called it convexity — while structurally excluding the
hypergrowth names in `CVX_AGGR_TICKERS` (NBIS/PLTR/MSTR/IBIT), whose 2.5-year
far-OTM calls cost far more than the old 12%-of-spot ceiling. That is backwards:
convexity is the right structure precisely where a large move is credible.
The concrete gate: **`cov20 ≥ 1.00`** — the trade must at least break even if
the stock compounds at 20%/yr for the option's life. It was already computed on
every row and simply never checked. AAPL sat at 0.94-0.99 for 22 straight scans:
it needed >21%/yr for 2.3 years just to reach breakeven ($470.60 on a $301
stock, roughly a $7T market cap by Dec 2028).
- Evidence: EX-26 (the AAPL convexity monoculture, Aug 6-12 2026).
- Corollary: a filter whose output does not move with price is not a signal.
  Required CAGR stayed pinned at 20.6-21.8% while AAPL ranged $301-316, because
  the ranker picks the lowest-CAGR passer and the binding constraint was the
  convexity-score floor — so the scanner was solving for its own boundary and
  re-anchoring the strike to spot each scan. Watch for this shape elsewhere:
  if a metric never varies, it is describing the filter, not the market.
- System status: **Actioned — A31.**

### P30 — "In zone" must mean what it says, and a NO BUY name means NO BUY
Two separate failures of the same kind — a filter whose name promised one thing
and whose code did another.

**(a) The IV override ate the zone.** The dashboard's "In Zone Only" toggle
tested price against the target *and then* let anything through on `IVP ≥ 70`
(CSP/CC) or `IVP ≤ 25` (LEAPS). On the 2026-08-13 scan, 14 of 24 priced names
cleared that override and **12 of the 12 rows the filter kept were out of zone —
zero passed on price.** The button showed the exact opposite of its name. Worse,
IVP ≥ 70 selects for volatility, so the override was loudest on PLTR/NBIS/CLS/
CRDO — the names where P3 says the zone matters most. A tolerance band is fine;
a second rule that silently readmits everything the first rule excluded is not.
- John: *"Should it be opportunities below or above my buy/sell targets? That
  would be logical."* Yes — and that is what the label had always claimed.

**(b) `buy_under = 0` meant two opposite things.** `SYMBOL_SETTINGS` and
`compute_in_zone` read it as NO BUY. `csp_engine` and `find_best_csp` both gated
with `if buy_under > 0`, so 0 meant *no restriction* — a NO BUY name got an
**unlimited** entry price, the precise inverse of the intent. That is why PATH
(`buy_under: 0`, +32.8% over 30 days) was printing a CSP with a $13.40 effective
entry. John: *"Buy under = 0. I do not want to purchase more of that stock. No
CSP or LEAP should fire."* A sentinel that reads as "unset" in one file and
"forbidden" in another will eventually be read the wrong way; make it explicit
(`is_no_buy()`) and gate at the source.
- Note the near-miss: POWL's CSP was *correct* — strike $180 − $6.50 premium =
  $173.50 effective entry, 3.6% BELOW the $180 buy target, exactly P27 — while
  the stock sat 15% above it. John chose to filter CSPs on **stock price**
  anyway, so POWL is hidden. Deliberate: the button answers "where is the stock
  vs my target", the same question on all three strategies.
- Evidence: EX-27. System status: **Actioned — A32.**

### P31 — A LEAPS entry band is a growth allowance, not a percentage
A flat band cannot work across strategies. The old LEAPS rule was
`price ≤ buy_under × 1.10`, and it excluded **23 of 23 LEAPS names** on the
2026-08-13 scan — a wall, not a band. John: *"Isn't the price target too
restrictive for LEAPS? There is a difference if LEAP dte is 2 years vs 3 years.
The length of DTE gives more time for price to rise. Also some super growth
stocks might need to be more aggressive."*

Both objections are the same objection, and one formula answers both:

    price ≤ buy_under × (1 + g) ** years

- **DTE scales it mechanically.** 20%/yr allows 31% headroom at 1.5 years and
  73% at 3 years. No separate rule needed.
- **`g` carries the growth profile.** Default by bucket (A 10% / B 15% / C 20% /
  D 25%), with a per-ticker override, because bucket grades VOLATILITY and the
  two diverge.
- **A drop re-enters the band on its own.** A big fall cuts the growth today's
  price implies, so the name qualifies without any special dip rule.

The number this exposes is the real content, and nothing displayed it before:
PLTR at $176.32 against an $85 buy target over 1.44 years **implies 66%/yr**.
That, not "107% above target", is the honest reason not to enter there.
Result on the 2026-08-13 data: 9 pass, 10 out, 4 excluded as NO BUY.
- Caveat recorded deliberately: the band measures **spot**, not breakeven
  (strike + premium, 3-9% higher). Kept on spot so CSP/CC/LEAPS all answer the
  same question; profitability stays with the LEAPS scoring that already owns it.
- Evidence: EX-27. System status: **Actioned — A32.**

---

## Trade Examples (raw log)

### EX-27 — "I forgot what IN ZONE means" (2026-08-13)
- John, looking at the CSP list: *"I see CSP for PATH. But I also know that PATH
  had a run up recently. 30 day change +29.89%. So in my mind it is not the best
  time to write CSP. There are two CSP opportunities shown POWL and PATH. When I
  click on IN ZONE it shows only POWL. I forgot what IN ZONE MEANS."*
- Investigation found the label was the smallest problem. **Three** things were
  wrong, and neither of the two tickers was behaving the way the name implied:
  - **POWL showed because of the IV override, not price.** $207.09 against a
    $180 buy target = 15.0% ABOVE it. The Price Watch panel at the top of the
    same page called POWL `FAR`. Same ticker, same screen, opposite verdicts —
    because the panel and the toggle were two different definitions of
    "in zone", and only one of them had an escape hatch.
  - **PATH was hidden for an unrelated reason** — `buy_under = 0` → NO BUY. The
    filter never looked at the 30-day run-up John was reacting to. Right answer,
    wrong mechanism, and the CSP row itself should never have existed (P30b).
  - **Every kept row was out of zone** — 12 of 12 (P30a).
- Fixed as A32. Decisions taken in the thread, all John's: kill the IV override
  outright; measure CSP on stock price and CC on sell_above; keep a NEAR tier
  (5% CSP / 8% CC, reusing the Price Watch panel's own APPROACHING bands so the
  two surfaces finally agree); growth-allowance band for LEAPS by bucket; NO BUY
  blocks CSP and LEAPS; **convexity stays exempt** (EX-15 holds — a far-OTM long
  call has no assignment risk) but now carries a visible NO BUY tag, which
  closes the C-list item opened by EX-26.
- Meta-lesson, third instance of the shape (EX-15 dashboard mismatch, EX-26
  AAPL monoculture): **when two code paths answer the same question, they will
  drift, and the drift is invisible until someone reads both.** The Price Watch
  panel and the opportunity filter had disagreed about POWL for as long as both
  existed. Nothing failed loudly; the button just quietly stopped meaning
  anything.

### EX-26 — The AAPL convexity monoculture (2026-08-06 → 08-12)
- John: "I only keep seeing trades for AAPL in convexity. Only the expiration
  DTE and strikes are changing, but I keep wondering if those are really good
  trades. Especially after recent run up in stock price."
- Verified against all 22 committed scans in that window: **every** convexity
  row was AAPL on the **same** 2028-12-15 expiry (the only expiry with
  liquidity in the 700-1100 DTE window, so "DTE changing" was just that one
  expiry counting down). Strike drifted 480 → 470 → 460 → 450 tracking spot.
  Only other ticker to appear all week: PYPL (6 rows, 2 Grade A).
- Tell: convexity scores on all 22 AAPL rows were 20.0-21.8 — clustered just
  over the old `CVX_SCORE_MIN` of 20. The floor was the binding constraint on
  every single row.
- The math was never wrong (BE $470.60, +56.1%, 20.97%/yr, cov30 1.18 all
  re-derived correctly). The selection was wrong: nothing asked whether a 21%/yr
  hurdle is plausible for a mature mega-cap, and `cov20 = 0.98` — a loss at a
  20%/yr growth scenario — was displayed but not gated.
- Also noted, not acted on (deliberately): AAPL is `buy_under: 0` and
  `sell_above: 350`, so the row shipped as an execution candidate with
  `zone_reason: "NO BUY (buy_under=0)"`, and the trade needs $470.60 to break
  even — 34% above where John says he'd sell. EX-15 already settled that
  `buy_under` should not GATE convexity (no assignment risk); surfacing the
  conflict in `fmt_convex` remains open (C-list).
- Fixed as A31 (tighten only — no new plumbing, per John's instruction).

### EX-1 — NBIS $180 put — swing-to-breakeven exit window
- Wrote CSP: stock ~$242, strike **$180**, 5 contracts, premium **$10.50**
  ($5,250 received), entry Jun 26 2026, expiry Jul 31 2026, delta 0.17, IVP 88%.
- Stock fell to ~$177 → put went ITM / deeply underwater.
- Then NBIS **+16.34% in one day** → stock ~$212.46; put back to ~breakeven
  (mark ~$10.85, ≈ −0.6%).
- John's read: if I don't want to own NBIS at $180, this spike is a window to
  close near breakeven instead of risking it sliding back underwater.
- Engine today: BIG MOVE fires correctly (15% OTM, real number shown). ✅

### EX-2 — NBIS $140 put — +36% in one day
- Wrote CSP: stock ~$177, strike **$140**, 5 contracts, premium **$15.75**
  ($7,875 received), entry Jul 19 2026, expiry Aug 21 2026, delta 0.23, IVP 100%.
- After the +16.34% day → stock ~$212.46; put marked ~$10.27 → **+36% profit**
  (34% OTM).
- John's read: big favorable swing on a fresh position; strong candidate to
  close and lock the gain if I don't want the shares.
- Engine today: BIG MOVE fires, BUT the stale-mark guard mislabels it
  "mark may be stale" and hides the real +36% (see C1). ⚠️

### EX-2b — NBIS $150 put (context, from position screenshot)
- Short 10, avg premium $9.19, mark ~$12.85 → ~ **−40%** (real loss), 29% OTM.
- Same guard misfire: real −40% would be hidden as "stale." Useful as the
  loss-side counterpart to EX-2.
- Day 2 update (+17% day): mark $12.70 → loss narrowed to ~−36.5%. A
  loss-reduction close candidate under P6 if the recovery extends.

### EX-3 — NBIS $180 put CLOSED at $10.00 on the spike (completes EX-1)
- 2026-07-21, NBIS +17% second day of rally (~$213.67). Closed the Jul31 $180
  put at **$10.00** vs $10.50 received → small profit (~5% of premium), after
  being ~**35% underwater the day before**.
- Stated rationale: (a) earnings can go either way, prefer not to carry the
  $180 obligation through that uncertainty; (b) at this volatility, if short a
  put at all, it should be at a LOWER strike — and the $140 (written the prior
  day, now +37%) already fills that role; (c) the decision hinge is "how much
  do I want to own at $180" → answer: less than at lower strikes.
- Follow-up confirmed (2026-07-21): the earnings logic was strike-depth logic
  (the $180/Jul31 expired BEFORE the 8/6 earnings; the kept $140/$150 Aug21
  puts sit THROUGH it). The principle is P9: keep earnings exposure only at
  strikes worth owning through the event; the $180 close was locking the
  recovery at a strike he didn't love.
- Prediction logged: John expects a drop after this spike and plans to write a
  new CSP below $180 — watch whether this plays out (P8 evidence).
- What John wants from the system for this case: a notification in the
  **CSP/CC Actions** view when a short-option position makes a big favorable
  day-over-day swing (e.g., substantially negative → positive/breakeven), so
  it's obvious WHICH position produced the exit window. 17%/day is huge; the
  card should make the swing visible, not just current P&L.

### EX-6 — PATH close + Telegram noise feedback (2026-07-21, evening)
- **PATH CC $13**: alert "PATH dropped 10.3% — your CC $13 is 20% OTM, now at
  65% profit ($260 to close, 29d left)" → John closed at **$0.21 fill**
  within minutes. First fully-validated A5/A6 alert → action → fill cycle.
  (Also note: 10.3% would have fired even at the old 0.10 threshold, but the
  real P&L display and stacked framing are new.)
- **Noise complaint**: same evening, NBIS $140 (+52%, 36% OTM) and NBIS $150
  (−30%, 31% OTM) BIG MOVE alerts judged "not valuable" — he wanted ONLY the
  PATH-style ping. NBIS had also re-alerted on every scan while its day move
  stayed over 5%. Distilled into P17; gate calibrated so that every alert he
  acted on (PATH, CLS ×2, NBIS $180 swing) passes and both NBIS noise alerts
  fail (9/9 test cases).

### EX-25 — Move Watcher called owned LEAPS "short … moving toward your strike" (2026-08-07)
- John: "This message is confusing — `You hold short LEAPSCALL $50 20280121 —
  ⚠ moving toward your strike`. Those are LEAPS."
- He's right on every count; one line had three defects:
  1. **"short" is factually wrong** — LEAPS_CALL entries are calls he OWNS.
  2. **The warning is backwards** — `favorable` only tested CSP/CC, so any
     other type fell through to "⚠ moving toward your strike". For a LONG
     call a rising stock is GOOD, not a warning.
  3. **"LEAPS_CALL" rendered as "LEAPSCALL"** — Telegram Markdown ate the
     underscore as an italic marker.
- Root cause: same as EX-24 — PR #2 added LEAPS_CALL rows to
  `position_actions`, and `move_watcher.build_line()` had always assumed
  everything in that list was a short premium position. A new position TYPE
  flowed into consumers written for the old assumption.
- Fixed (A30): long calls are skipped in the Move Watcher's held-position
  context AND in the `near_actionable` proximity gate (a long strike sitting
  near spot is not a risk event and must not manufacture urgency). Consistent
  with P16 — LEAPS aren't traded off short-term moves.
- Pattern worth noting: this is the second consumer broken by the same upstream
  change. When a new position type is added to `position_actions`, EVERY
  consumer of that list needs review — the scanner's Telegram blocks, the Move
  Watcher, and the dashboard all read it.

### EX-24 — 10 LEAPS trim alerts in one batch; P16 violated (2026-08-07)
- John received 10 consecutive "📞 HELD LONG CALL — REVIEW" pushes: 3 AMZN and
  6 NVDA all saying "NEAR 52W HIGH", plus PATH "SELL TARGET HIT". He asked
  whether it was an unintended consequence.
- Source: PR #2 that morning ("Price target updates + held long-call
  tracking") added `long_call_management_engine`. Not from the A28 work.
- Three distinct problems:
  1. **Violates P16** — LEAPS are long-term stock replacement; John explicitly
     does not want trimming prompts driven by short-term price action.
  2. **Two alerts were nonsense**: NVDA $450 and $280 calls are far-OTM
     convexity LEAPS (Dec-2028, $13-33 cost) with NVDA at $223. "Stock near its
     52-week high" is an argument FOR those positions — they need NVDA to
     double. Suggesting review because the stock rose inverts the thesis.
  3. **Volume**: 10 pushes carrying 2 facts (AMZN near high, NVDA near high) —
     one message per contract instead of per ticker.
- The one defensible alert was PATH $8 (deep-ITM stock replacement, PATH $14.92
  above the $13 sell target) — a real decision.
- Fixed as A29 exactly per John's instruction ("NEAR 52W HIGH — yes useful on
  dashboard, yes implement all what you said"). Replaying today's batch through
  the new gate: **10 pushes → 1** (PATH only).
- Lesson: a documented principle in this log did not prevent another session
  from building against it. Principles need to be checked when work lands from
  a different session, not just when written.

### EX-23 — "How likely is assignment?" — the missing triage number (2026-08-07)
- John: "I always think how likely the shares might get called away or assigned.
  If the risk is high I feel bigger urgency… if the possibility is very small I
  do not want to waste so much time. Stock moved 30% and then an additional 5%
  — this is not a signal for urgency." Asked to explore and discuss first.
- The number already existed and was being discarded: the engine reads each
  position's contract from the chain to get the mark, and `delta` sits in that
  same record. Alerts showed `dist_to_strike` instead — a raw percentage blind
  to volatility and time.
- Built A28 (display only, per John's "discuss first"): `Assignment odds ~X%`
  now appears in the reason line of BIG MOVE, P&L SWING, TAKE PROFIT and HOLD,
  which renders on BOTH Telegram and the dashboard CSP/CC Actions tab (putting
  it in the reason string rather than a new field means no dashboard-repo
  change is needed). Structured `assign_odds` / `assign_odds_est` fields also
  written to results.json for future use.
- Fallback: when no chain contract is found (stale-mark path) the odds are
  estimated via `estimate_delta()` and labelled "(est)" — never silently
  presented as exact.
- Validated against his real decisions: NBIS $140 noise → 3%; CRDO $200 closed
  → 88%; his stated 30%-then-5% example → 2%; stale-mark fallback → 24% (est).
- Open follow-up: use odds as the P17 urgency gate (ping when ≥15%, quiet
  below) instead of the current dist_to_strike ≤15% rule. Proposed, not built.

### EX-22 — Two scans killed by the 15-min timeout (2026-08-06)
- John: "the last few scans were failures." Both Aug 6 scheduled runs (15:57
  and 18:14 UTC) ended `cancelled` after 15.1 and 15.8 minutes — the
  `timeout-minutes: 15` kill, not a crash. The 13:53 dispatch that day
  succeeded in 2.7 min, which is the normal duration (recent runs: 2.6-3.0 min
  for a full scan, 0.2 min for a schedule run that skips as redundant).
- So the scan HUNG for ~12 extra minutes rather than gradually slowing.
  **Root cause not identified** — GitHub does not retain logs for jobs killed
  by timeout (both job-log fetches returned HTTP 404), so there is no record of
  where it stalled. Ruled out: earnings-calendar lookups (`load_cache` is
  memoized, 30 lookups = 0.002s) and the Nasdaq sweep for those two runs (the
  cache was already built that day at 13:10 by the earnings-watcher, so
  `refresh_needed()` was False).
- Remaining suspects (unproven): a Schwab/Yahoo call hanging without an
  effective timeout during the per-ticker chain loop, or Schwab auth
  degradation — the token has a 7-day expiry and the last known refresh was
  around 2026-07-31.
- Actions (A27): raise the job timeout 15 → 25 min so a merely-slow scan
  completes instead of dying; add `_phase()` timing prints at every major
  stage so the NEXT occurrence names the culprit in the log; and commit
  `earnings_calendar.json` from scanner.yml — it was rebuilt (~38 Nasdaq
  requests, worst case ~10 min) and then thrown away on any run where the
  scanner is first that day, which is a genuine timeout risk in its own right.
- Lesson: a timeout kill destroys its own evidence. Instrument BEFORE the next
  failure, not after.

### EX-21 — NOTABLE MOVES ballooned to 14 names in a rally (2026-08-05)
- Aug 5 briefing listed 14 names under NOTABLE MOVES (AAPL, NVO, CRDO, PLTR,
  NBIS, PATH, TSM, MSFT, AMZN, FIX, POWL, GOOGL, MU, NVDA). John: "too many —
  I would like to see just the more notable."
- Cause: not a bug — a broad rally. The A18 bucket bars (A8/B10/C13/D18 on the
  net 5-day move) are calibrated for a normal tape; when most of the watchlist
  is up 10-30% over five days, nearly everything clears its bar at once. A
  move-magnitude rule alone has no ceiling on output.
- Fix (A26), three parts: (1) raise the bars to A12/B15/C20/D28; (2) hard-cap
  the list at `NOTABLE_MAX_LINES` = 5, keeping target-triggered (IN_ZONE) rows
  unconditionally and filling the rest with the largest 5-day moves, with a
  "+N smaller moves — see dashboard" footer so nothing is silently lost;
  (3) dedupe against the CSP/CC opportunity sections — PATH appeared twice in
  the same message (once as a CC alert, once as a notable move).
- Result on the real message: 14 → 5 lines (NBIS +49.8, PLTR +29.6, CRDO +29.5,
  MSFT +25.6, MU +23.8), PATH deduped.
- Design note: a cap is the structural fix a threshold can't provide — it makes
  the message length bounded regardless of market conditions, which is what
  P22 ("Telegram = go sit at the computer") actually requires.

### EX-20 — TSM CSP alert above the buy target: Telegram path had no gate (2026-08-03)
- Alert: CSP TSM @ $406.86, sell $370 put, 45 DTE, 25.3% annualized, δ0.26,
  39 contracts / $1.44M collateral. John challenged it: TSM is nowhere near his
  $320 buy target and the alert's own text said "near 52w high."
- He was right, and the numbers are stark: effective entry = $370 − $11.55 =
  **$358.45**, which is **12% ABOVE** his $320 buy target. csp_engine (the
  dashboard path) would have hard-skipped it — its limit is $320 × 1.03 =
  $329.60.
- **Root cause — the C9 pipeline split again, now on the CSP side.**
  `find_best_csp()` feeds Telegram and had **no buy_under parameter at all**;
  only the dashboard's `csp_engine()` enforced the rule. So Telegram could
  recommend CSPs whose assignment price violated the core target, indefinitely.
  This is the third instance of the same class (EX-17 CC shares, EX-15 dashboard
  mismatch) — different pipelines computing different answers.
- Fixed as A24 (buy_under gate, same 3% grace as csp_engine, so both paths now
  agree). Verified against the real chain: the $370 and $340 puts are blocked;
  $330 and below pass.
- Two display bugs in the same alert, also fixed (A25): "✅ Earnings in 999d —
  safe" — 999 is the *no earnings date known* sentinel (C13) being printed as a
  real number AND labelled safe; and "Core Core tier" duplication.
- Unresolved contradiction noted: the alert simultaneously said "near 52w high"
  (timing_score) and "23.1% off highs" (quality) — two different measures
  disagreeing in one message. Logged as C14.

### EX-19 — "IVP 58%" vs the broker's 29%: the label was fake (2026-07-31)
- John on the AMZN CC card: "I actually see IVP at 29% for AMZN so I think
  premium will not be so exciting." Scanner card read "✅ IVP 58% — Good".
- Investigation: the scanner's `ivp` is `100*(1-exp(-atm_iv/0.25))` — a pure
  function of current ATM IV, no history. Inverting it: "IVP 58" = **ATM IV
  21.7%**, which is ordinary-to-low for AMZN. John's 29% was a TRUE percentile
  (post-earnings IV crush). **John's read was correct and the scanner's label
  was actively misleading** — it implied rich premium on a 21.8%-annualized CC.
- CLAUDE.md already warned "IVP ≠ IV Rank", but every card, alert and score
  still printed it as if it were a percentile, so the warning never reached the
  decision. Naming an approximation after the real metric is worse than not
  having it.
- Fix A23: honest labels now; real percentile auto-enables once history banks.
- Note: the technical limitation was genuine — Schwab exposes current IV only,
  no history — so a real percentile cannot be backfilled, only accumulated.

### EX-18 — AMZN post-earnings CC: the C12 bug's live scenario (2026-07-31)
- Follow-up to EX-17. John: "It had earnings yesterday. Therefore the increase
  in stock price." AMZN reported 2026-07-30 → ~15% pop to $271.32, IVP still 58.
- This is the textbook CC window: **event resolved, IV not yet crushed, stock
  above the $270 sell target, no earnings inside a 30-50 day expiry.** Card:
  CC $285 / 20 DTE / δ0.27 / $3.40 premium / 21.8% ann, effective sale $288.40.
- **Confirms C12 was live, not theoretical:** `days_to_earnings` used
  `max(0, delta)`, so a just-passed earnings date reads as 0 = "earnings today"
  and the A17 CC gate would have SUPPRESSED exactly this best-case CC. It only
  escaped because `get_earnings_date("AMZN")` returned None entirely.
- Fixed as A21: a PAST earnings date now yields 999 ("no upcoming earnings"),
  while delta == 0 (earnings actually today) still suppresses.
- Second issue raised in the same card: 20 DTE — too short (P25 → A22).
- Open concern: `get_earnings_date` returned None for AMZN, a mega-cap. The
  earnings feed has real coverage gaps, which quietly weakens every earnings
  gate (A15/A17/A19). Worth a dedicated look — see C13.

### EX-17 — AMZN CC missing from Telegram: IBKR-only share lookup (2026-07-31)
- AMZN +~15% to $271.32, ABOVE John's $270 sell target. He expected a CC
  trigger; the dashboard HAD one (CC $285, score 13/13 — perfect — IVP 58,
  δ0.27, 21.8% ann, plus a SPIKE_CC $280) but no Telegram alert fired.
- Ruled out one by one: score gate (13 ≥ threshold 10 ✓), A14 sell-target gate
  ($271.32 ≥ $270 ✓), A17 earnings gate (AMZN earnings = None ✓), John's own
  IVP hypothesis (IVP 58 — not low), market regime (`sell_premium` hardcoded
  True).
- **Root cause:** the two CC pipelines used different share-count sources. The
  dashboard uses `position_check()` → `qty_cache` (all accounts). The Telegram
  path used `stk_hold`, built IBKR-only (`asset_class=="STK"` over the ibkr
  dict). AMZN is a **Schwab IRA** holding ($1.37M), so qty read as 0 and the
  entire CC block was skipped → AMZN could never enter `cc_opps`.
- Fix (A20): CC block now uses the same `position_check` qty/avg as the
  dashboard, `stk_hold` only as fallback.
- Lesson: this is the C9 pipeline split biting for real. Any "dashboard shows
  it, Telegram doesn't" report should compare the two data sources first.

### EX-16 — CRDO $200 put: P&L SWING alert → closed at $10 (2026-07-30) ✅
**The feature working exactly as specified, with a real fill.**
- Alert (06:45 PT): "CRDO rose 7.5% today — your CSP $200 is ITM, now at 10%
  profit ($3,660 to close, 0d left). Big move — review whether to close before
  it reverses. **Swing since last scan: 46% loss → 10% profit.**"
- John: "That is exactly the right notification as I wanted. The put which was
  under water yesterday got profitable today, a day before expiration."
- **Closed at $10.00 fill** (3 contracts): premium in $13 → $3,900 received,
  $3,000 paid to close, **realized +$900 = 23% of premium**, 1.5% on $60,000
  collateral. Better than the $12.20 mark shown at scan time (CRDO kept rising
  during the day, so the put decayed further in his favour).
- Decision quality: at the alert's $190.78 spot, intrinsic was $9.22 and the
  effective basis on assignment would have been $187. He had just lowered
  CRDO buy_under to $175 — so he did NOT want the shares at $187, and paying a
  small amount of time value to avoid assignment is coherent with that target
  (P9/P8: only accept assignment at strikes you want to own).
- Validates: A6 (P&L SWING + stacked swing context), A5 (real P&L shown on a
  volatile name — the old stale-mark guard would have hidden this), P17 (the
  alert passed the Telegram gate on near-strike/ITM, not on profit %).
- Fourth completed alert→action→fill cycle after PATH (EX-6), CLS ×2 (EX-5),
  NBIS $180 (EX-3). The 0-DTE timing also shows the value of the swing line:
  the position was only worth acting on because of WHERE it came from.

### EX-15 — PYPL convexity alert on earnings day (2026-07-28)
- Telegram: CHEAP CONVEXITY PYPL @ $58.58, Grade B, buy $90 call Dec-2028
  (870 DTE), mid $1.73 / ask $1.85, breakeven $91.85 (needs 20.8%/yr), spread
  14.5%. PYPL reported earnings the same day. John asked (a) why it wasn't on
  the dashboard, (b) whether earnings makes it bad timing.
- (a) Not a bug: the PYPL row WAS in the committed results.json (with an AAPL
  $510 Grade B); John was viewing a cached copy — confirmed visible after
  refresh. Worth remembering when a Telegram/dashboard mismatch is reported.
- (b) Claude's read: structure fine (max loss = premium, 48.6x convexity, 1.3%
  /yr burden) but entering on earnings day is poor — you pay pre-earnings IV on
  a vega-heavy long-dated option, the 14.5% spread is wide, and a 20.8%/yr
  hurdle makes entry price most of the edge. Recommended waiting a day.
- Also flagged: PYPL buy_under is $35 vs $58.58 spot — a convexity LEAP isn't
  stock ownership (no assignment), so not a contradiction, but worth being
  deliberate about funding a +57% bet on a name he won't buy for 40% lower.
- Gap found: scan_convexity had NO earnings awareness at all. Fixed as A19
  (warning, not gate — P24).

### EX-14 — The round-trip problem: a 1-day move alone is misleading (2026-07-24)
- John: "If AAPL drops 7% today but rose 7% yesterday, I consider it neutral —
  overreaction to news that corrected itself, no action warranted. But you
  might say 'write CSP.' In this situation the hard buy-below target works
  better." Key insight: notability can't key off the 1-DAY move — a move that
  reverses a recent opposite move is noise.
- Design consequence: gate notability on the NET multi-day move (5d), which
  mathematically cancels a round-trip (today is inside the 5d window, so
  −7%/+7% nets ~0 in 5d while a real crash or slide shows up), PLUS price
  at/below the buy target as a second confirmation (a round-trip doesn't leave
  the price at the target). The 1-day % is shown for urgency, not the trigger.
- Reconciles EX-13: bucket-scaled = "big for this stock"; net-move + target =
  "real, not a round-trip." Hard targets are NOT abandoned — they're the
  confirmation that catches exactly this case.

### EX-13 — Telegram notification philosophy (2026-07-24)
- John: "I want a system where really notable moves will NOT slip from my
  attention, but there won't be many other notifications or noise — so I don't
  stop paying attention to Telegram. I don't want to be constantly checking
  CSP/CC/LEAPS in my sheet. And the hard price targets are short-term and
  high-maintenance — I have to update them often — so the system shouldn't lean
  entirely on them."
- Correction on record: A16 over-tightened — it gated notable moves BEHIND the
  hard targets (IN_ZONE only), the exact fragile dependency John objects to,
  and dropped genuinely notable moves (CRDO −10%). To be revised per C11.

### EX-12 — "NOTABLE MOVES" too noisy; wants earnings reminder (2026-07-24)
- Scan Telegram "NOTABLE MOVES" listed CRDO −10% (6.5% from buy), MU −7.8%,
  TSLA −2.3%/−18%5d (4.1% from buy), NOW +7.1% (9.4% from buy) — each with
  "→ consider CSP/LEAPS" or "consider CC". John: "nice to see notable moves,
  but I don't like the 'consider'. Minimize signals unless the stock is really
  below buy or a really serious LEAPS. I don't want to check seven stocks. Make
  Telegram a clear signal to go sit at the computer — e.g. 'TSLA is below your
  buy and dropped X% today'."
- Also asked: show the earnings date on whatever fires ("Earnings 7/31") as a
  reminder he might otherwise miss.
- Fixes: A16 (GO-only gate → P22) + earnings tag on CSP/CC/spike alerts and GO
  lines. Validated: all four screenshot names suppressed; only IN_ZONE GO
  signals fire (6/6).

### EX-11 — AAPL spike CC below target + near earnings (2026-07-24)
- SPIKE CC alert: AAPL @ $332.67, +8.6% above 50MA, IVP 98%, sell call $340
  (20 DTE / Aug 14), 35.3% annualized. John: (1) "same as NVDA — only notify
  if above the sell-above target" (AAPL sell_above = $360, so $332.67 is 7.6%
  below); (2) "earnings is a few days away, bad time even with the spike."
- Two gaps: the spike-CC path (find_spike_cc → tg_spikes) bypassed the A14
  sell-target gate entirely (different code path), and its earnings gate
  (`days_to_earnings > 7`) either just cleared (~8d) or the earnings date
  wasn't fetched.
- Fix (A15): unified `_cc_telegram_ok` now gates BOTH regular and spike CC on
  (a) price ≥ sell_above and (b) earnings not inside the option's expiry.
  AAPL suppressed on the sell-target rule regardless of earnings-data state;
  additionally on earnings when the date is known. Validated 8/8.
- Caveat logged: if AAPL's alert fired because get_earnings_date returned
  nothing, the earnings gate can't help (missing data) — the sell-target gate
  still covers this case. Worth a separate check on get_earnings_date coverage.

### EX-10 — NVDA CC ping below the sell target (2026-07-24)
- Telegram fired a CC for NVDA at stock $209.685, sell call $235, IVP 51%,
  15.6% annualized. John: "should not fire if it is not above the sell target.
  If NVDA was above $225 I would like to be notified." NVDA sell_above = $225.
- Cause: the CC fired on the zone-first MIDPOINT gate ($202.50 for NVDA's
  $180–$225 band), which $209.69 clears. John wants the alert gated at the
  full sell target, not the midpoint.
- Fix (A14): CC Telegram gate requires underlying ≥ sell_above (cc_only names
  exempt). Validated 8/8 (NVDA suppressed at $209.69, pings at $225+; MSFT
  below $500 suppressed; TSM above $450 pings; MSTR/OWL always ping).

### EX-9 — Move Watcher noise: too many pings far from targets (2026-07-23)
- 10:56 ET ping listed CLS, CRDO, MU, NBIS, POWL (5 names). John: "a little
  less messages." Pointed at MU −6.7% ($923.86): buy-under $450 → 105% above,
  short CSP $700 → 32% away. "I don't even want to be notified unless it's
  getting close." Same for the far short CSP ($700 vs $900).
- Distilled: gate the ping on PROXIMITY to an actionable price, not the size
  of the move (A13). Only CRDO (7.8% from buy, on the $200 strike) and POWL
  (9.4% from buy) survive the gate — exactly the actionable ones.
- Note: NBIS −10.5% is silenced from the ping but STILL triggers a full scan
  (≥8%), so its real candidate (BUY_DIP LEAP if IVP low, updated cards) is
  delivered by the scan — the noisy price ping is replaced by the actual
  actionable alert, not lost.

### EX-8 — TSLA −14% dip: LEAPS bought on the drop (2026-07-23)
- TSLA $321.04, **−14.16% today** (−52.98), IVP ~28%. 52wk range $297.82–
  $498.83, so ~36% off the high.
- John: "Remove the waiting for falling-knife stop. I take the risk if TSLA
  drops again tomorrow. The dip is 14%+ — strong signal to look into LEAPS."
  **Executed:** bought LEAPS at **$180** and a few at **$240** strike
  (deep-ITM stock replacement — $180 is ~44% ITM, $240 ~25% ITM).
- Engine gap it exposed: LEAPS Telegram only fired on trend_action == "BUY",
  and `leaps_trend_action` returned WAIT ("wait for stabilization") for
  STILL_FALLING / AT_LOWS — the exact philosophy P7 rejects. So the biggest,
  cheapest dip of the week produced no LEAPS alert.
- Fix (A11): BUY_DIP action on big-drop + low-IVP, ahead of the wait states;
  validated TSLA −14%/IVP28 → BUY_DIP, high-IVP version stays quiet, mild
  drops unaffected, existing states unchanged (6/6).
- Challenge on record: a long LEAP on a still-falling stock has no premium
  cushion (unlike a CSP) — if TSLA keeps dropping the call bleeds directly.
  John accepts this explicitly; the low-IVP gate is the risk control (cheap
  entry), not a stabilization wait.

### EX-7 — NBIS post-rally: attractive premium, wrong day to write (2026-07-22)
- NBIS $225.64, **+4.02% today, +31.36% over 5 days**. Dashboard showed CSP
  cards as BUY (RISKY) at 110-117% annualized (the $170/$195 strikes).
- John: "I would NOT write CSP today for NBIS due to the huge run-up 35% in 5
  days. This is not a day to write CSP and I am certain." Premium looks great;
  timing is wrong — he's at the SPIKE end of the P8 swing, not the drop end.
- Engine gap (verified in code): csp_engine only guards the UP side on a
  1-DAY basis (≥7% skip / ≥5% downgrade). The 5-day check `rebound_relative`
  is computed ONLY when 5d change is negative (recovery-inside-a-drop). A
  sustained multi-day rally is invisible, and the pullback-from-52wk-high
  rule actively promotes it to BUY (RISKY) because NBIS is still ~30% below
  its high. The +4% day slips under the 1-day bar.
- **Design decision (2026-07-22): don't hard-code thresholds; measure the
  move in units of the stock's OWN normal movement (IV-scaled).** See P19.

### EX-4 — Analysis of the Options Trades sheet (2026-07-21)
Parsed John's Google Sheet ("Options Trades", 348 usable trades: 159 CSP,
87 CC, 99 LEAP, 3 Bull Call). Delta/IVP-at-entry recorded from Dec 15 2025 on.

**Personal premium book — entry stats for the most-traded names (CSP/CC):**

| Name / strat | Trades | Delta at entry (med) | IVP at entry (med) | Annualized (med) | OTM% at entry (med) |
|---|---|---|---|---|---|
| NBIS CC | 32 | 0.20–0.38 (0.28) | 32–96 (43) | 26–77% (42%) | 12–60% (23%) |
| IBIT CSP | 31 | 0.13–0.37 (0.30) | 26–74 (39) | 11–62% (38%) | — |
| MU CSP | 17 | 0.17–0.38 (0.27) | 49–93 (82) | 26%+ (55%) | 4–19% (11%) |
| LULU CSP | 14 | 0.23–0.34 (0.31) | 54–86 (84) | 25–61% (35%) | 5–11% (9%) |
| AMZN CSP | 13 | 0.26–0.68 (0.30) | 25–65 (51) | 17–93% (30%) | ~7% |
| NVO CSP | 11 | 0.27–0.36 (0.29) | 38–48 (40) | 31%+ (44%) | — |
| MSFT CSP | 10 | 0.22–0.37 (0.23) | 34–42 (34) | 15–31% (20%) | 1–6% (6%) |
| IBIT CC | 10 | 0.21–0.31 (0.27) | 5–25 (13) | 12–30% (19%) | 8–15% (11%) |
| NBIS CSP | 8 | 0.21–0.29 (0.29) | 34–38 (34) | 48–86% (72%) | — |
| OWL CSP | 8 | 0.25–0.34 (0.31) | 37–83 (63) | 37–65% (57%) | ~17% |
| NVDA CSP | 7 | 0.15–0.27 (0.22) | 23–39 (26) | 18–97% (22%) | 6–11% (10%) |
| CRDO CSP | 6 | 0.16–0.26 (0.20) | 27–35 (30) | 46–64% (50%) | 13–18% (17%) |
| GOOGL CSP | 6 | 0.18–0.30 (0.29) | 19–66 (26) | 16–31% (30%) | 4–12% (5%) |

**Patterns visible in the history (not yet rules):**
- CSP deltas cluster 0.21–0.31 across nearly every name (med ~0.27–0.30) —
  remarkably consistent, and a bit higher than the scanner's Bucket D bands.
- On the highest-vol names (MU, LULU) entries cluster at very high IVP
  (med 82–84) — P7/P10 in the data.
- But plenty of good entries happened at IVP 26–40 (NBIS, MSFT, CRDO, NVDA,
  GOOGL) — the "IVP≥50" funnel bar is soft in practice (see P10 tension).
- IBIT CCs were written at med IVP 13 — very low vol. RESOLVED 2026-07-21:
  deliberate — "IBIT I want to sell", exit intent overrides IV richness (P14).
- Jul 20 2026 CRDO $175 CSPs (IVP 94, 74–81% ann) beat all six prior CRDO
  entries — first concrete case where history context would have upgraded a
  scanner signal (P13).

**Data-quality caveats for any future automation:**
- The sheet's "Stock Price" column is a LIVE formula (shows today's price on
  old rows) — entry price is "Stock price at purchase" / "Spot Price at
  Entry", only filled on some rows (134 of 348).
- Delta/IVP at entry recorded only from Dec 15 2025 onward (184/155 rows).
- The Drive export truncates/mangles some recent rows (Jun 26 + Jul 19-20 2026
  NBIS/CRDO entries missing from export despite existing — confirmed via
  John's screenshots). Multi-tab layouts differ; parse per-header.
- Closed Fill price exists on 138 rows → win/loss analysis is possible later
  (deferred to the Trading Performance Review project).

### EX-5 — CLS spike day: the exit flag that SHOULD have fired (2026-07-21)
Positions: short 3× Jul31 $300 puts (prem $16.31, mark $11.06 → **+34.0%**)
and short 3× Jul31 $280 puts (prem $11.55, mark $6.05 → **+48.1%**). Also
long 10× Jan'28 $180 LEAPS calls + 1 share. Stock **+9.57%** to $336.75.
- Context: CLS earnings **7/27** — INSIDE the puts' expiry window (7/31),
  5 days away. Some analysts call CLS overpriced; John genuinely unsure of
  direction. $300 strike is only **10.9% OTM**.
- John's read: today's spike halved the puts' value in one session (−46%/−52%
  on the day). "If I want out, this is a good day to do it profitably" —
  wants the system to FLAG this day on these positions.
- What the engine actually does (verified by running it): both puts →
  "EARNINGS WARNING — decide before event (32%/48% profit captured)". No
  mention of the spike. BIG MOVE silent: +9.57% < hard 10.0% BIGMOVE_1D. Had
  the move been +10.1%, BIG MOVE would have fired but suppressed the earnings
  line (one action per position).
- Contrast with EX-3/P9: on NBIS he KEPT the through-earnings puts because
  strikes were 30%+ OTM. Here the $300 is 10.9% OTM through earnings — much
  closer, so the exit-on-spike reads consistent with P9, not contradictory.
- Also note (P10 funnel): +9.57% would have made CLS a "today's mover" name
  under his ≥5% daily screen — the funnel catches what the 10% alert missed.
- **OUTCOME (2026-07-21): CLOSED both puts into the spike**, same day —
  P15 executed in real time (fills recorded in the Options Trades sheet per
  John's practice; Drive export truncation currently hides those rows from
  Claude — grab them when C7's sync path is built; marks at decision time
  ~$11.06 / ~$6.05 → roughly +34% / +48% of premium captured, 9 days early,
  ahead of 7/27 earnings). Second completed spike-close after EX-3 — the
  pattern is now 2-for-2 on spike days. Open thread: the 10× Jan'28 $180
  LEAPS ride through earnings — John hasn't said whether P15 applies to
  trimming those (Claude's challenge, unanswered).

---

## Candidate system changes (pending — do NOT implement without go-ahead)

### ~~C1~~ — GRADUATED → A5 (built 2026-07-21)
### C1 (original text) — Mark-credibility guard misfires on high-IVP names
`position_management_engine` line ~1971:
`if dist_to_strike >= 20 and profit_pct < 60: mark_src = "incredible"`.
Assumes deep-OTM ⇒ near-worthless ⇒ high profit. False on NBIS/CRDO/CLS, where
deep-OTM options legitimately hold value (P2). It hides real P&L behind
"mark may be stale" — and overrides even live NBBO chain quotes.
- Proposed: only apply the override when the mark came from the **position feed**
  (stale-prone fallback); **trust live chain NBBO**. Optionally make the
  fallback threshold vol-aware (scale by IVP).
- Guardrail: CLAUDE.md flags stale-mark logic as the #1 historical source of
  wrong alerts — preserve protection for genuinely stale position-feed marks.

### ~~C2~~ — GRADUATED → A6 (built 2026-07-21)
### C2 (original text) — Swing-aware / loss-reduction close prompts (P6, P15)
Frame BIG MOVE (and maybe a new prompt) around the swing: "this move cut your
cost-to-close from $X to $Y." Consider surfacing loss-reduction exits ("a swing
has cut this loss from −X% to −Y%; close window before it widens"). Needs more
examples to set thresholds.
- EX-5 learnings (2026-07-21): (1) the hard 10.0% BIGMOVE_1D threshold missed
  a +9.57% CLS day John considered flag-worthy — threshold should be softer
  and/or per-name (P7 already says judge moves against the name's own history;
  his manual screen uses ≥5%). (2) BIG MOVE and EARNINGS WARNING are mutually
  exclusive (one action per position), but the CLS case needed BOTH in one
  alert: "spike day + earnings in 5d inside expiry + strike 11% away + +34%
  available = good exit day if you want out." Direction: a confluence-scored
  exit flag whose reason line stacks every active factor, instead of a
  priority ladder that shows only the top one.

### C3 — Map CC entry deltas to intent (P5)
Encode "don't want to sell → 0.20–0.25 delta / do want to sell → 0.25–0.30" as
a selectable posture in the CC engine. Needs confirmation of how to expose it.

### ~~C4~~ — GRADUATED → A7 (built 2026-07-21)
### C4 (original text) — Intraday move-watcher (detection cadence)
The 3×/weekday scan can miss a fast intraday spike entirely (it can fade before
a scan runs). Bigger build — already on the CLAUDE.md backlog. Tracked here
because it directly limits P1 (BIG MOVE can only fire if a scan catches the move).

### C5 — Rethink WAIT labels for high-IVP big-drop setups (P7)
Current csp_engine downgrades BUY→WAIT on below-200DMA / at-lows. John's actual
behavior (EX-2) is to SELL into exactly those conditions when the move is big,
IVP is extreme, and the strike sits well under his buy-below target. Possible
direction: when IVP is very high AND effective entry is comfortably below
buy_under, don't downgrade — or show a distinct label ("RICH PREMIUM — BIG
MOVE") instead of WAIT. Needs more examples before touching risk logic.

### C6 — Spike-close → re-entry-lower tracking (P8)
After a position is closed into a spike (or BIG MOVE fires), track the name for
the follow-on drop and surface the lower-strike CSP re-entry. Also: make the
BIG MOVE / Actions card show the day-over-day P&L swing ("was −35% yesterday →
breakeven now"), not just current P&L, so the position that produced the exit
window is unmistakable. Depends on C1 (real P&L must be shown for the swing to
be visible) and is limited by C4 (cadence).
- Trigger confirmed 2026-07-21: **volatility / price action**, not P&L
  thresholds. What John wants highlighted on the Dashboard: a position that
  was hugely negative one day and is positive/breakeven the next — so if he
  decides to close, he immediately knows WHICH position today's move made
  beneficial to close. Implementation direction: persist each position's P&L
  per scan (results.json already regenerates; needs a small history store) and
  highlight sign-flips / large day-over-day P&L swings on the CSP/CC Actions
  cards.

### C7 — Per-ticker trade-history context on opportunity cards (P13)
The scanner knows nothing about what John was paid before on the same name.
Candidate: keep a normalized copy of the Options Trades history (or a distilled
per-ticker stats file — see EX-4 table) in the repo, and show a context line on
CSP/CC cards: "Your CRDO CSP history: 6 entries, med δ0.20, med IVP 30, med
50% ann → today's 81% is your richest." Needs: a sync path from the Google
Sheet (manual export is fine to start), and the EX-4 data-quality caveats
handled.

### C9 — Conviction-only Telegram for OPPORTUNITY pings (P18)
John acts on BUY (RISKY)/BUY (SAFE) CSP cards; WAIT cards are dashboard
material. LEAPS should ping rarely ("borderline... or none"). Design issue:
Telegram CSPs currently come from the strict execution pipeline (score-gated),
which is separate from the dashboard pipeline that computes BUY/WAIT actions —
wiring conviction into the Telegram gate needs the two pipelines reconciled.
Collect a few more "I'd act on this / noise" examples first.

### C10 — IV-scaled, soft "wrong side of the swing" CSP timing gate (P19)
Replace/extend the 1-day-only up-move suppression in csp_engine with an
IV-scaled multi-day check: compute normal move = IV × √(days/252), express
the recent 1d and 5d moves as multiples of normal, and DOWNGRADE (BUY→WAIT,
never SKIP) when the up-move multiple exceeds ~1.2×. Data available: ATM IV
per name from the chain fetch; 1d and 5d changes already in mkt/trend_state.
Flag text names the reason in John's terms ("+31%/5d ≈ 2.4× normal"). Same
machinery could later flag the CC side symmetrically (drop that's already
overextended) and inform C5. BUILD ONLY after enough cross-vol verdicts
validate the multiple — see P19(b).

### ~~C11~~ — GRADUATED → A18 (built 2026-07-24)
### C11 (original) — Bucket-scaled, net-move notability for Telegram (EX-13/EX-14)
Redefine what earns a Telegram "notable move," to make Telegram high-signal
without leaning on hand-maintained price targets:
- **Notable = big for THIS stock** — threshold scales by bucket (A–D), which is
  stable (rarely edited) unlike buy/sell targets. Draft: 1d A5/B6/C9/D12%.
- **Trigger on the NET multi-day move, not 1-day** — gate on 5d (bucket-scaled),
  which cancels round-trips (EX-14). 1-day % shown for urgency only.
- **Hard target as the SECOND confirmation** — price at/below buy (drop) or
  at/above sell (rise) is an independent trigger AND the round-trip catch.
  Notable = (sustained 5d move, bucket-scaled) OR (price in the target zone).
- **Telegram shrinks to 3 things:** notable moves (clean, + earnings, target as
  context not nudge), position alerts (P17), rare real opportunities (BUY_DIP /
  CC-at-target / convexity A/B). Remove "consider CSP/LEAPS/CC" and "X% away".
- Revises A16 (which over-gated on IN_ZONE only). Awaiting John's sign-off on
  the bucket thresholds + the net-move-vs-1-day trigger.

### C14 — "near 52w high" vs "23.1% off highs" contradiction
The TSM alert (EX-20) said both in one message: `timing_score` called it "near
52w high" while `quality` reported 23.1% off the high. Two different distance
measures with different reference points, printed side by side. At minimum they
should agree; at best one should be removed. Found 2026-08-03.

### C13 — get_earnings_date coverage gaps
`get_earnings_date("AMZN")` returned None for a mega-cap with a known earnings
date (it reported 2026-07-30). Every earnings gate built so far (A15 CC gate,
A17 buffer, A19 convexity warning, the earnings tag) silently no-ops when the
date is missing — failing OPEN, so alerts fire unwarned rather than being
wrongly suppressed. Needs an audit of the source and a fallback. Found
2026-07-31.

### ~~C12~~ — GRADUATED → A21 (fixed 2026-07-31)
### C12 (original) — days_to_earnings clamps past dates to 0 (latent A17 bug)
`quality["days_to_earnings"]` = `max(0, (earnings - now).days)`. A PAST earnings
date becomes 0, which the A17 CC gate reads as "earnings imminent" and
suppresses — backwards, since just-passed earnings is the BEST time to write a
CC (event resolved, IV still elevated). Fix: use the signed delta and treat
negative as "no upcoming earnings." Not yet hit in production (AMZN's date was
None, not past) but it will bite. Found 2026-07-31.

### C8 — "Is today a trading day?" notification gate (P10)
John only wants pings on days when conditions exist: at least one watchlist
name moving ≥5% (or an open-position BIG MOVE). Quiet market → no Telegram at
all, regardless of what the strict filters found. The dashboard can still fill
for browsing; Telegram is the "today matters" channel. Needs decision: does a
rich-but-quiet opportunity (high IVP, no big move today) deserve a ping or not?

---

## Actioned changes (already implemented, traceable to principles)

- **A1 — CRDO CSPs unblocked** (`buckets.csv`, `spreads_only` → FALSE). The flag
  routed CRDO into a dead end (no put-spread scanner integrated; bull-call-spread
  rejects IVP>80). Supports P3. Commit on branch
  `claude/scanner-opportunities-discrepancy-j0ux5p`.
- **A2 — NBIS CSPs unblocked** (same change, same rationale). Supports P3.
- **A3 — Removed at-lows hard-SKIP for Opportunistic-tier CSPs**
  (`csp_engine`). High-IVP setups at the 5-day low were discarded before yield
  was checked; now they surface as WAIT with flags. Supports P4.
- **A4 — Removed the unused Bull Call Spread opportunity scanner** (never
  produced results; decluttered). Not a principle — housekeeping.
- **A5 — Mark-credibility guard scoped to position-feed marks only**
  (`position_management_engine`). Live chain NBBO is now trusted as-is; the
  "deep OTM but low profit ⇒ stale" heuristic applies only to
  `position_mv`/`none` sources. Real P&L now shows on NBIS/CRDO/CLS-style
  deep-OTM puts (EX-2, EX-2b). Guard protection retained for genuinely
  stale-prone marks (validated: T3/T8). Supports P2. Built 2026-07-21 with
  John's go-ahead.
- **A6 — Swing-aware exit alerts** (P6, P15). Three parts: (1) `BIGMOVE_1D`
  0.10 → 0.05, matching John's manual ≥5% daily screen (the +9.57% CLS day
  was missed at 0.10 — EX-5). (2) BIG MOVE reason now STACKS context: P&L
  swing since last scan, earnings ≤7d (flags inside-expiry), take-profit
  level reached — confluence in one message instead of a priority pick.
  (3) New **P&L SWING** action + Telegram alert: fires when a position
  recovered ≥30 points of premium or flipped from ≤−15% loss to breakeven
  since the last scan, even on a quiet day (catches EX-3's "−35% yesterday →
  breakeven today"). P&L history source: the previous scan's committed
  results.json; only credible (chain) marks feed it. Validated 8/8 against
  EX-2/EX-3/EX-5 scenarios. Built 2026-07-21 with John's go-ahead.
- **A7 — Move Watcher** (P10, C4). New `move_watcher.py` + 15-min GitHub
  Actions workflow: Yahoo-quote-only check of watchlist + held-short-option
  names during market hours; ≥5% day moves → one compact Telegram message
  with target/position context; 5%-bucket dedup per ticker/direction/day.
  Zero installation for John — runs in the same cloud as the scanner. No
  Schwab/IBKR usage. Validated: alert content, dedup, escalation, weekend
  guard (4/4 scenarios). Built 2026-07-21 with John's go-ahead.
- **A8 — Watchdog self-heal for scheduled scans.** Run-history analysis
  showed every scheduled scan 60-105 min late + occasional drops (GitHub
  scheduler under load, scanner itself 29/30 healthy). The Move Watcher now
  re-dispatches scanner.yml when an expected slot is >10 min overdue; the
  scanner skips late-arriving schedule duplicates (<100 min freshness).
  Directly serves P10 (fresh data when John sits down to trade) — this was
  why he kept pushing manual scans. Validated 10/10. Built 2026-07-21.
- **A9 — Telegram decision-pressure gate for position alerts** (P17, EX-6).
  `tg_position_alert_worthy()`: BIG MOVE / P&L SWING reach Telegram only if
  P&L SWING, or earnings ≤7d inside expiry, or ≤15% from strike, or credible
  profit ≥60%. Plus once-per-position-per-day dedup via `tg_position_alerts`
  in results.json. Dashboard unchanged — every action still visible there.
  Constants: `TG_POS_MIN_PROFIT` (60), `TG_POS_NEAR_STRIKE` (15). Validated
  9/9 against every logged real case. Built 2026-07-21.
- **A10 — CC/PIO card text shortened (P18); convexity Grades A+B → Telegram.**
  Removed zone-%-of-band and cost-basis lines from CC and PIO card reasoning;
  lead line is now sale price vs John's sell target (✅/⚠ BELOW), then IVP
  only when signal-worthy, then annualized + $/day. Convexity Telegram filter
  widened from Grade A only to A+B (strict mode already keeps these rare;
  cleared the "Grade B convexity → Telegram" backlog item). Built 2026-07-22.
