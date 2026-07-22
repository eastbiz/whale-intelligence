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
- System status: **partially built** — this is the existing BIG MOVE alert.
  Gap identified (see C1): the stale-mark guard hides the real P&L on exactly
  these high-vol names. Pending sign-off.

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

### P12 — LEAPS: low IVP + recent price drop
LEAPS candidates come from the opposite screen as premium selling: stocks with
LOW IVP that dropped recently — buy cheap optionality on quality names after
the fall, sell expensive premium elsewhere.
- Evidence: stated workflow 2026-07-21; sheet shows 99 LEAP entries (MSFT
  after drops, LULU, NVDA...).
- System status: aligned — strategy_allowed blocks LEAPS at elevated/extreme
  IVR and in upper price zones.

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

### P16 — LEAPS are long-term investments, exempt from event-day logic
The deep-ITM LEAPS (e.g. 10× CLS Jan'28 $180) are stock replacement held for
years. Earnings calls don't factor into them — no trimming logic, no P15
confluence application. Event-day exit thinking applies to SHORT premium
positions only.
- Evidence: stated 2026-07-21 in response to Claude's challenge on the CLS
  LEAPS riding through earnings.
- System status: consistent with current behavior (position engine only acts
  on short options). Encoded here so nobody "improves" LEAPS with exit alerts.

---

## Trade Examples (raw log)

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
