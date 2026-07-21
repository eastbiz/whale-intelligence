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
- Evidence: stated workflow (2026-07-21); EX-1 (breakeven exit window).
- System status: **Candidate C2** — swing-aware close framing / loss-reduction
  exit prompts. Needs more examples to define thresholds.

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

---

## Candidate system changes (pending — do NOT implement without go-ahead)

### C1 — Mark-credibility guard misfires on high-IVP names *(highest priority)*
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

### C2 — Swing-aware / loss-reduction close prompts (P6)
Frame BIG MOVE (and maybe a new prompt) around the swing: "this move cut your
cost-to-close from $X to $Y." Consider surfacing loss-reduction exits ("a swing
has cut this loss from −X% to −Y%; close window before it widens"). Needs more
examples to set thresholds.

### C3 — Map CC entry deltas to intent (P5)
Encode "don't want to sell → 0.20–0.25 delta / do want to sell → 0.25–0.30" as
a selectable posture in the CC engine. Needs confirmation of how to expose it.

### C4 — Intraday move-watcher (detection cadence)
The 3×/weekday scan can miss a fast intraday spike entirely (it can fade before
a scan runs). Bigger build — already on the CLAUDE.md backlog. Tracked here
because it directly limits P1 (BIG MOVE can only fire if a scan catches the move).

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
