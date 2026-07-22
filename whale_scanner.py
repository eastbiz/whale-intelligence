"""
Whale Intelligence — Personal Options Trading Scanner
v5 — Full framework implementation:
     Earnings blackout, pullback filter, 200MA, PMCC detection,
     tiered position sizing, IVP, deep ITM LEAPS,
     deal quality checklist, Peter Lynch discovery
"""

import os, json, re, math, time
from datetime import timezone as tz
from zoneinfo import ZoneInfo
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Optional

# ── Phase 1: Bucket-aware options system ─────────────────────
from bucket_config import (
    load_buckets,
    get_bucket,
    get_min_annualized_csp,
    get_min_annualized_cc,
    is_spreads_only,
    is_leaps_only,
    is_cc_only,
    is_watchlist_only,
)

# ── API Keys ────────────────────────────────────────────────
# Pacific Time helper (your local timezone)
PT = ZoneInfo("America/Los_Angeles")

def now_et():
    """Current time in Pacific Time (handles DST automatically)."""
    return datetime.now(tz.utc).astimezone(PT)

UNUSUAL_WHALES_API_KEY = ""  # Removed — no longer used
TELEGRAM_BOT_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID       = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY      = os.environ.get("ANTHROPIC_API_KEY", "")
IBKR_FLEX_TOKEN        = os.environ.get("IBKR_FLEX_TOKEN", "")
IBKR_FLEX_QUERY_ID     = os.environ.get("IBKR_FLEX_QUERY_ID", "")

PORTFOLIO_SIZE = 7_000_000  # fallback — overridden at runtime by live account data

# ── Sizing limits (configurable) ─────────────────────────────
MAX_CSP_ALLOCATION_PCT = 0.25   # max 25% of portfolio in CSP obligations
MAX_CC_COVERAGE_PCT    = 0.50   # max 50% of owned shares covered by calls
# ── BIG MOVE — REVIEW alert (event-driven, replaces gated close alerts) ──
# The MOVE is the only trigger. If you hold a short option on a name that just
# made a big favorable move, you get pinged — decide for yourself whether to
# close. Profit %, strike distance, cost-to-close are shown as CONTEXT in the
# alert, they do NOT gate it.
#   CC (short call)  + stock DROPS big  → call cheap → review to close
#   CSP (short put)  + stock RISES big  → put cheap  → review to close
# No profit floor, no strike-proximity gate. Deep-OTM at 41% (today's NBIS)
# fires just the same as near-strike at a loss.
BIGMOVE_1D             = 0.05   # >=5% move in one day (favorable direction) —
                                # matches John's manual daily screen; 0.10 missed
                                # a +9.57% CLS day that was a real exit window
BIGMOVE_3D             = 0.99   # 3-day trigger OFF by default (set e.g. 0.15 to enable)
# P&L SWING alert: fires when a position's P&L improved sharply since the last
# scan (even if today's underlying move alone is under BIGMOVE_1D). Catches
# "hugely negative yesterday → positive today" across scans.
PNLSWING_MIN_IMPROVE   = 30.0   # >=30 percentage points of premium recovered
PNLSWING_FLIP_FROM     = -15.0  # or: was at least this deep in loss...
PNLSWING_FLIP_TO       = 0.0    # ...and is now at/above breakeven
# ── Telegram gate for position alerts (P17) ──────────────────────────────
# The Move Watcher already pings the NAME once per day when it moves >=5%.
# A POSITION alert additionally reaches Telegram only under DECISION PRESSURE
# (high profit captured / near strike / earnings inside expiry / P&L swing).
# The dashboard always shows every action — this gates Telegram only.
# Calibrated on real reactions: PATH CC 65% profit → wanted; CLS put 11% OTM
# pre-earnings at 32% → wanted; NBIS 52% profit 36% OTM → noise (2026-07-21).
TG_POS_MIN_PROFIT      = 60.0   # profit% that alone justifies a close ping
TG_POS_NEAR_STRIKE     = 15.0   # dist-to-strike% that makes any move relevant

# ── Schwab account number → label mapping ────────────────────
# Format: last 4 digits or full number (dashes optional)
# Update CRT number once confirmed
SCHWAB_ACCOUNT_LABELS = {
    "17860185": "IRA",       # Schwab IRA      1786-0185
    "52644501": "CRT",       # Schwab CRT      5264-4501
    "62969383": "Personal",  # Schwab Personal 6296-9383
}

# ── Canonical tier target ranges (used everywhere) ───────────
# Single source of truth — do not duplicate below
TARGET_RANGES = {
    "Core":          (5.0, 10.0),
    "Growth":        (3.0,  6.0),
    "Cyclical":      (2.0,  5.0),
    "Opportunistic": (1.0,  3.0),
}
# Canonical tier allocations: (normal_max, hard_max)
# normal_max = On Target ceiling, hard_max = Overweight trigger
TIER_ALLOCATIONS = {
    "Core":          (0.08, 0.12),
    "Growth":        (0.05, 0.08),
    "Cyclical":      (0.04, 0.06),
    "Opportunistic": (0.02, 0.04),
}
# Keep TIER_MAX_PCT as alias for backward compat
TIER_MAX_PCT = {k: v[1] for k, v in TIER_ALLOCATIONS.items()}

# ── Per-ticker allocation targets (updated Apr 17 2026) ──────
# target_pct: desired portfolio weight
# On Target zone: 80%–120% of target_pct (±20% tolerance band)
# Speculative tickers at 0% show "Not Held" rather than "Underweight"
TICKER_TARGETS = {
    # Core
    "AAPL":  {"target_pct":  8.0, "speculative": False},
    "AMZN":  {"target_pct": 10.0, "speculative": False},
    "GOOGL": {"target_pct": 10.0, "speculative": False},
    "IBKR":  {"target_pct":  5.0, "speculative": False},
    "MELI":  {"target_pct":  5.0, "speculative": False},
    "MSFT":  {"target_pct":  6.0, "speculative": False},
    "NOW":   {"target_pct":  4.0, "speculative": False},
    "NVDA":  {"target_pct": 12.0, "speculative": False},
    "TSM":   {"target_pct":  6.0, "speculative": False},
    # Trading
    "CRDO":  {"target_pct":  3.0, "speculative": False},
    "FIX":   {"target_pct":  3.0, "speculative": False},
    "MU":    {"target_pct":  4.0, "speculative": False},
    "NFLX":  {"target_pct":  4.0, "speculative": False},
    "PLTR":  {"target_pct":  4.0, "speculative": False},
    "TSLA":  {"target_pct":  4.0, "speculative": False},
    # Speculative — "Not Held" when at 0%, no BUY pressure
    "CLS":   {"target_pct":  1.0, "speculative": True},
    "GRBK":  {"target_pct":  1.0, "speculative": True},
    "IBIT":  {"target_pct":  0.0, "speculative": True},
    "KNX":   {"target_pct":  0.0, "speculative": True},
    "LULU":  {"target_pct":  1.0, "speculative": True},
    "NBIS":  {"target_pct":  2.0, "speculative": True},
    "NVO":   {"target_pct":  2.0, "speculative": True},
    "POWL":  {"target_pct":  1.0, "speculative": True},
    # Very Speculative (added 2026-06-22)
    "UBER":  {"target_pct":  1.0, "speculative": True},
    "GRAB":  {"target_pct":  1.0, "speculative": True},
    "PATH":  {"target_pct":  0.0, "speculative": True},  # NO BUY
    "MSTR":  {"target_pct":  0.0, "speculative": True},  # NO BUY — exit only
    "PYPL":  {"target_pct":  1.0, "speculative": True},
    "SPCX":  {"target_pct":  1.0, "speculative": True},
}
TICKER_TOLERANCE = 0.20  # ±20% relative band around target_pct

# ── Strategy parameters (from framework doc) ────────────────
CSP_DTE_MIN           = 30;   CSP_DTE_MAX     = 45
CSP_MIN_DTE           = 30;   CSP_MAX_DTE     = 45   # aliases
CSP_DELTA_PLTR_MIN    = 0.20; CSP_DELTA_PLTR_MAX = 0.25  # PLTR stricter
CSP_DELTA_HIGH_IVP_MAX= 0.35  # allowed when IVP > 50
CC_DTE_MIN            = 30;   CC_DTE_MAX      = 45
LEAPS_DTE_MIN         = 500   # 2+ years
# CSP: default delta 0.25-0.30, up to 0.35 only when IVP > 50
CSP_DELTA_MIN         = 0.25; CSP_DELTA_MAX   = 0.35  # target 0.25-0.30, hard max 0.35
CSP_DELTA_MAX_HIGH_IV = 0.35  # allowed when IVP > 50
CC_DELTA_MIN          = 0.20; CC_DELTA_MAX    = 0.30  # target range per doc
CC_DELTA_HARD_MAX     = 0.35  # absolute ceiling for income CC
CC_DELTA_OW_MAX       = 0.50  # overweight positions — allow closer strikes
LEAPS_DELTA_MIN       = 0.75; LEAPS_DELTA_MAX = 0.99  # max removed — BE% and extrinsic% are the real gates
CSP_MIN_ANNUALIZED    = 20.0  # preferred minimum (high vol stocks)
CC_MIN_ANNUALIZED     = 8.0   # lowered — stable core names rarely give 15%
MAX_ANNUALIZED        = 120.0 # cap bad data
IVP_MIN_SELL          = 30    # floor — skip below 30
IVP_ELEVATED          = 50    # "elevated" — allow wider delta, flag as excellent
IVP_MAX_BUY           = 50    # max IVP to buy LEAPS
LEAPS_EXTRINSIC_MAX   = 20.0  # 20% max extrinsic — primary cost filter for LEAPS — target <20%
# ── Cheap Convexity LEAPS (far-OTM long-dated calls, asymmetric upside) ──
# STRICT MODE (default). Main view shows ONLY trades passing ALL hard filters.
# Anything failing → "Near Misses / Diagnostics" (hidden). Zero results is OK.
# Distinct from deep-ITM stock-replacement LEAPS above.
CVX_DTE_MIN           = 700    # HARD floor — reject below 700 in main view
CVX_DTE_PREF_MIN      = 700    # preferred window starts
CVX_DTE_MAX           = 1100   # preferred/acceptable ceiling
CVX_FETCH_DAYS        = 1150   # chain fetch horizon (covers DTE_MAX + buffer)
# ── HARD FILTERS (all must pass for main view) ──
CVX_CAGR_MAX          = 0.25   # Required CAGR hard max (≤25%)
CVX_CAGR_PREF         = 0.20   # preferred (<20%)
CVX_SCORE_MIN         = 20     # Convexity Score hard min (≥20)
CVX_SCORE_PREF        = 25     # preferred (>25)
CVX_SCORE_EXC         = 30     # exceptional (>30)
CVX_PREM_MAX          = 0.12   # Premium % hard max (≤12%)
CVX_PREM_PREF         = 0.10   # preferred (<10%)
CVX_PREM_EXC          = 0.08   # exceptional (<8%)
CVX_COV30_MIN         = 1.05   # Coverage@30% hard min (≥1.05)
CVX_COV30_PREF        = 1.15   # preferred (>1.15)
CVX_STRIKE_PCT_MIN    = 1.25   # Strike % hard min (≥125%)
CVX_STRIKE_PCT_PREF_LO= 1.40   # preferred band 140%–220%
CVX_STRIKE_PCT_PREF_HI= 2.20
CVX_BURDEN_MAX        = 0.05   # Annualized Premium Burden hard max (<5%/yr)
CVX_BURDEN_PREF       = 0.03   # preferred (<3%/yr)
CVX_SPREAD_MAX        = 0.15   # bid/ask spread hard ceiling (<15%)
CVX_SPREAD_PREF       = 0.10   # preferred (<10%)
CVX_OI_MIN            = 50     # hard min (≥50); below 50 hidden unless manual
CVX_OI_PREF           = 100    # preferred (>100)
# Scenario CAGRs for breakeven coverage
CVX_SCENARIOS         = [0.20, 0.25, 0.30]  # spec display fields: 20/25/30%
CVX_SCENARIOS_AGGR    = [0.40, 0.50]        # hypergrowth / bitcoin-linked names
CVX_AGGR_TICKERS      = {"IBIT", "MSTR", "PLTR", "NBIS"}
# Earnings filter: <14 days = hard stop, 14-21 = warning, >21 = normal
EARNINGS_HARD_STOP    = 14    # hard stop
EARNINGS_WARNING      = 21    # warning label
# Price location: >15% below 52w high = preferred, 8-15% = caution, <8% = skip CSP
NEAR_HIGH_SKIP        = 0.08  # skip CSP if within 8% of 52w high
NEAR_HIGH_CAUTION     = 0.15  # caution if 8-15% below high
PULLBACK_MIN          = 0.15  # preferred zone starts here
PULLBACK_MAX          = 0.65  # not >65% (company may be broken)
# MA filters
MA50_EXTENDED         = 0.08  # skip CSP if >8% above 50-day MA
# ── Gap / spike / drop thresholds ───────────────────────
GAP_RISK_PCT          = 0.08  # Income mode: skip if moved >8% today
GAP_RISK_PCT_OPP      = 0.20  # Opportunistic mode: allow up to 20% move

# Mode 2: Post-Spike CC — triggered BY upward gaps
OPP_SPIKE_MIN         = 0.08  # minimum upward spike to trigger
OPP_SPIKE_DAYS        = 3
OPP_CC_DTE_MIN        = 14
OPP_CC_DTE_MAX        = 30
OPP_CC_DELTA_MIN      = 0.25  # post-spike: slightly aggressive ok
OPP_CC_DELTA_MAX      = 0.40  # post-spike hard max per doc
OPP_IVP_MIN           = 40
OPP_EARNINGS_MIN      = 7

# ── Per-symbol income trade settings ────────────────────────────────────────
# Source: Positions_Buy_Sell_Delta.xlsx (Income Trades sheet) — updated Apr 17 2026
# buy_under:  max effective entry for CSP (effective_entry <= buy_under * 1.03 hard gate)
# sell_above: min effective exit for CC  (strike + premium >= sell_above hard gate)
# Delta ranges are hard filters applied per strategy.
SYMBOL_SETTINGS = {
    # ── CORE ─────────────────────────────────────────────────────────────────
    # Updated 2026-07-19 per user table — current price targets reflecting recent run-up.
    # buy_under=0 means NO BUY (price way above target, only CC monitoring).
    "AAPL": {"buy_under":    0, "sell_above":  360, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.28, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "AMZN": {"buy_under":  220, "sell_above":  270, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.28, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "GOOGL":{"buy_under":  300, "sell_above":  450, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.28, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "IBKR": {"buy_under":   70, "sell_above":  110, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.28, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "MELI": {"buy_under": 1560, "sell_above": 1800, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "MSFT": {"buy_under":  345, "sell_above":  500, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.28, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "NOW":  {"buy_under":   90, "sell_above":  125, "csp_delta_min": 0.20, "csp_delta_max": 0.25, "cc_delta_min": 0.20, "cc_delta_max": 0.25, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "NVDA": {"buy_under":  180, "sell_above":  225, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.28, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "TSM":  {"buy_under":  320, "sell_above":  450, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    # ── TRADING ──────────────────────────────────────────────────────────────
    "CRDO": {"buy_under":  200, "sell_above":  300, "csp_delta_min": 0.20, "csp_delta_max": 0.28, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "FIX":  {"buy_under": 1400, "sell_above": 2200, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "MU":   {"buy_under":  450, "sell_above": 1400, "csp_delta_min": 0.20, "csp_delta_max": 0.28, "cc_delta_min": 0.20, "cc_delta_max": 0.28, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "NFLX": {"buy_under":    0, "sell_above":   90, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.32, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "PLTR": {"buy_under":   85, "sell_above":  160, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "TSLA": {"buy_under":  340, "sell_above":  450, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    # ── SPECULATIVE ──────────────────────────────────────────────────────────
    "CLS":  {"buy_under":  275, "sell_above":  400, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "GRBK": {"buy_under":   63, "sell_above":   85, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "IBIT": {"buy_under":    0, "sell_above":   46, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "KNX":  {"buy_under":   55, "sell_above":    0, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "LULU": {"buy_under":  105, "sell_above":  150, "csp_delta_min": 0.20, "csp_delta_max": 0.28, "cc_delta_min": 0.20, "cc_delta_max": 0.28, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "NBIS": {"buy_under":  150, "sell_above":  280, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "NVO":  {"buy_under":   38, "sell_above":   60, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.28, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "POWL": {"buy_under":  210, "sell_above":  400, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    # ── VERY SPECULATIVE (added 2026-06-22) ──────────────────────────────────
    "UBER": {"buy_under":   65, "sell_above":   95, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "GRAB": {"buy_under":  3.1, "sell_above":  4.5, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "PATH": {"buy_under":    0, "sell_above": 13.0, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "MSTR": {"buy_under":    0, "sell_above":  200, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "PYPL": {"buy_under":   35, "sell_above":   75, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
    "SPCX": {"buy_under":  100, "sell_above":  150, "csp_delta_min": 0.20, "csp_delta_max": 0.30, "cc_delta_min": 0.20, "cc_delta_max": 0.30, "leaps_delta_min": 0.75, "leaps_delta_max": 0.99},
}

# ── Phase 1: Load bucket assignments ──
# Buckets define spreads_only / leaps_only / cc_only flags and bucket-aware
# annualized return minimums (A: 12%, B: 18%, C: 28%, D: 40%).
# Falls back to per-symbol SYMBOL_SETTINGS for everything else.
BUCKETS = load_buckets("buckets.csv")

# ── Feature flag: Position Income Optimization (PIO) ─────────
# PIO uses relaxed rules (loss positions allowed, lower delta, etc.) and
# generates a lot of noise. Set to False to suppress all PIO cards from
# dashboard and Telegram. Only zone-first CCs will surface.
# Toggle to True to re-enable PIO scanning.
ENABLE_PIO = False

# ── Feature flag: Strict zone-only Telegram ──────────────────
# When True: Telegram alerts ONLY fire for opportunities where the stock's
# current price has reached the actionable zone (CSP: price ≤ buy_under,
# CC: price ≥ sell_above, LEAPS: price ≤ buy_under × 1.10), OR an IVR
# override applies (IVR ≥ 70 for CSP/CC, IVR ≤ 25 for LEAPS, with data
# reliability guards). Dashboard has a separate UI toggle.
# Default False — keeps current Telegram behavior.
STRICT_ZONE_TELEGRAM = False


def compute_in_zone(strategy: str, price: float, buy_under: float, sell_above: float,
                    ivr: float, atm_iv: float) -> tuple:
    """
    Determine if an opportunity is in its actionable zone.

    Returns (in_zone: bool, reason: str).

    Zones:
      CSP:   price ≤ buy_under  (stock is in BUY zone)
      CC:    price ≥ sell_above (stock is at SELL target)
      LEAPS: price ≤ buy_under × 1.10 (stock near BUY zone — stock replacement entry)

    IVR override (relaxes zone gate when premium environment is exceptional):
      CSP/CC: IVR ≥ 70 → premium pays enough to justify out-of-zone trade
      LEAPS:  IVR ≤ 25 → premium so cheap that LEAPS make sense even mid-band

    Reliability guard for IVR override: requires atm_iv ≥ 0.15 (15%) to confirm
    the IVR value isn't a phantom/stale reading. Stale weekend IVP showing 7%
    won't satisfy this guard.
    """
    iv_reliable = atm_iv >= 0.15
    if strategy == "CSP":
        if buy_under <= 0:
            return (False, f"NO BUY (buy_under=0)")
        if price <= buy_under:
            return (True, f"Price ${price:.2f} ≤ Buy Below ${buy_under:.0f} — IN BUY ZONE")
        if iv_reliable and ivr >= 70:
            return (True, f"OUT of zone but IVR {ivr:.0f} override (premium pays)")
        gap = (price - buy_under) / buy_under * 100
        return (False, f"Stock ${price:.2f} is {gap:.1f}% above Buy Below ${buy_under:.0f}")
    if strategy == "CC":
        if sell_above <= 0:
            return (False, f"No sell target set")
        if price >= sell_above:
            return (True, f"Price ${price:.2f} ≥ Sell Above ${sell_above:.0f} — IN SELL ZONE")
        if iv_reliable and ivr >= 70:
            return (True, f"OUT of zone but IVR {ivr:.0f} override (premium pays)")
        gap = (sell_above - price) / sell_above * 100
        return (False, f"Stock ${price:.2f} is {gap:.1f}% below Sell Above ${sell_above:.0f}")
    if strategy == "LEAPS":
        if buy_under <= 0:
            return (False, f"NO BUY (buy_under=0)")
        threshold = buy_under * 1.10
        if price <= threshold:
            return (True, f"Price ${price:.2f} ≤ ${threshold:.0f} (Buy Below ×1.10) — NEAR BUY ZONE")
        if iv_reliable and ivr <= 25:
            return (True, f"OUT of zone but IVR {ivr:.0f} override (premium cheap)")
        gap = (price - threshold) / threshold * 100
        return (False, f"Stock ${price:.2f} is {gap:.1f}% above LEAPS entry zone ${threshold:.0f}")
    return (False, "unknown strategy")

# Speculative tickers — smaller position sizing, wider OTM buffers.
# Suppressed from Telegram CSP entry alerts (entries only on deliberate decision).
# CC alerts still sent when approaching sell_above (useful for existing positions).
SPECULATIVE_TICKERS = {"CLS", "GRBK", "IBIT", "KNX", "LULU", "NBIS", "NVO", "POWL",
                        "UBER", "GRAB", "PATH", "MSTR", "PYPL", "SPCX"}

# Mode 3: Post-Drop CSP — triggered BY downward drops
DROP_TRIGGER_MIN      = 0.08  # minimum drop to trigger (8-12%+)
DROP_CSP_DTE_MIN      = 25    # DTE range
DROP_CSP_DTE_MAX      = 45
DROP_CSP_DELTA_MIN    = 0.20  # post-drop: conservative
DROP_CSP_DELTA_MAX    = 0.25  # tighter range — post-drop is higher risk
DROP_IVP_MIN          = 40    # preferred >50
DROP_EARNINGS_MIN     = 7     # hard stop
DROP_SIZE_FACTOR      = 0.60  # 50-70% of normal position size

# Quality tiers allowed for post-drop CSP
DROP_CSP_ALLOWED_TIERS = {"Core", "Growth"}
# Liquidity requirements
MIN_OPEN_INTEREST     = 1000
MIN_DAILY_VOLUME      = 100
MAX_BID_ASK_SPREAD    = 0.05  # 5%
# Premium efficiency: minimum premium as % of strike
MIN_PREMIUM_PCT_30_45 = 0.015 # 1.5% for 30-45 DTE
MIN_PREMIUM_PCT_45_60 = 0.020 # 2.0% for 45-60 DTE
# Sector exposure cap
MAX_SECTOR_PCT        = 0.25  # 25% max per sector

# ── Stock universe defined below at canonical location ──────
# (see CORE_STOCKS / GROWTH_STOCKS / CYCLICAL_STOCKS / OPPORTUNISTIC_STOCKS)


# ── Schwab API ───────────────────────────────────────────
SCHWAB_APP_KEY       = os.environ.get("SCHWAB_APP_KEY", "")
SCHWAB_APP_SECRET    = os.environ.get("SCHWAB_APP_SECRET", "")
SCHWAB_REFRESH_TOKEN = os.environ.get("SCHWAB_REFRESH_TOKEN", "")
SCHWAB_ACCESS_TOKEN  = os.environ.get("SCHWAB_ACCESS_TOKEN", "")
SCHWAB_TOKEN_PATH    = os.environ.get("SCHWAB_TOKEN_PATH", "schwab_token.json")
SCHWAB_BASE          = "https://api.schwabapi.com"
SCHWAB_MARKET_BASE   = f"{SCHWAB_BASE}/marketdata/v1"
SCHWAB_TRADER_BASE   = f"{SCHWAB_BASE}/trader/v1"

# Use schwab-py for token management when token file exists (Windows local mode)
_schwab_py_client = None
def get_schwab_py_client():
    """Get schwab-py client if token file exists — handles refresh automatically."""
    global _schwab_py_client
    if _schwab_py_client:
        return _schwab_py_client
    if not os.path.exists(SCHWAB_TOKEN_PATH):
        return None
    try:
        import schwab
        _schwab_py_client = schwab.auth.easy_client(
            api_key=SCHWAB_APP_KEY,
            app_secret=SCHWAB_APP_SECRET,
            callback_url="https://127.0.0.1:8182",
            token_path=SCHWAB_TOKEN_PATH,
            interactive=False
        )
        print("   ✅ schwab-py client loaded from token file")
        return _schwab_py_client
    except Exception as e:
        print(f"   ⚠️ schwab-py client error: {e}")
        return None

_schwab_cache = {"access_token": "", "expires_at": 0}


def schwab_get_token() -> str:
    """Auto-refresh Schwab access token using refresh token."""
    import time as _time, base64
    c = _schwab_cache
    # Use cached token if still valid
    if c["access_token"] and _time.time() < c["expires_at"] - 60:
        return c["access_token"]
    # Try to refresh
    if not SCHWAB_REFRESH_TOKEN:
        return SCHWAB_ACCESS_TOKEN  # fall back to stored
    try:
        creds = base64.b64encode(f"{SCHWAB_APP_KEY}:{SCHWAB_APP_SECRET}".encode()).decode()
        r = requests.post(
            f"{SCHWAB_BASE}/v1/oauth/token",
            headers={"Authorization": f"Basic {creds}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "refresh_token", "refresh_token": SCHWAB_REFRESH_TOKEN},
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            c["access_token"] = data["access_token"]
            c["expires_at"]   = _time.time() + data.get("expires_in", 1800)
            print("   ✅ Schwab token refreshed")
            return c["access_token"]
        else:
            if r.status_code == 400:
                print(f"   ⚠️ Schwab refresh token expired (400)")
                print(f"   ℹ️  Run schwab_test.py on Windows, then update SCHWAB_REFRESH_TOKEN in GitHub Secrets")
            else:
                print(f"   ⚠️ Schwab token refresh failed ({r.status_code}) — using stored token")
            return SCHWAB_ACCESS_TOKEN
    except Exception as e:
        print(f"   ⚠️ Schwab token error: {e}")
        return SCHWAB_ACCESS_TOKEN


def schwab_headers() -> dict:
    """Get auth headers — uses schwab-py client if available, else manual refresh."""
    py_client = get_schwab_py_client()
    if py_client:
        # schwab-py manages token internally — extract the token
        try:
            token = py_client.session.token["access_token"]
            return {"Authorization": f"Bearer {token}"}
        except:
            pass
    token = schwab_get_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def schwab_get_quotes(tickers: list) -> dict:
    """Real-time quotes: price, 52w range, volume, prev close."""
    if not SCHWAB_APP_KEY:
        return {}
    try:
        r = requests.get(
            f"{SCHWAB_MARKET_BASE}/quotes",
            headers=schwab_headers(),
            params={"symbols": ",".join(tickers), "fields": "quote,fundamental"},
            timeout=15
        )
        if r.status_code != 200:
            print(f"   Schwab quotes error: {r.status_code}")
            return {}
        result = {}
        for ticker, info in r.json().items():
            q = info.get("quote", {})
            f = info.get("fundamental", {})
            # ivPercentile is in the quote object for equities
            _ivp_raw = (q.get("volatility", 0) or
                        q.get("impliedYield", 0) or 0)
            # Print raw fundamental keys on first ticker to debug
            if not result:
                print(f"   Schwab quote keys for {ticker}: q={list(q.keys())[:10]} f={list(f.keys())[:10]}")
            result[ticker] = {
                "price":       float(q.get("lastPrice",  q.get("mark", 0)) or 0),
                "week52_high": float(q.get("52WeekHigh", 0) or 0),
                "week52_low":  float(q.get("52WeekLow",  0) or 0),
                "avg_volume":  int(q.get("totalVolume",  0) or 0),
                "prev_close":  float(q.get("closePrice", 0) or 0),
                "pe_ratio":    float(f.get("peRatio",    0) or 0),
                "_raw_quote":  q,   # keep raw so we can find IVP field
            }
        print(f"   Schwab quotes: {len(result)}/{len(tickers)} ✓")
        return result
    except Exception as e:
        print(f"   Schwab quotes exception: {e}")
        return {}


def schwab_get_option_chain(ticker: str, from_date: str, to_date: str) -> list:
    """
    Real option chain with TRUE delta, IV, OI, theta from Schwab.
    Replaces Yahoo Finance + Black-Scholes approximation entirely.
    """
    if not SCHWAB_APP_KEY:
        return []
    try:
        r = requests.get(
            f"{SCHWAB_MARKET_BASE}/chains",
            headers=schwab_headers(),
            params={
                "symbol":       ticker,
                "contractType": "ALL",
                "strikeCount":  50,  # wider range needed for deep ITM LEAPS on high-price stocks
                "includeUnderlyingQuote": True,
                "fromDate":     from_date,
                "toDate":       to_date,
                "optionType":   "S",
            },
            timeout=15
        )
        if r.status_code != 200:
            print(f"   Schwab chain {ticker}: {r.status_code}")
            return []
        data      = r.json()
        contracts = []
        price     = float(data.get("underlyingPrice", 0) or 0)
        # Schwab chain-level fields:
        # "volatility" = current ATM IV of the chain (annualized %) — THIS is what Schwab returns
        # "ivPercentile" = Schwab does NOT reliably return this field
        # We store the current IV and compute IVP from per-contract IVs in calculate_ivp
        _ivp  = float(data.get("ivPercentile", 0) or 0)  # only set if Schwab returns it
        _iv   = float(data.get("volatility", 0) or 0) / 100  # current ATM IV as decimal
        _chain_meta = {"_ivp": _ivp, "_iv_current": _iv}
        for opt_type, map_key in [("P","putExpDateMap"),("C","callExpDateMap")]:
            for exp_str, strikes in data.get(map_key, {}).items():
                exp_date = exp_str.split(":")[0]
                for strike_str, opts in strikes.items():
                    for opt in opts:
                        c = {
                            "option_symbol":   opt.get("symbol",""),
                            "strike":          float(strike_str),
                            "expiry":          exp_date,
                            "option_type":     opt_type,
                            "nbbo_bid":        float(opt.get("bid",           0) or 0),
                            "nbbo_ask":        float(opt.get("ask",           0) or 0),
                            "iv":              float(opt.get("volatility",     0) or 0) / 100,
                            "delta":           float(opt.get("delta",         0) or 0),
                            "theta":           float(opt.get("theta",         0) or 0),
                            "gamma":           float(opt.get("gamma",         0) or 0),
                            "open_interest":   int(opt.get("openInterest",    0) or 0),
                            "volume":          int(opt.get("totalVolume",     0) or 0),
                            "underlying_price": price,
                            "_chain_ivp":      _ivp,
                            "_chain_iv":       _iv,
                        }
                        contracts.append(c)
        # Debug IVP source — only print first time to avoid log spam
        if _ivp == 0 and _iv > 0:
            print(f"   Schwab chain {ticker}: {len(contracts)} contracts ✓ | ATM IV={_iv*100:.1f}% (IVP computed from chain)")
        elif _ivp > 0:
            print(f"   Schwab chain {ticker}: {len(contracts)} contracts ✓ | IVP={_ivp:.1f}%")
        else:
            print(f"   Schwab chain {ticker}: {len(contracts)} contracts ✓")
        return contracts
    except Exception as e:
        print(f"   Schwab chain {ticker} error: {e}")
        return []


def schwab_get_ivp(ticker: str) -> float:
    """
    Calculate real IV Percentile from 1-year Schwab price history.
    Much more accurate than our current ATM contract proxy.
    """
    if not SCHWAB_APP_KEY:
        return 0
    try:
        from datetime import timedelta
        end   = datetime.now()
        start = end - timedelta(days=365)
        r = requests.get(
            f"{SCHWAB_MARKET_BASE}/pricehistory",
            headers=schwab_headers(),
            params={
                "symbol":        ticker,
                "periodType":    "year",
                "period":        1,
                "frequencyType": "daily",
                "frequency":     1,
                "startDate":     int(start.timestamp() * 1000),
                "endDate":       int(end.timestamp()   * 1000),
            },
            timeout=15
        )
        if r.status_code != 200:
            print(f"   schwab_get_ivp {ticker}: HTTP {r.status_code}")
            return 0
        candles = r.json().get("candles", [])
        if len(candles) < 30:
            print(f"   schwab_get_ivp {ticker}: only {len(candles)} candles")
            return 0
        closes  = [c["close"] for c in candles if c.get("close")]
        returns = [(closes[i]-closes[i-1])/closes[i-1] for i in range(1,len(closes))]
        vols    = []
        for i in range(20, len(returns)):
            w    = returns[i-20:i]
            mean = sum(w)/20
            var  = sum((x-mean)**2 for x in w)/20
            vols.append((var**0.5)*(252**0.5))
        if not vols:
            return 0
        curr = vols[-1]
        return round(sum(1 for v in vols if v < curr)/len(vols)*100, 1)
    except Exception as _e:
        print(f"   schwab_get_ivp {ticker} error: {_e}")
        return 0


def schwab_parse_positions(accounts: list) -> dict:
    """
    Parse Schwab accounts into stock AND option holdings.
    Stock:  {ticker: {quantity, avg_cost, market_value, asset_class="STK"}}
    Option: {opt_key: {underlying, put_call, strike, expiry, quantity,
                       side, avg_cost, market_value, asset_class="OPT", source="schwab"}}
    Aggregates across all Schwab accounts (IRA, CRT, Personal).
    """
    holdings = {}
    for acc in accounts:
        acc_type   = acc.get("account_type", "")
        acc_id_raw = acc.get("account_id", "")
        # Resolve human label: strip dashes, look up in SCHWAB_ACCOUNT_LABELS
        acc_id_key = acc_id_raw.replace("-","").replace(" ","")
        acc_label  = (SCHWAB_ACCOUNT_LABELS.get(acc_id_key)
                   or SCHWAB_ACCOUNT_LABELS.get(acc_id_key[-8:] if len(acc_id_key)>=8 else acc_id_key)
                   or acc_type  # fallback to MARGIN/CASH if not in map
                   or "Schwab")
        print(f"   Schwab account: id={acc_id_raw} type={acc_type} -> label={acc_label}")
        for pos in acc.get("positions", []):
            inst  = pos.get("instrument", {})
            asset = inst.get("assetType", "")
            ticker_dbg = inst.get("symbol","")
            if asset not in ("EQUITY","ETF","COLLECTIVE_INVESTMENT","OPTION","FIXED_INCOME","MUTUAL_FUND"):
                print(f"     Unknown assetType: {asset!r} for {ticker_dbg!r}")

            # ── Stocks / ETFs / Preferred / REITs ──────────────
            if asset in ("EQUITY", "ETF", "COLLECTIVE_INVESTMENT",
                         "FIXED_INCOME", "MUTUAL_FUND"):
                ticker = inst.get("symbol", "").replace("/", "-")
                qty    = float(pos.get("longQuantity", 0) or 0)
                # Also check shortQuantity for short stock positions
                short_stk_qty = float(pos.get("shortQuantity", 0) or 0)
                if qty <= 0 and short_stk_qty <= 0:
                    print(f"     Skipping {inst.get('symbol','')} ({asset}): longQty={qty} shortQty={short_stk_qty}")
                    continue
                if qty <= 0 and short_stk_qty > 0:
                    qty = short_stk_qty  # short stock position
                avg  = float(pos.get("averagePrice", 0) or 0)
                mval = float(pos.get("marketValue",  0) or 0)
                if ticker in holdings and holdings[ticker].get("asset_class") == "STK":
                    holdings[ticker]["quantity"]     += qty
                    holdings[ticker]["market_value"] += mval
                    # Track per-account breakdown
                    _by_acct = holdings[ticker].setdefault("mv_by_account", {})
                    _by_acct[acc_label] = _by_acct.get(acc_label, 0) + mval
                    # Use the account with the largest holding as primary label
                    holdings[ticker]["account_type"] = max(_by_acct, key=_by_acct.get)
                else:
                    holdings[ticker] = {
                        "quantity":     qty,
                        "avg_cost":     avg,
                        "market_value": mval,
                        "account_type": acc_label,
                        "mv_by_account":{acc_label: mval},
                        "asset_class":  "STK",
                    }

            # ── Options (short puts = CSP, short calls = CC) ───
            elif asset == "OPTION":
                import re as _re
                opt_sym    = inst.get("symbol", "")
                underlying = (inst.get("underlyingSymbol", "")
                              .replace("/", "-").replace("BRK B", "BRK-B").strip())
                put_call   = inst.get("putCall", "").upper()
                strike     = float(inst.get("strikePrice", 0) or 0)
                expiry_raw = inst.get("expirationDate", "")

                # Schwab often leaves strikePrice=0 and expirationDate='' — parse from OCC symbol
                if (strike == 0 or not expiry_raw) and opt_sym:
                    _occ = opt_sym.replace(" ", "")
                    _m = _re.match(r'^([A-Z]+)(\d{6})([CP])(\d{8})$', _occ)
                    if _m:
                        _und, _date6, _pc, _strike8 = _m.groups()
                        if not underlying: underlying = _und
                        if not put_call:   put_call   = "PUT" if _pc == "P" else "CALL"
                        if strike == 0:    strike     = float(_strike8) / 1000.0
                        if not expiry_raw: expiry_raw = "20" + _date6  # YYYYMMDD

                # shortQuantity > 0 means we sold this option (CSP or CC)
                short_qty = float(pos.get("shortQuantity", 0) or 0)
                long_qty  = float(pos.get("longQuantity",  0) or 0)
                qty = short_qty if short_qty > 0 else long_qty
                if qty <= 0 or not underlying or strike == 0:
                    continue

                side    = "Short" if short_qty > 0 else "Long"
                avg     = float(pos.get("averagePrice", 0) or 0)
                mval    = float(pos.get("marketValue",  0) or 0)
                # Normalise to single char P/C to match IBKR convention
                pc_norm = "P" if "PUT" in put_call else "C" if "CALL" in put_call else put_call[:1]
                # YYYYMMDD format to match IBKR
                expiry_fmt = expiry_raw.replace("-", "") if expiry_raw else ""

                key = opt_sym or f"{underlying}_{pc_norm}_{strike}_{expiry_fmt}"
                holdings[key] = {
                    "underlying":    underlying,
                    "put_call":      pc_norm,
                    "strike":        strike,
                    "expiry":        expiry_fmt,
                    "quantity":      qty,
                    "avg_cost":      avg,
                    "market_value":  mval,
                    "side":          side,
                    "account_type":  acc_label,
                    "asset_class":   "OPT",
                    "source":        "schwab",
                }

    short_puts  = sum(1 for v in holdings.values()
                      if v.get("asset_class") == "OPT"
                      and v.get("side") == "Short" and v.get("put_call") == "P")
    short_calls = sum(1 for v in holdings.values()
                      if v.get("asset_class") == "OPT"
                      and v.get("side") == "Short" and v.get("put_call") == "C")
    stk_count   = sum(1 for v in holdings.values() if v.get("asset_class") == "STK")
    if short_puts + short_calls > 0:
        print(f"   Schwab options parsed: {short_puts} short puts (CSP), {short_calls} short calls (CC)")
    return holdings


def schwab_get_accounts() -> list:
    """Fetch all Schwab accounts with balances and positions."""
    if not SCHWAB_APP_KEY:
        return []
    try:
        r = requests.get(
            f"{SCHWAB_TRADER_BASE}/accounts",
            headers=schwab_headers(),
            params={"fields": "positions"},
            timeout=15
        )
        if r.status_code != 200:
            print(f"   Schwab accounts: {r.status_code}")
            return []
        result = []
        for acc in r.json():
            sec = acc.get("securitiesAccount", {})
            bal = sec.get("currentBalances", {})
            result.append({
                "account_id":      sec.get("accountNumber",""),
                "account_type":    sec.get("type",""),
                "buying_power":    float(bal.get("buyingPower",       0) or 0),
                "cash":            float(bal.get("cashBalance",        0) or 0),
                "net_liquidation": float(bal.get("liquidationValue",   0) or 0),
                "positions":       sec.get("positions", []),
            })
        print(f"   Schwab accounts: {len(result)} ✓")
        for _acc in result:
            _pos_count = len(_acc.get("positions", []))
            _acct_id   = _acc.get("account_id","")
            _acct_type = _acc.get("account_type","")
            _stk = sum(1 for p in _acc.get("positions",[])
                       if p.get("instrument",{}).get("assetType") in ("EQUITY","ETF","COLLECTIVE_INVESTMENT"))
            _opt = sum(1 for p in _acc.get("positions",[])
                       if p.get("instrument",{}).get("assetType") == "OPTION")
            print(f"     Account {_acct_id} ({_acct_type}): {_pos_count} positions ({_stk} stocks, {_opt} options)")
        return result
    except Exception as e:
        print(f"   Schwab accounts error: {e}")
        return []


# ════════════════════════════════════════════════════════════
# MARKET DATA
# ════════════════════════════════════════════════════════════

def get_market_data(tickers: list) -> dict:
    """Price, 52w range, volume, earnings date, 200-day MA proxy."""
    data = {}
    for ticker in tickers:
        yf = ticker.replace("BRK.B","BRK-B")
        try:
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{yf}"
                f"?range=1y&interval=1d",
                headers={"User-Agent":"Mozilla/5.0"}, timeout=12
            )
            j    = r.json()["chart"]["result"][0]
            meta = j["meta"]
            closes = j.get("indicators",{}).get("quote",[{}])[0].get("close",[])
            closes = [c for c in closes if c]

            closes_clean = [c for c in closes if c is not None]
            ma200 = sum(closes_clean[-200:]) / min(200, len(closes_clean)) if closes_clean else 0
            ma50  = sum(closes_clean[-50:])  / min(50,  len(closes_clean)) if closes_clean else 0
            price = float(meta.get("regularMarketPrice", 0))

            # Day change — always use closes array, never Yahoo metadata.
            # On weekends Yahoo's regularMarketPrice / regularMarketPreviousClose fields
            # can be swapped (current=Thu, previous=Fri) producing an inverted sign.
            # closes_clean[-1] = last actual trading day, [-2] = day before. Always correct.
            if len(closes_clean) >= 2 and closes_clean[-2] > 0:
                day_change_pct = (closes_clean[-1] - closes_clean[-2]) / closes_clean[-2]
                prev_close     = closes_clean[-2]
            else:
                prev_close     = float(meta.get("regularMarketPreviousClose", 0) or 0) or price
                day_change_pct = (price - prev_close) / prev_close if prev_close > 0 else 0
            # Sanity check — ignore if change looks like stale data (>50% single day move)
            if abs(day_change_pct) > 0.50:
                day_change_pct = 0.0

            # Multi-timeframe price changes — all computed from already-fetched closes
            p3d  = closes_clean[-4]  if len(closes_clean) >= 4  else price
            p5d  = closes_clean[-6]  if len(closes_clean) >= 6  else price
            p30d = closes_clean[-31] if len(closes_clean) >= 31 else price
            change_3d  = (price - p3d)  / p3d  if p3d  > 0 else 0
            change_5d  = (price - p5d)  / p5d  if p5d  > 0 else 0
            change_30d = (price - p30d) / p30d if p30d > 0 else 0

            data[ticker] = {
                "price":          round(price, 2),
                "week52_high":    round(float(meta.get("fiftyTwoWeekHigh", price)), 2),
                "week52_low":     round(float(meta.get("fiftyTwoWeekLow",  price)), 2),
                "avg_volume":     int(meta.get("averageDailyVolume3Month", 0)),
                "ma200":          round(ma200, 2),
                "ma50":           round(ma50, 2),
                "above_ma200":    price >= ma200 * 0.97,
                "pct_above_ma50": (price - ma50) / ma50 if ma50 > 0 else 0,
                "day_change_pct": round(day_change_pct, 4),
                "change_3d_pct":  round(change_3d, 4),
                "change_5d_pct":  round(change_5d, 4),
                "change_30d_pct": round(change_30d, 4),
                # Raw closes for support level analysis — 21d (1M) and 63d (3M)
                "closes_21d":  closes_clean[-22:] if len(closes_clean) >= 22 else closes_clean[:],
                "closes_63d":  closes_clean[-64:] if len(closes_clean) >= 64 else closes_clean[:],
            }
        except Exception as e:
            data[ticker] = {"price":0,"week52_high":0,"week52_low":0,
                            "avg_volume":0,"ma200":0,"above_ma200":False}
    return data


def get_earnings_date(ticker: str) -> Optional[datetime]:
    """Fetch next earnings date from Yahoo Finance."""
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
            f"?modules=calendarEvents",
            headers={"User-Agent":"Mozilla/5.0"}, timeout=8
        )
        j   = r.json().get("quoteSummary",{}).get("result",[{}])[0]
        ts  = j.get("calendarEvents",{}).get("earnings",{}).get("earningsDate",[])
        if ts:
            return datetime.fromtimestamp(ts[0].get("raw",0))
    except: pass
    return None


def get_fundamentals(ticker: str) -> dict:
    """PEG, EPS growth, revenue growth, profit margin for Peter Lynch screen."""
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
            f"?modules=defaultKeyStatistics,financialData",
            headers={"User-Agent":"Mozilla/5.0"}, timeout=8
        )
        j  = r.json().get("quoteSummary",{}).get("result",[{}])[0]
        ks = j.get("defaultKeyStatistics",{})
        fd = j.get("financialData",{})
        return {
            "peg_ratio":      ks.get("pegRatio",{}).get("raw"),
            "forward_pe":     ks.get("forwardPE",{}).get("raw"),
            "eps_growth":     fd.get("earningsGrowth",{}).get("raw"),
            "revenue_growth": fd.get("revenueGrowth",{}).get("raw"),
            "profit_margin":  fd.get("profitMargins",{}).get("raw"),
            "market_cap":     ks.get("enterpriseValue",{}).get("raw"),
        }
    except: return {}


def position_in_range(price, w52_low, w52_high) -> float:
    if w52_high <= w52_low or price <= 0: return 0.5
    return round((price - w52_low) / (w52_high - w52_low), 2)


def pullback_from_high(price, w52_high) -> float:
    """How far is price from 52w high. 0.20 = 20% below high."""
    if w52_high <= 0: return 0
    return round((w52_high - price) / w52_high, 3)


# ════════════════════════════════════════════════════════════
# IBKR
# ════════════════════════════════════════════════════════════

def _parse_ibkr_xml(root: "ET.Element") -> dict:
    """Parse OpenPosition elements from a Flex XML root into the IBKR positions dict."""
    positions = {}
    _all_positions = list(root.iter("OpenPosition"))
    _all_stk = [p for p in _all_positions if p.get("assetCategory","") == "STK"]
    _all_opt = [p for p in _all_positions if p.get("assetCategory","") == "OPT"]
    print(f"   IBKR Flex XML: {len(_all_positions)} OpenPosition elements ({len(_all_stk)} STK, {len(_all_opt)} OPT)")
    for pos in _all_positions:
        sym = pos.get("symbol","").strip()
        if not sym: continue
        asset = pos.get("assetCategory", pos.get("assetClass",""))
        qty   = float(pos.get("position", 0) or 0)
        sym        = sym.replace("BRK B", "BRK-B")
        underlying = pos.get("underlyingSymbol", sym).strip().replace("BRK B","BRK-B")
        # Infer side from signed position qty (Flex XML has no side attribute)
        _ibkr_side = "Short" if qty < 0 else "Long"
        positions[sym] = {
            "market_value":   float(pos.get("positionValue",    0) or 0),
            "quantity":       qty,
            "avg_cost":       float(pos.get("costBasisPrice",   0) or 0),
            "pct_nav":        float(pos.get("percentOfNAV",     0) or 0),
            "asset_class":    asset,
            "currency":       pos.get("currency", pos.get("currencyPrimary","USD")),
            "strike":         pos.get("strike",""),
            "expiry":         pos.get("expiry",""),
            "put_call":       pos.get("putCall",""),
            "underlying":     underlying,
            "unrealized_pnl": float(pos.get("fifoPnlUnrealized", 0) or 0),
            "account":        pos.get("accountId", pos.get("clientAccountID","")),
            "account_type":   "IBKR",
            "side":           _ibkr_side,
            "source":         "ibkr",
        }
    stk  = sum(1 for v in positions.values() if v["asset_class"]=="STK")
    lopt = sum(1 for v in positions.values() if v["asset_class"]=="OPT" and v.get("side")=="Long")
    sopt = sum(1 for v in positions.values() if v["asset_class"]=="OPT" and v.get("side")=="Short")
    print(f"   IBKR: {stk} stocks, {lopt} long options, {sopt} short options loaded")
    return positions


IBKR_XML_FALLBACK = "ibkr_positions.xml"   # commit this file to repo when Flex API is down


def get_ibkr_positions() -> dict:
    positions = {}
    if not IBKR_FLEX_TOKEN or not IBKR_FLEX_QUERY_ID:
        print("   ⚠️ IBKR_FLEX_TOKEN or IBKR_FLEX_QUERY_ID not set")
        return positions
    try:
        r = requests.get(
            f"https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/"
            f"SendRequest"
            f"?t={IBKR_FLEX_TOKEN}&q={IBKR_FLEX_QUERY_ID}&v=3",
            timeout=15
        )
        root = ET.fromstring(r.text)
        ref  = root.findtext("ReferenceCode")
        if root.findtext("Status") != "Success" or not ref:
            _err = root.findtext("ErrorCode","")
            print(f"   ⚠️ IBKR Flex SendRequest failed: Status={root.findtext('Status')!r} ErrorCode={_err} Ref={ref!r}")
            print(f"   IBKR Flex raw response: {r.text[:300]}")
            if _err == "1001":
                print("   Retrying IBKR Flex in 30 seconds...")
                time.sleep(30)
                r = requests.get(
                    f"https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/"
                    f"SendRequest"
                    f"?t={IBKR_FLEX_TOKEN}&q={IBKR_FLEX_QUERY_ID}&v=3",
                    timeout=15
                )
                root = ET.fromstring(r.text)
                ref  = root.findtext("ReferenceCode")
                if root.findtext("Status") != "Success" or not ref:
                    print(f"   ⚠️ IBKR Flex retry also failed: {r.text[:200]}")
                    # ── XML file fallback ──────────────────────────────────
                    return _ibkr_xml_file_fallback()
                print("   ✅ IBKR Flex retry succeeded")
            else:
                return _ibkr_xml_file_fallback()
        time.sleep(5)
        r2    = requests.get(
            f"https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/"
            f"GetStatement"
            f"?t={IBKR_FLEX_TOKEN}&q={ref}&v=3",
            timeout=15
        )
        root2 = ET.fromstring(r2.text)
        positions = _parse_ibkr_xml(root2)
    except Exception as e:
        print(f"   IBKR error: {e}")
        return _ibkr_xml_file_fallback()
    return positions


def _ibkr_xml_file_fallback() -> dict:
    """Parse ibkr_positions.xml from the working directory when Flex API is unavailable.
    To use: download the XML manually from IBKR (Reports → Flex Queries → Run),
    save it as 'ibkr_positions.xml' in the repo root, commit and push."""
    import os
    if not os.path.exists(IBKR_XML_FALLBACK):
        print(f"   ℹ️  No {IBKR_XML_FALLBACK} file found — IBKR positions unavailable")
        return {}
    try:
        print(f"   📂 Loading IBKR positions from {IBKR_XML_FALLBACK} (Flex API fallback)...")
        with open(IBKR_XML_FALLBACK) as f:
            xml_text = f.read()
        root = ET.fromstring(xml_text)
        # Accept both FlexQueryResponse (manual download) and FlexStatementResponse wrappers
        positions = _parse_ibkr_xml(root)
        if positions:
            # Show file date so it's clear this is not live data
            _stmt = root.find(".//FlexStatement")
            _when = _stmt.get("whenGenerated","?") if _stmt is not None else "?"
            print(f"   ⚠️  Using XML fallback data — statement generated {_when} (NOT live)")
        return positions
    except Exception as e:
        print(f"   ⚠️ Failed to parse {IBKR_XML_FALLBACK}: {e}")
        return {}


def compute_portfolio_exposure(ibkr: dict, portfolio_size: float) -> dict:
    """
    Compute real-time CSP and CC exposure from open broker positions.
    Merges IBKR Flex (asset_class=OPT) + Schwab option positions (source="schwab").
    Both sources are already merged into the ibkr dict by run_scanner().
    Returns exposure dict written to results.json under 'exposure' key.
    """
    csp_positions  = []
    cc_positions   = []
    leaps_positions= []  # long calls with DTE >= 500
    bcs_positions  = []  # long calls that are part of bull call spreads (DTE >= 500)
    cc_shares      = {}
    seen           = set()
    leaps_accum    = {}  # accumulate LEAPS quantities across accounts: key=(underlying,strike,expiry)

    for sym, pos in ibkr.items():
        if pos.get("asset_class") != "OPT": continue
        side      = pos.get("side", "Long")
        put_call  = pos.get("put_call", "").upper()[:1]  # normalise to P / C
        qty       = abs(float(pos.get("quantity", 0) or 0))
        strike    = float(pos.get("strike", 0) or 0)
        expiry    = pos.get("expiry", "")
        source    = pos.get("source", "ibkr")
        underlying = (
            pos.get("underlying", sym)
            .upper().replace("BRK B","BRK-B").replace("BRK/B","BRK-B").strip()
        )
        if qty == 0 or strike == 0: continue

        # Deduplicate — same position sometimes in both IBKR and Schwab feeds.
        # Long Calls excluded — leaps_accum aggregates them across all accounts.
        if not (side == "Long" and put_call == "C"):
            dedup_key = (underlying, put_call, strike, expiry, side)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

        if side == "Short" and put_call == "P":
            cso      = round(strike * 100 * qty, 0)
            # avg_cost for IBKR short options: negative = credit received (e.g. -4.95 = received $4.95)
            # costBasisPrice for Schwab: positive premium received
            _raw_avg = float(pos.get("avg_cost", 0) or pos.get("averagePrice", 0) or pos.get("costBasisPrice", 0) or 0)
            # For short positions IBKR stores negative avg_cost = credit received
            # We want the absolute value of what was received per share
            avg_cost_opt = abs(_raw_avg)
            # IBKR stores avg_cost in per-contract terms (×100), not per-share
            # If value > 50 it's almost certainly per-contract — divide by 100
            if avg_cost_opt > 50:
                avg_cost_opt = avg_cost_opt / 100
            # Cross-check with market_value to derive current mark
            _mv  = float(pos.get("market_value", 0) or 0)
            _mark_from_mv = abs(_mv / (qty * 100)) if qty > 0 else 0
            acct_lbl = pos.get("account_type", "") or ("IBKR" if source == "ibkr" else source)
            print(f"   CSP pos {underlying}: avg_cost_raw={_raw_avg:.4f} premium_received={avg_cost_opt:.4f} mark_from_mv={_mark_from_mv:.4f}")
            csp_positions.append({
                "ticker":              underlying,
                "strike":              strike,
                "contracts":           int(qty),
                "cso":                 cso,
                "expiry":              expiry,
                "source":              source,
                "account":             acct_lbl,
                "premium_received":    round(avg_cost_opt, 4),  # abs(avg_cost) = credit received per share
                "mark_from_mv":        round(_mark_from_mv, 4),
            })
        elif side == "Short" and put_call == "C":
            # BCS short leg: DTE >= 400 days — exclude from CC coverage
            try:
                from datetime import datetime as _dt
                _exp_dt = _dt.strptime(expiry, "%Y%m%d") if len(expiry) == 8 else _dt.strptime(expiry, "%Y-%m-%d")
                _dte = (_exp_dt - _dt.now()).days
            except:
                _dte = 0
            if _dte >= 400:
                # BCS short leg — record but don't count as CC coverage
                try:
                    _bcs_exp_str = _exp_dt.strftime("%b %Y")
                except:
                    _bcs_exp_str = str(expiry)[:7]
                bcs_positions.append({
                    "ticker":    underlying,
                    "strike":    float(strike) if strike else 0,
                    "contracts": int(qty),
                    "expiry":    str(expiry),
                    "expiry_fmt":_bcs_exp_str,
                    "dte":       _dte,
                    "leg":       "short",
                    "source":    source,
                })
            else:
                # Standard covered call
                nva = round(strike * 100 * qty, 0)
                shares_covered = int(qty * 100)
                avg_cost_cc  = abs(float(pos.get("avg_cost", 0) or pos.get("averagePrice", 0) or pos.get("costBasisPrice", 0) or 0))
                if avg_cost_cc > 50: avg_cost_cc = avg_cost_cc / 100
                acct_lbl_cc  = pos.get("account_type", "") or ("IBKR" if source == "ibkr" else source)
                mv_cc        = float(pos.get("market_value", 0) or 0)
                mark_cc      = abs(mv_cc / (qty * 100)) if qty > 0 else 0
                cc_positions.append({
                    "ticker":           underlying,
                    "strike":           strike,
                    "contracts":        int(qty),
                    "nva":              nva,
                    "shares_covered":   shares_covered,
                    "expiry":           expiry,
                    "source":           source,
                    "account":          acct_lbl_cc,
                    "premium_received": round(avg_cost_cc, 4),
                    "mark":             round(mark_cc, 4),
                })
                cc_shares[underlying] = cc_shares.get(underlying, 0) + shares_covered
        elif side == "Long" and put_call == "C":
            # Long calls — LEAPS (DTE >= 400) or BCS long leg
            try:
                from datetime import datetime as _dt
                _exp_raw = str(expiry).strip()
                if len(_exp_raw) == 8 and _exp_raw.isdigit():
                    _exp_dt = _dt.strptime(_exp_raw, "%Y%m%d")
                elif len(_exp_raw) >= 10:
                    _exp_dt = _dt.strptime(_exp_raw[:10], "%Y-%m-%d")
                else:
                    _exp_dt = _dt.now()
                _dte = (_exp_dt - _dt.now()).days
            except Exception as _e:
                print(f"     LEAPS expiry parse error: {expiry!r} — {_e}")
                _dte = 0
            if _dte >= 400:
                avg_cost  = float(pos.get("avg_cost", 0) or 0)
                _strike_f = float(strike) if strike else 0
                breakeven = round(_strike_f + avg_cost, 2) if avg_cost > 0 and _strike_f > 0 else None
                try:
                    _exp_str = _exp_dt.strftime("%b %Y")
                except:
                    _exp_str = str(expiry)[:7]
                _leaps_mv = float(pos.get("market_value", 0) or 0)
                # Resolve account label — same logic as CSP/CC
                _leaps_acct = pos.get("account_type", "") or ("IBKR" if source == "ibkr" else source)
                # Key by (ticker, strike, expiry, account) — preserves per-account rows
                _lkey = (underlying, _strike_f, str(expiry), _leaps_acct)
                if _lkey in leaps_accum:
                    leaps_accum[_lkey]["contracts"]    += int(qty)
                    leaps_accum[_lkey]["market_value"] += round(_leaps_mv, 0)
                else:
                    leaps_accum[_lkey] = {
                        "ticker":       underlying,
                        "strike":       _strike_f,
                        "contracts":    int(qty),
                        "expiry":       str(expiry),
                        "expiry_fmt":   _exp_str,
                        "dte":          _dte,
                        "avg_cost":     avg_cost,
                        "breakeven":    breakeven,
                        "market_value": round(_leaps_mv, 0),
                        "source":       source,
                        "account":      _leaps_acct,
                    }

    # Flush accumulated LEAPS into leaps_positions
    leaps_positions = list(leaps_accum.values())

    total_cso     = sum(p["cso"] for p in csp_positions)
    total_nva     = sum(p["nva"] for p in cc_positions)
    max_cso       = portfolio_size * MAX_CSP_ALLOCATION_PCT
    remaining_csp = max(0.0, max_cso - total_cso)
    csp_pct       = round(total_cso / portfolio_size * 100, 1) if portfolio_size > 0 else 0

    # Per-ticker CC coverage stats (for spec §4 CC Exposure)
    cc_coverage_pct = {}
    for ticker, covered in cc_shares.items():
        stk = ibkr.get(ticker, {})
        total_shares = float(stk.get("quantity", 0) or stk.get("qty", 0) or 0)
        if total_shares > 0:
            cc_coverage_pct[ticker] = round(covered / total_shares * 100, 1)

    schwab_puts  = sum(1 for p in csp_positions if p.get("source") == "schwab")
    schwab_calls = sum(1 for p in cc_positions  if p.get("source") == "schwab")
    ibkr_puts    = len(csp_positions) - schwab_puts
    ibkr_calls   = len(cc_positions)  - schwab_calls
    print(f"   Exposure: {len(csp_positions)} short puts (IBKR:{ibkr_puts} Schwab:{schwab_puts}) | "
          f"{len(cc_positions)} short calls (IBKR:{ibkr_calls} Schwab:{schwab_calls})")
    print(f"   LEAPS found: {len(leaps_positions)} | BCS legs: {len(bcs_positions)}")
    if leaps_positions:
        for lp in leaps_positions[:3]:
            print(f"     LEAPS: {lp['ticker']} ${lp['strike']} {lp['expiry']} DTE={lp['dte']} BE={lp['breakeven']}")
    print(f"   CSP Obligation: ${total_cso:,.0f} ({csp_pct:.1f}%) | Remaining: ${remaining_csp:,.0f}")

    return {
        "portfolio_size":          round(portfolio_size, 0),
        "max_csp_allocation_pct":  int(MAX_CSP_ALLOCATION_PCT * 100),
        "max_csp_allocation_usd":  round(max_cso, 0),
        "total_csp_obligation":    round(total_cso, 0),
        "csp_allocation_pct":      csp_pct,
        "remaining_csp_capacity":  round(remaining_csp, 0),
        "csp_positions":           csp_positions,
        "total_cc_notional":       round(total_nva, 0),
        "cc_shares_covered":       cc_shares,
        "cc_coverage_pct":         cc_coverage_pct,
        "cc_positions":            cc_positions,
        "total_premium_csp":       0,
        "total_premium_cc":        0,
        "total_premium_all":       0,
        # Risk summary (spec §4)
        "max_assignment_exposure": round(total_cso, 0),
        "max_shares_called_away":  sum(cc_shares.values()),
        # LEAPS and BCS positions for Positions tab
        "leaps_positions":         leaps_positions,
        "bcs_positions":           bcs_positions,
        # LEAPS totals — for footer display
        "total_leaps_market_value": round(sum(
            float(lp.get("market_value", 0) or 0) for lp in leaps_positions), 0),
        "total_leaps_cost_basis":   round(sum(
            lp.get("avg_cost", 0) * lp.get("contracts", 0) * 100
            for lp in leaps_positions), 0),
        "total_leaps_contracts":    sum(lp.get("contracts", 0) for lp in leaps_positions),
        "total_leaps_positions":    len(leaps_positions),
    }


def find_existing_leaps(ticker: str, ibkr_positions: dict) -> Optional[dict]:
    """Check if we already own a LEAPS call on this ticker (for PMCC)."""
    today = datetime.now()
    for sym, pos in ibkr_positions.items():
        if (pos.get("asset_class") == "OPT"
                and pos.get("underlying","").upper() == ticker.upper()
                and pos.get("put_call","").upper() == "C"
                and pos.get("quantity", 0) > 0):
            try:
                exp = datetime.strptime(pos["expiry"], "%Y%m%d")
                dte = (exp - today).days
                if dte >= 300:  # it's a LEAPS
                    return {
                        "strike":   float(pos.get("strike", 0) or 0),
                        "expiry":   exp.strftime("%Y-%m-%d"),
                        "dte":      dte,
                        "quantity": int(pos.get("quantity", 0)),
                        "avg_cost": float(pos.get("avg_cost", 0) or 0),
                    }
            except: continue
    return None


# ════════════════════════════════════════════════════════════
# UNUSUAL WHALES
# ════════════════════════════════════════════════════════════

def get_option_contracts(ticker: str) -> list:
    """Replaced by schwab_get_option_chain — returns empty fallback."""
    return []


def get_darkpool(ticker: str = "", **kwargs) -> list:
    return []


def get_flow_alerts_market() -> list:
    return []


def get_market_tide() -> dict:
    return {"score": 0, "label": "Market Tide removed", "available": False}


def get_vix() -> dict:
    """Fetch VIX from Yahoo Finance. VIX = market fear gauge."""
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX",
            headers={"User-Agent":"Mozilla/5.0"}, timeout=8
        )
        price = r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"]
        vix = round(float(price), 2)

        if vix < 15:
            label = f"😴 VIX {vix} — Very low fear. Market complacent. Poor time to sell premium."
            regime = "low"
        elif vix < 20:
            label = f"😊 VIX {vix} — Low-moderate fear. Decent but not ideal for premium selling."
            regime = "low_moderate"
        elif vix < 25:
            label = f"😐 VIX {vix} — Moderate fear. Reasonable premium selling conditions."
            regime = "moderate"
        elif vix < 35:
            label = f"😬 VIX {vix} — Elevated fear. Good premium selling opportunity."
            regime = "elevated"
        else:
            label = f"😱 VIX {vix} — Extreme fear. Exceptional CSP/CC premiums but manage size carefully."
            regime = "extreme"

        return {"vix": vix, "label": label, "regime": regime}
    except Exception as e:
        return {"vix": 0, "label": "VIX unavailable", "regime": "unknown"}


def get_spike() -> dict:
    return {"available": False}


def get_oi_change() -> dict:
    return {}


def get_expiry_breakdown(ticker: str) -> dict:
    return {}


def market_go_nogo(tide: dict, vix_data: dict,
                   spy_regime: dict = None) -> dict:
    """
    Simplified go/no-go — VIX and Market Tide are for BRIEFING ONLY.
    Individual trades are filtered by per-stock IVP.
    This function always returns sell_premium=True and buy_leaps=True
    so the scanner always runs — IVP handles the actual filtering.
    The briefing informs the user; it does not block trades.
    """
    vix        = vix_data.get("vix", 20)
    vix_regime = vix_data.get("regime", "moderate")
    tide_score = tide.get("score", 0)
    spy_warning = spy_regime and not spy_regime.get("above_ma200", True)

    # Context labels for briefing only
    if vix >= 25 and tide_score > 0:
        quality = "🔥 EXCELLENT — High VIX + bullish tide. Strong premium selling environment."
    elif vix >= 20 and tide_score > -20:
        quality = "✅ GOOD — Reasonable conditions. IVP filters individual trades."
    elif tide_score < -30:
        quality = "⚠️ BEARISH TIDE — Put premium dominating. Be selective, check IVP per stock."
    elif vix < 15:
        quality = "😴 LOW VOLATILITY — Premiums are thin. Only trade highest IVP setups."
    else:
        quality = "✅ NEUTRAL — Scanner running. IVP determines trade quality per stock."

    if spy_warning:
        quality += " | ⚠️ S&P below 200MA — reduce size."

    return {
        "sell_premium": True,   # always scan — IVP filters per stock
        "buy_leaps":    True,   # always scan — timing filters per stock
        "vix":          vix,
        "tide_score":   tide_score,
        "vix_regime":   vix_regime,
        "quality":      quality,
        "spy_warning":  spy_warning if spy_warning else False,
        "bull_oi_count": 0,
        "bear_oi_count": 0,
    }




# ════════════════════════════════════════════════════════════
# OPTION MATH
# ════════════════════════════════════════════════════════════

def parse_option_symbol(sym: str):
    try:
        m = re.match(r'^([A-Z.\-]+)(\d{6})([CP])(\d{8})$', sym)
        if not m: return None
        return datetime.strptime("20"+m.group(2), "%Y%m%d"), m.group(3), int(m.group(4))/1000
    except: return None


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def estimate_delta(spot, strike, dte, iv, opt_type) -> Optional[float]:
    """Black-Scholes delta using actual spot and strike."""
    try:
        if iv <= 0 or dte <= 0 or iv > 5: return None
        t   = dte / 365
        sst = iv * math.sqrt(t)
        if sst == 0: return None
        d1  = (math.log(spot / strike) + 0.5 * iv**2 * t) / sst
        delta = norm_cdf(d1) if opt_type == "C" else norm_cdf(d1) - 1
        return round(abs(delta), 2)
    except: return None


def fetch_historical_ivp(ticker: str) -> float:
    """
    Compute true IVP from 1 year of daily price history.
    IVP = percentile rank of current 21-day realized vol vs past year of 21d vols.
    Returns float 0-100. Falls back to 50 on error.
    """
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            headers={"User-Agent": "Mozilla/5.0"},
            params={"interval": "1d", "range": "1y"},
            timeout=8
        )
        closes = r.json()["chart"]["result"][0]["indicators"]["quote"][0].get("close", [])
        closes = [c for c in closes if c is not None]
        if len(closes) < 42:
            return 50.0
        import math as _m
        log_rets = [_m.log(closes[i] / closes[i-1]) for i in range(1, len(closes)) if closes[i-1] > 0]
        # Rolling 21-day realized vol (annualized)
        rv_series = []
        for i in range(21, len(log_rets) + 1):
            window = log_rets[i-21:i]
            rv = _m.sqrt(252 * sum(x**2 for x in window) / 21)
            rv_series.append(rv)
        if len(rv_series) < 2:
            return 50.0
        current_rv = rv_series[-1]
        ivp = sum(1 for v in rv_series[:-1] if v <= current_rv) / len(rv_series[:-1]) * 100
        return round(ivp, 1)
    except Exception:
        return 50.0


# Cache IVP values per scan to avoid repeated Yahoo calls
_ivp_cache: dict = {}

def get_ivp(ticker: str, atm_iv_fallback: float = 0.29) -> float:
    """Return cached or freshly computed IVP for ticker."""
    global _ivp_cache
    if ticker in _ivp_cache:
        return _ivp_cache[ticker]
    ivp = fetch_historical_ivp(ticker)
    _ivp_cache[ticker] = ivp
    return ivp


def calculate_ivp(contracts: list, ticker: str = "") -> dict:
    """
    IV data for a ticker. Uses true historical IVP from Yahoo price history.
    ATM IV comes from Schwab chain. IVP from rolling realized vol percentile.
    """
    chain_iv = next((c.get("_chain_iv", 0) for c in contracts if c.get("_chain_iv", 0) > 0), 0)

    # True per-stock IVP from historical realized vol
    if ticker:
        ivp = get_ivp(ticker, atm_iv_fallback=chain_iv if chain_iv > 0 else 0.29)
    else:
        # No ticker — use ATM IV based estimate as fallback
        import math as _math
        atm_iv = chain_iv if chain_iv > 0.01 else 0.29
        ivp = round(min(95, max(5, 100 * (1 - _math.exp(-atm_iv / 0.25)))), 1)

    atm_iv = chain_iv if chain_iv > 0.01 else 0.29
    return {
        "iv_current": round(atm_iv, 3),
        "iv_low":     round(atm_iv * 0.50, 3),
        "iv_high":    round(atm_iv * 2.00, 3),
        "ivp":        ivp,
    }

def calc_support_levels(closes_21d: list, closes_63d: list, tier: str) -> dict:
    """
    Detect repeated price support zones from 1M and 3M closes.
    Returns starter / main / aggressive levels + confidence score.

    Algorithm:
      1. Find swing lows in each window (local minima with rebound)
      2. Cluster lows within 3% — most-touched cluster = main support
      3. Define starter (shallow, above main) and aggressive (panic low)

    Used as a scoring signal only. Manual buy_under in SYMBOL_SETTINGS
    remains the hard gate for CSP/LEAPS qualification.
    """
    # Tier-specific volatility spacing between levels
    _spread = {"Core": 0.03, "Growth": 0.05, "Cyclical": 0.04, "Opportunistic": 0.07}.get(tier, 0.05)

    def _swing_lows(closes, window=3):
        """Local minima: closes[i] is lowest in a 2*window+1 band and preceded by a decline."""
        lows = []
        for i in range(window, len(closes) - window):
            segment = closes[i - window: i + window + 1]
            if closes[i] == min(segment) and closes[i] < closes[i - 1]:
                lows.append(closes[i])
        return lows

    def _cluster(lows, tol=0.03):
        """Group lows within tol% of the running cluster centre."""
        clusters = []
        for low in sorted(lows):
            placed = False
            for cl in clusters:
                if cl['centre'] > 0 and abs(low - cl['centre']) / cl['centre'] <= tol:
                    cl['lows'].append(low)
                    cl['centre'] = sum(cl['lows']) / len(cl['lows'])
                    placed = True
                    break
            if not placed:
                clusters.append({'centre': low, 'lows': [low]})
        return sorted(clusters, key=lambda x: len(x['lows']), reverse=True)

    try:
        lows_21 = _swing_lows(closes_21d) if len(closes_21d) >= 8 else []
        lows_63 = _swing_lows(closes_63d) if len(closes_63d) >= 8 else []
        if not lows_21 and not lows_63:
            return {}

        # 1M lows weighted 2× — more recent structure matters more
        all_lows = lows_21 * 2 + lows_63
        clusters = _cluster(all_lows)
        if not clusters:
            return {}

        main     = clusters[0]
        main_mid = round(main['centre'], 2)
        main_low = round(min(main['lows']), 2)
        main_hi  = round(max(main['lows']), 2)

        # Starter: nearest cluster above main, or vol-adjusted offset above
        above = [c for c in clusters[1:] if c['centre'] > main_mid * 1.01]
        if above:
            sc         = min(above, key=lambda x: x['centre'])
            start_low  = round(min(sc['lows']), 2)
            start_hi   = round(max(sc['lows']), 2)
        else:
            start_low  = round(main_mid * (1 + _spread * 0.6), 2)
            start_hi   = round(main_mid * (1 + _spread * 1.2), 2)

        # Aggressive: absolute lowest swing low from combined set
        abs_low = min(all_lows)
        agg_low = round(abs_low, 2)
        agg_hi  = round(main_low * 0.99, 2)   # just under main cluster floor

        touches    = len(main['lows'])
        confidence = min(10, touches * 2)      # 0–10 scale

        return {
            "starter_buy_low":    start_low,
            "starter_buy_high":   start_hi,
            "main_buy_low":       main_low,
            "main_buy_high":      main_hi,
            "main_buy_under":     main_mid,    # single value used in scoring
            "aggressive_buy_low": agg_low,
            "aggressive_buy_high": agg_hi,
            "support_touches":    touches,
            "confidence":         confidence,
        }
    except Exception:
        return {}


def tier_weight(tier: str) -> int:
    """Quality points by tier. Used in all strategy scores."""
    return {"Core": 3, "Growth": 2, "Cyclical": 1, "Opportunistic": 0}.get(tier, 0)

def tier_target_range(tier: str) -> tuple:
    """Target allocation range (low%, high%) by tier — used as fallback."""
    return TARGET_RANGES.get(tier, (1.0, 3.0))

def ticker_target_range(ticker: str, tier: str) -> tuple:
    """Per-ticker target range using ±20% tolerance band around target_pct.
    Falls back to tier range if ticker not in TICKER_TARGETS."""
    tt = TICKER_TARGETS.get(ticker)
    if tt:
        mid = tt["target_pct"]
        lo  = round(mid * (1 - TICKER_TOLERANCE), 2)
        hi  = round(mid * (1 + TICKER_TOLERANCE), 2)
        return (lo, hi)
    return tier_target_range(tier)

def ticker_position_status(ticker: str, tier: str, exposure_pct: float) -> str:
    """Underweight / On Target / Overweight / Not Held using per-ticker targets."""
    tt = TICKER_TARGETS.get(ticker)
    if tt and tt.get("speculative") and exposure_pct == 0:
        return "Not Held"
    lo, hi = ticker_target_range(ticker, tier)
    if exposure_pct == 0 or exposure_pct < lo:   return "Underweight"
    elif exposure_pct <= hi:                      return "On Target"
    else:                                         return "Overweight"

def tier_position_status(tier: str, exposure_pct: float) -> str:
    """Legacy shim — prefer ticker_position_status for new code."""
    lo, hi = tier_target_range(tier)
    if exposure_pct == 0 or exposure_pct < lo:   return "Underweight"
    elif exposure_pct <= hi:                      return "On Target"
    else:                                         return "Overweight"

# Max scores per strategy (for normalization)
SCORE_MAX = {"CSP": 12, "CC": 13, "LEAPS": 13, "PIO": 13, "PMCC": 13}

# Telegram green-light threshold — only trades scoring ≥ this % of SCORE_MAX get sent.
# Dashboard always shows everything. Raise to reduce Telegram noise; lower to see more.
TELEGRAM_MIN_SCORE_PCT = 0.75  # 75% = at least 9/12 for CSP, 10/13 for CC/LEAPS

def quality_label(score: int, max_score: int) -> str:
    """Convert score to Strong / Acceptable / Weak."""
    pct = score / max_score if max_score > 0 else 0
    if pct >= 0.75:   return "Strong"
    elif pct >= 0.50: return "Acceptable"
    else:             return "Weak"

def clean_signal(signal: str) -> str:
    """Remove IVP from signal text — IVP shown once in card header."""
    if not signal: return ''
    parts = [p.strip() for p in signal.split('|')]
    parts = [p for p in parts if not p.startswith('IVP') and not p.startswith('ivp')]
    return ' | '.join(parts).strip()

def normalized_score(raw_score: int, mode: str) -> float:
    """Normalize score to 0-1 for cross-strategy comparison."""
    mx = SCORE_MAX.get(mode, 12)
    return round(raw_score / mx, 3) if mx > 0 else 0.0

def score_cc(opp: dict) -> int:
    """
    Canonical CC score (max 13):
    Tier(3) + Delta(3) + IVP(2) + Safety(3) + Income(2)
    Delta is a FILTER — score rewards target range, not max delta.
    """
    s = tier_weight(opp.get("tier",""))
    # Delta: CONSTRAINT not scoring driver (patch guide section 3)
    # Applied as safety reduction — does not add positive points
    d = abs(opp.get("delta", 0))
    if d > 0.40:            s -= 2   # hard penalty — above ceiling
    elif d > 0.35:          s -= 1   # soft penalty — approaching limit
    # 0.20-0.35 = acceptable range, no bonus points for delta itself
    # IVP quality — CSP: higher IVP = better premium for selling
    ivp = opp.get("ivp", 0)
    if ivp >= 40:            s += 2   # good/elevated = excellent for selling
    elif ivp >= 20:          s += 1   # moderate = ok
    # <20 = 0 (low IVP = thin premium environment)
    # Safety buffer: strike above breakeven/cost basis
    strike = opp.get("strike", 0)
    be = opp.get("breakeven", 0) or opp.get("avg_cost", 0)
    if be and be > 0 and strike > 0:
        buf_pct = (strike - be) / be * 100
        if buf_pct > 10:    s += 3
        elif buf_pct >= 5:  s += 2
        elif buf_pct >= 0:  s += 1
    # Income quality — not the primary driver
    ann = opp.get("annualized_return", 0)
    if 15 <= ann <= 35:     s += 2
    elif 8 <= ann < 15:     s += 1
    elif ann > 35:          s += 1   # high return is ok but not +3
    # Rebound boost: stock up strongly today = ideal CC timing
    day_chg = opp.get("day_change_pct", 0)
    if day_chg >= 0.07:     s += 3   # strong rally — prioritize CC
    elif day_chg >= 0.05:   s += 2   # solid rebound — boost CC
    elif day_chg >= 0.03:   s += 1   # moderate up day — slight boost
    return max(0, s)

def csp_engine(opp: dict, spy_day_chg: float = 0,
               buy_under: float = 0, csp_delta_min: float = 0, csp_delta_max: float = 0,
               ticker: str = "") -> dict:
    """
    CSP Entry Engine v3 — price-first, risk-aware.
    Actions: BUY_SAFE, BUY_RISKY, WAIT, SKIP
    Penalty stacking limited to top 2 strongest.
    buy_under:     per-symbol max effective entry (strike - premium). 0 = no restriction.
    csp_delta_min: per-symbol delta floor. 0 = use global default.
    csp_delta_max: per-symbol delta ceiling. 0 = use global default.
    ticker:        symbol (for bucket-aware flag/threshold lookup). "" = skip bucket checks.
    """
    # ── Phase 1: Bucket-aware early skip ──────────────────────
    # NBIS, CRDO → spreads_only (handled by Phase 2 spread scanner, not CSPs)
    # BABA → leaps_only (no premium selling)
    # MSTR, OWL → cc_only (exit-waiting positions, no new CSPs)
    if ticker and BUCKETS:
        if is_spreads_only(ticker, BUCKETS):
            return {"action": "SKIP", "drop_type": "WEAK", "yield_30d": 0,
                    "flags": ["BUCKET: spreads-only ticker"], "sort_key": 0}
        if is_leaps_only(ticker, BUCKETS):
            return {"action": "SKIP", "drop_type": "WEAK", "yield_30d": 0,
                    "flags": ["BUCKET: LEAPS-only ticker"], "sort_key": 0}
        if is_cc_only(ticker, BUCKETS):
            return {"action": "SKIP", "drop_type": "WEAK", "yield_30d": 0,
                    "flags": ["BUCKET: CC-only (exit-waiting)"], "sort_key": 0}
        # Watchlist tickers (META): require meaningful pullback for new CSPs
        if is_watchlist_only(ticker, BUCKETS):
            _pb = opp.get("pullback_pct", 0)
            if _pb < 15:
                return {"action": "SKIP", "drop_type": "WEAK", "yield_30d": 0,
                        "flags": ["BUCKET: watchlist — needs >15% pullback"], "sort_key": 0}

    tier      = opp.get("tier", "Opportunistic")
    delta     = abs(opp.get("delta", 0))
    dte       = opp.get("dte", 30)
    strike    = opp.get("strike", 0)
    premium   = opp.get("premium", 0)
    ivp       = opp.get("ivp", 50)
    drop_1d   = opp.get("drop_1d", 0)
    drop_5d   = opp.get("drop_5d", 0)
    off_low   = opp.get("off_low_5d", 5.0)
    price     = opp.get("price", 0)
    ma50      = opp.get("ma50", price)
    ma200     = opp.get("ma200", price * 0.9)
    contracts = opp.get("contracts", 1)
    pullback  = opp.get("pullback_pct", 0)

    flags = []

    # ── Step 0: SPY panic day — hard skip ─────────────────────
    if spy_day_chg <= -0.03:
        return {"action": "SKIP", "drop_type": "WEAK", "yield_30d": 0,
                "flags": ["SPY PANIC DAY"], "sort_key": 0}
    spy_downgrade = spy_day_chg <= -0.02

    # ── Step 1: Drop classification ────────────────────────────
    if drop_1d <= -0.04 or drop_5d <= -0.08:
        drop_type = "STRONG";  action = "BUY"
    elif drop_1d <= -0.02 or drop_5d <= -0.04:
        drop_type = "MODERATE"; action = "WAIT"
    elif pullback >= 35:
        drop_type = "STRONG";  action = "BUY"
    elif pullback >= 20:
        drop_type = "MODERATE"; action = "WAIT"
    elif pullback >= 10:
        drop_type = "MODERATE"; action = "WAIT"
    else:
        drop_type = "WEAK";    action = "WAIT"

    if spy_downgrade and action == "BUY":
        action = "WAIT"; flags.append("HIGH VOLATILITY")

    # ── Rebound suppression: avoid CSPs on sharp up days ──────────────
    # A strong rebound day means premium spike + likely reversal risk
    # Use relative rebound: how much of the 5d drop was recovered today
    rebound_relative = drop_1d / abs(drop_5d) if drop_5d < -0.01 else 0
    if drop_1d >= 0.07:
        # Skip CSP entirely — strong rally day, sell CC instead
        return {"action": "SKIP", "drop_type": drop_type, "yield_30d": 0,
                "flags": ["REBOUND DAY — use CC"], "sort_key": 0}
    elif drop_1d >= 0.05 or rebound_relative >= 0.6:
        # Downgrade one level — moderate rally or recovered most of 5d drop
        if action == "BUY":
            action = "WAIT"
        elif action == "WATCH":
            action = "SKIP"
            return {"action": "SKIP", "drop_type": drop_type, "yield_30d": 0,
                    "flags": ["REBOUND DAY — use CC"], "sort_key": 0}

    # ── Step 2: Trend penalties — MAX 2, priority ordered ─────
    # Priority: 1=below200DMA (strongest), 2=at_lows, 3=below50DMA (weakest)
    penalties = []
    below_200 = price > 0 and ma200 > 0 and price < ma200
    below_50  = price > 0 and ma50  > 0 and price < ma50
    at_lows   = off_low <= 2.0

    if below_200: penalties.append("BELOW 200DMA")
    if at_lows:   penalties.append("AT LOWS")
    if below_50 and len(penalties) < 2: penalties.append("BELOW 50DMA")
    # Only apply top 2 penalties max
    flags.extend(penalties[:2])

    # Downgrades: only if strongest penalty present (applies to all tiers,
    # including Opportunistic — previously Opportunistic was hard-skipped
    # here, which meant high-IVP setups on speculative names were discarded
    # before yield was ever checked)
    if below_200 or at_lows:
        if action == "BUY":
            action = "WAIT"

    # ── Step 3: Yield validation ───────────────────────────────
    yield_30d = (premium / strike) * (30 / dte) if strike > 0 and dte > 0 else 0
    min_yields = {"Core": 0.015, "Growth": 0.020, "Opportunistic": 0.025, "Cyclical": 0.020}
    base_min   = min_yields.get(tier, 0.020)
    threshold  = base_min * (0.80 if ivp < 30 else 1.20 if ivp > 60 else 1.0)

    if yield_30d < threshold:
        return {"action": "SKIP", "drop_type": drop_type,
                "yield_30d": round(yield_30d*100, 2),
                "flags": ["LOW YIELD"], "sort_key": 0}

    # Absolute $500 minimum
    if premium * contracts * 100 < 500:
        return {"action": "SKIP", "drop_type": drop_type,
                "yield_30d": round(yield_30d*100, 2),
                "flags": ["PREMIUM < $500"], "sort_key": 0}

    # ── Step 3c: Bucket-aware annualized minimum ──────────────
    # In addition to the tier-based 30d yield check above, enforce the
    # bucket-level annualized return floor (A:12%, B:18%, C:28%, D:40%).
    # This catches cases where the tier check passes but the bucket
    # (which reflects volatility) demands more premium for the risk.
    if ticker and BUCKETS and dte > 0 and strike > 0:
        _annualized = (premium / strike) * (365 / dte) * 100
        _bucket_min_ann = get_min_annualized_csp(ticker, BUCKETS)
        if _bucket_min_ann > 0 and _annualized < _bucket_min_ann:
            _bkt = get_bucket(ticker, BUCKETS)
            return {"action": "SKIP", "drop_type": drop_type,
                    "yield_30d": round(yield_30d*100, 2),
                    "flags": [f"BUCKET {_bkt}: ANN {_annualized:.1f}% < {_bucket_min_ann:.0f}% floor"],
                    "sort_key": 0}

    # ── Step 3b: Buy Under — effective entry alignment ─────────
    # effective_entry = strike - premium (what we'd pay if assigned)
    # Hard rule: if effective entry exceeds buy_under, skip entirely.
    # "Buy Under" is a strict intent — no point selling a CSP if assignment
    # would land above the target acquisition price.
    # Only a 3% grace margin is allowed for rounding / premium variance.
    if buy_under > 0 and strike > 0:
        effective_entry = round(strike - premium, 2)
        if effective_entry > buy_under * 1.03:
            # Effective entry more than 3% above buy_under — hard skip
            return {"action": "SKIP", "drop_type": drop_type,
                    "yield_30d": round(yield_30d*100, 2),
                    "flags": [f"ENTRY ${effective_entry:.2f} > BUY UNDER ${buy_under:.2f}"], "sort_key": 0}
        elif effective_entry > buy_under:
            # Within 3% — allow but flag it
            flags.append(f"ENTRY ${effective_entry:.2f} ~= BUY UNDER ${buy_under:.2f}")

    # ── Step 4: Delta check (uses per-symbol range when provided) ──
    _delta_max = csp_delta_max if csp_delta_max > 0 else (0.30 if ivp > 50 else 0.25)
    _delta_min = csp_delta_min if csp_delta_min > 0 else 0.0
    if delta > _delta_max:
        return {"action": "SKIP", "drop_type": drop_type,
                "yield_30d": round(yield_30d*100, 2),
                "flags": [f"DELTA {delta:.2f} > MAX {_delta_max:.2f}"], "sort_key": 0}
    if _delta_min > 0 and delta < _delta_min:
        return {"action": "SKIP", "drop_type": drop_type,
                "yield_30d": round(yield_30d*100, 2),
                "flags": [f"DELTA {delta:.2f} < MIN {_delta_min:.2f}"], "sort_key": 0}

    # ── Step 5: Portfolio context ──────────────────────────────
    if opp.get("over_allocation"):      flags.append("OVER ALLOCATION")
    if opp.get("csp_exposure_pct",0) > 50: flags.append("EXTREME CSP EXPOSURE")
    elif opp.get("csp_exposure_pct",0) > 30: flags.append("HIGH CSP EXPOSURE")

    # ── Step 6: Classify as BUY_SAFE or BUY_RISKY ─────────────
    # BUY_SAFE: no major flags, good setup
    # BUY_RISKY: best available, has flags but actionable
    risky_flags = {"BELOW 200DMA", "AT LOWS", "HIGH VOLATILITY", "OVER ALLOCATION"}
    has_risky   = any(f in risky_flags for f in flags)

    if action == "BUY":
        action = "BUY_RISKY" if has_risky else "BUY_SAFE"

    # Sort key: BUY_SAFE=4, BUY_RISKY=3, WAIT STRONG=2, WAIT MODERATE=1, WAIT WEAK=0.x
    if action == "BUY_SAFE":
        drop_score = 4
    elif action == "BUY_RISKY":
        drop_score = 3
    else:
        drop_score = 2 if drop_type == "STRONG" else 1 if drop_type == "MODERATE" else 0.5

    sort_key = drop_score * 10 + min(yield_30d * 100, 9.9)

    return {
        "action":    action,
        "drop_type": drop_type,
        "yield_30d": round(yield_30d * 100, 2),
        "flags":     flags,
        "sort_key":  round(sort_key, 3),
    }


def csp_promote_best(dashboard_csps: list) -> list:
    """
    Post-processing: if no BUY_SAFE or BUY_RISKY exists,
    promote top 1-3 WAIT candidates to BUY_RISKY.
    Selection: highest yield_30d, no EXTREME flags.
    """
    has_buy = any(c.get("action") in ("BUY_SAFE","BUY_RISKY") for c in dashboard_csps)
    if has_buy:
        return dashboard_csps

    bad_flags = {"EXTREME CSP EXPOSURE","OVER ALLOCATION","AT LOWS — SKIP","LOW YIELD","PREMIUM < $500"}
    candidates = [c for c in dashboard_csps
                  if c.get("action") == "WAIT"
                  and not any(f in bad_flags for f in (c.get("csp_flags") or []))]
    candidates.sort(key=lambda x: x.get("yield_30d",0), reverse=True)

    promoted = 0
    for c in candidates:
        if promoted >= 2: break
        c["action"]    = "BUY_RISKY"
        c["csp_flags"] = (c.get("csp_flags") or []) + ["BEST AVAILABLE"]
        c["sort_key"]  = 30 + min(c.get("yield_30d",0), 9.9)
        promoted += 1

    return dashboard_csps


def score_csp(opp: dict) -> int:
    """
    Compatibility shim — returns numeric score for legacy callers.
    New engine: csp_engine(). Score here is used only for sorting
    within same action tier when yield_30d is not available.
    """
    s = tier_weight(opp.get("tier",""))
    d = abs(opp.get("delta", 0))
    if d > 0.35:   s -= 2
    elif d > 0.30: s -= 1
    ivp = opp.get("ivp", 0)
    if ivp >= 40:  s += 2
    elif ivp >= 20: s += 1
    pb = opp.get("pullback_pct", 0)
    if pb > 15:    s += 2
    elif pb > 8:   s += 1
    ann = opp.get("annualized_return", 0)
    if 15 <= ann <= 35:  s += 2
    elif 8 <= ann < 15:  s += 1
    elif ann > 35:       s += 1
    return max(0, s)

def position_management_engine(pos: dict, mkt: dict, portfolio_value: float,
                               total_csp_exposure: float, spy_day_chg: float = 0) -> dict:
    """
    CSP / CC Position Management Engine v3.
    Philosophy: assignment is acceptable — it was priced in when you wrote the option.
    Actions: TAKE PROFIT, EARNINGS WARNING, HOLD
    - TAKE PROFIT: profit >= 90% (Core/Growth) or >= 80% (Speculative/Trading)
    - EARNINGS WARNING: earnings within 7 days — heads up only, not a forced close
    - HOLD: everything else
    """
    ticker        = pos.get("ticker", "")
    pos_type      = pos.get("type", "CSP")
    contracts     = pos.get("contracts", 1)
    strike        = pos.get("strike", 0)
    expiry_str    = pos.get("expiry", "")
    prem_received = abs(pos.get("premium_received", 0))
    mark          = pos.get("mark", 0)
    mark_src      = pos.get("mark_src", "chain")   # 'chain' = live, else may be stale
    tier          = pos.get("tier", "Opportunistic")
    earn_days     = pos.get("days_to_earnings", 999)
    assignment_intent = pos.get("assignment_intent", False)

    md         = mkt.get(ticker, {})
    underlying = md.get("price", 0)
    day_chg    = md.get("day_change_pct", 0)
    chg_3d     = md.get("change_3d_pct", 0)

    # ── DTE ────────────────────────────────────────────────────────────
    try:
        from datetime import datetime as _dt
        _raw = str(expiry_str).replace("-","")
        if len(_raw) >= 8:
            dte = (_dt.strptime(_raw[:8], "%Y%m%d") - _dt.now()).days
        else:
            dte = 30
    except Exception:
        dte = 30
    dte = max(0, dte)

    # ── Market regime ──────────────────────────────────────────────────
    volatile = abs(spy_day_chg) >= 0.015

    # ── Core metrics ───────────────────────────────────────────────────
    # Profit % — capped display at -100%
    if prem_received > 0:
        profit_pct       = round((prem_received - mark) / prem_received * 100, 1)
        remaining_prem   = round(mark / prem_received * 100, 1)
    else:
        profit_pct       = 0
        remaining_prem   = 100

    profit_pct = max(profit_pct, -100)

    # Dollar P&L
    pnl_dollar = round((prem_received - mark) * contracts * 100, 0)

    # Breakeven (PRIMARY risk metric per v2 spec)
    if pos_type == "CSP":
        breakeven          = round(strike - prem_received, 2)
        dist_to_breakeven  = round((underlying - breakeven) / underlying * 100, 1) if underlying > 0 else 99
        dist_to_strike     = round((underlying - strike) / underlying * 100, 1) if underlying > 0 else 99
    else:  # CC
        breakeven          = round(strike + prem_received, 2)
        dist_to_breakeven  = round((breakeven - underlying) / underlying * 100, 1) if underlying > 0 else 99
        dist_to_strike     = round((strike - underlying) / underlying * 100, 1) if underlying > 0 else 99

    # ── Mark credibility check ─────────────────────────────────────────
    # Guard against STALE marks only. A live chain NBBO fetched this scan is
    # trusted as-is: on extreme-vol names (NBIS, CRDO, CLS) a put 30%+ OTM can
    # legitimately be worth $10+ because the stock moves 15%+ in a day — deep
    # OTM does NOT imply near-worthless there (P2 in TRADING_PRINCIPLES.md).
    # Only the position-feed fallback ('position_mv'/'none') can lag a fast
    # move, so the "deep OTM but low profit ⇒ not credible" heuristic applies
    # to those sources alone.
    if (mark_src not in ("chain", "chain_near")
            and dist_to_strike >= 20 and profit_pct < 60):
        # deep OTM but stale-prone mark implies <60% captured — not credible
        mark_src = "incredible"

    # Velocity signals — only meaningful when risk is present
    is_fast_drop       = day_chg <= -0.020
    is_accelerating    = chg_3d  <= -0.040
    is_fast_rally      = day_chg >= 0.020

    # ── BIG MOVE detection (event-driven review alert) ─────────────────
    # The move is the ONLY trigger. A big favorable move (CC: stock down /
    # CSP: stock up) on a name you hold a short option means "review to close."
    # No profit floor, no strike gate — context is shown in the alert, not gated.
    if pos_type == "CC":
        # short call benefits from the stock falling
        big_move_review = (day_chg <= -BIGMOVE_1D) or (chg_3d <= -BIGMOVE_3D)
        _move_desc = (f"dropped {abs(day_chg)*100:.1f}% today"
                      if day_chg <= -BIGMOVE_1D
                      else f"fell {abs(chg_3d)*100:.1f}% over 3 days")
    else:  # CSP
        # short put benefits from the stock rising
        big_move_review = (day_chg >= BIGMOVE_1D) or (chg_3d >= BIGMOVE_3D)
        _move_desc = (f"rose {day_chg*100:.1f}% today"
                      if day_chg >= BIGMOVE_1D
                      else f"gained {chg_3d*100:.1f}% over 3 days")
    # Position context for the alert (shown, not gated)
    _pos_ctx = ("ITM" if dist_to_strike <= 0
                else f"{dist_to_strike:.0f}% OTM")

    # Safe zone: long DTE + good cushion → cannot be DEFENSIVE, ignore velocity
    in_safe_zone = dte > 30 and dist_to_breakeven >= 10

    # Earnings zone
    if earn_days < 3:   earn_zone = "CRITICAL"
    elif earn_days <= 7: earn_zone = "DANGER"
    elif earn_days <= 14: earn_zone = "CAUTION"
    else:               earn_zone = "SAFE"

    earn_risk = earn_days <= 5

    # Per-tier profit thresholds
    is_speculative = tier in ("Opportunistic",) or pos.get("speculative", False)
    take_profit_threshold = 80.0 if is_speculative else 90.0

    # ── P&L swing since last scan (prev value injected by caller) ──────
    # prev_profit_pct comes from the previous scan's results.json. None if the
    # position wasn't in the last scan (new position / first run).
    prev_profit = pos.get("prev_profit_pct", None)
    pnl_swing_txt = ""
    big_swing = False
    if prev_profit is not None and prem_received > 0 and mark_src != "incredible":
        _improve = profit_pct - prev_profit
        if (_improve >= PNLSWING_MIN_IMPROVE or
                (prev_profit <= PNLSWING_FLIP_FROM and profit_pct >= PNLSWING_FLIP_TO)):
            big_swing = True
        if abs(_improve) >= 10:
            def _pl_word(p):
                return f"{p:.0f}% profit" if p >= 0 else f"{abs(p):.0f}% loss"
            pnl_swing_txt = (f" Swing since last scan: {_pl_word(prev_profit)} → "
                             f"{_pl_word(profit_pct)}.")

    # ── Helper to return result ────────────────────────────────────────
    def R(action, reason):
        priority = {"BIG MOVE": 0, "P&L SWING": 1, "TAKE PROFIT": 2,
                    "EARNINGS WARNING": 3, "HOLD": 4}
        return {
            "action":          action,
            "reason":          reason,
            "profit_pct":      profit_pct,
            "pnl_dollar":      pnl_dollar,
            "dte":             dte,
            "breakeven":       breakeven,
            "dist_to_be_pct":  dist_to_breakeven,
            "dist_to_strike":  dist_to_strike,
            "remaining_prem":  remaining_prem,
            "earn_zone":       earn_zone,
            "earn_days":       earn_days,
            "mark_src":        mark_src,
            "prev_profit_pct": prev_profit,
            "sort_priority":   priority.get(action, 4),
        }

    # ── Stackable context notes (P15: a good exit day is a CONFLUENCE —
    # the alert reason carries every active factor, not just the top one) ──
    earn_note = ""
    if earn_days <= 7:
        _inside = " — inside this expiry" if earn_days <= dte else ""
        earn_note = f" ⚠ Earnings in {earn_days}d{_inside}."
    tp_note = (f" ✅ {profit_pct:.0f}% captured (≥{take_profit_threshold:.0f}% take-profit level)."
               if prem_received > 0 and mark_src != "incredible"
               and profit_pct >= take_profit_threshold else "")

    # ── BIG MOVE — REVIEW (event-driven, highest priority) ─────────────
    # A big favorable move (CC: stock down / CSP: stock up) on a name you hold
    # a short option. The move alone triggers it — profit and strike distance
    # are shown as context so you can decide, but they do NOT gate the alert.
    if big_move_review:
        _close_cost = round(mark * contracts * 100, 0)
        _stale = mark_src not in ("chain", "chain_near")
        if _stale:
            return R("BIG MOVE",
                     f"{ticker} {_move_desc} — your {pos_type} ${strike:g} is {_pos_ctx}, "
                     f"{dte}d left. Big move — check the live option price and review "
                     f"whether to close (position mark may be stale)." + earn_note)
        _pl_txt = (f"{profit_pct:.0f}% profit" if profit_pct >= 0
                   else f"{abs(profit_pct):.0f}% loss")
        return R("BIG MOVE",
                 f"{ticker} {_move_desc} — your {pos_type} ${strike:g} is {_pos_ctx}, "
                 f"now at {_pl_txt} (${_close_cost:,.0f} to close, {dte}d left). "
                 f"Big move — review whether to close before it reverses."
                 + pnl_swing_txt + earn_note + tp_note)

    # ── P&L SWING — the position itself recovered hard since last scan ─
    # Catches "hugely negative yesterday → positive/breakeven today" even when
    # today's underlying move alone is below BIGMOVE_1D (e.g. the recovery
    # happened across two days). Only fires on credible marks.
    if big_swing:
        _close_cost = round(mark * contracts * 100, 0)
        _pl_txt = (f"{profit_pct:.0f}% profit" if profit_pct >= 0
                   else f"{abs(profit_pct):.0f}% loss")
        return R("P&L SWING",
                 f"{ticker} {pos_type} ${strike:g} recovered hard:"
                 + pnl_swing_txt +
                 f" Now {_pos_ctx}, ${_close_cost:,.0f} to close, {dte}d left — "
                 f"exit window if you don't want this position."
                 + earn_note + tp_note)

    # ── TAKE PROFIT ────────────────────────────────────────────────────
    # Act now — premium has decayed enough, risk/reward favours closing
    if profit_pct >= take_profit_threshold:
        return R("TAKE PROFIT",
                 f"{profit_pct}% profit captured — close now before reversal "
                 f"(threshold: {take_profit_threshold:.0f}%)")

    # ── EARNINGS WARNING ───────────────────────────────────────────────
    # Heads up only — decision is yours, but you need to know
    if earn_days <= 7:
        return R("EARNINGS WARNING",
                 f"Earnings in {earn_days}d — decide before event "
                 f"({profit_pct}% profit captured, {dte} DTE remaining)")

    # ── HOLD ───────────────────────────────────────────────────────────
    if pos_type == "CSP":
        return R("HOLD", f"{profit_pct}% profit, DTE {dte}, breakeven ${breakeven:.2f} "
                         f"({dist_to_breakeven}% cushion)")
    else:
        return R("HOLD", f"{profit_pct}% profit, DTE {dte}, "
                         f"{dist_to_strike}% below call strike ${strike:.2f}")


def score_leaps(opp: dict) -> int:
    """
    Canonical LEAPS score (max 13):
    Tier(3) + Delta(3) + Extrinsic(3) + DTE(2) + IVP(2)
    LEAPS = stock replacement — extrinsic is primary cost signal.
    """
    s = tier_weight(opp.get("tier",""))
    # Extrinsic quality — PRIMARY cost signal
    ext = opp.get("extrinsic_pct", 100)
    if ext < 15:            s += 3
    elif ext < 20:          s += 2
    elif ext < 25:          s += 1
    # Delta quality — higher delta = more stock-like = better (no upper ceiling)
    d = abs(opp.get("delta", 0))
    if d >= 0.88:           s += 3   # very deep ITM — maximum tracking
    elif d >= 0.80:         s += 2   # solid deep ITM
    elif d >= 0.75:         s += 1   # acceptable floor
    # DTE quality — longer is better for stock replacement
    dte = opp.get("dte", 0)
    if dte > 600:           s += 2
    elif dte > 500:         s += 1
    # IVP context — lower is better for buying
    ivp = opp.get("ivp", 100)
    if ivp < 20:            s += 2
    elif ivp < 40:          s += 1
    return max(0, s)

def score_allocation(pos: dict) -> int:
    """
    Conviction score for Positions page (Patch 10).
    Components: Tier + Quality + Position Status + Price Opportunity + Trend
    Score >= 6 = BUY, 4-5 = ADD, 2-3 = HOLD, 0-1 = TRIM, <0 = REDUCE
    """
    s = tier_weight(pos.get("tier",""))  # Tier quality: Core=3, Growth=2, etc

    # Position status vs tier target
    ps = pos.get("pos_status","")
    if ps == "Not Held":      s += 0   # neutral — speculative, no automatic buy pressure
    elif ps == "Underweight": s += 3   # strong buy signal
    elif ps == "On Target":   s += 1   # hold/add at current level
    elif ps == "Overweight":  s -= 3   # reduce

    # Price opportunity — only adds points if not already overweight
    po = pos.get("price_opp","")
    if ps != "Overweight":
        if po == "Pullback":      s += 2   # attractive entry
        elif po == "Neutral":     s += 1   # fair
        elif po == "Near High":   s -= 2   # expensive entry
    else:
        # Overweight: near high makes it worse, pullback doesn't help
        if po == "Near High":   s -= 2

    # Trend context
    if pos.get("above_ma200", True):   s += 1   # uptrend bonus

    # Stability bonus: Core names get conviction boost
    if pos.get("tier","") == "Core":   s += 1
    return s

def position_decision(current_pct: float, tier: str) -> str:
    """
    Position sizing decision per code patch guide section 8.
    Returns BUY / HOLD / TRIM based on current exposure vs tier allocation.
    """
    allocs = TIER_ALLOCATIONS.get(tier, TIER_ALLOCATIONS["Opportunistic"])
    min_alloc, max_alloc = allocs
    if current_pct < min_alloc:
        return "BUY"
    elif current_pct > max_alloc:
        return "TRIM"
    else:
        return "HOLD"


def score_to_action(score: int) -> str:
    """Map allocation score to action label."""
    if score >= 6:   return "BUY"
    elif score >= 4: return "ADD"
    elif score >= 2: return "HOLD"
    elif score >= 0: return "TRIM"
    else:            return "REDUCE"


def unified_score(quality: float, safety: float, income: float,
                  timing: float, liquidity: float) -> float:
    """
    Unified weighted score per code patch guide.
    All inputs normalized 0-1 before calling.
    Returns 0-1 score. Multiply by 10 for display.
    Weights: Quality 30%, Safety 25%, Income 20%, Timing 15%, Liquidity 10%
    """
    return (0.30 * quality +
            0.25 * safety  +
            0.20 * income  +
            0.15 * timing  +
            0.10 * liquidity)


def score_unified(opp: dict, mode: str = "CSP") -> float:
    """
    Build component scores from opportunity dict and call unified_score().
    Returns 0-10 for display.
    """
    tier = opp.get("tier", "Opportunistic")

    # Quality (0-1)
    quality = {"Core": 1.0, "Growth": 0.85, "Cyclical": 0.55, "Opportunistic": 0.20}.get(tier, 0.20)

    # Safety (0-1) — delta constraint applied here per patch guide section 3
    safety = 0.0
    d = abs(opp.get("delta", 0))
    if mode in ("CC", "PIO", "PMCC"):
        strike = opp.get("strike", 0)
        be = opp.get("breakeven", 0) or opp.get("avg_cost", 0)
        if be and be > 0 and strike > 0:
            buf = (strike - be) / be * 100
            safety = 1.0 if buf > 15 else 0.85 if buf > 10 else 0.65 if buf > 5 else 0.35
        # Delta constraint: penalize >0.40
        if d > 0.40: safety -= 0.2
    elif mode == "CSP":
        pb = opp.get("pullback_pct", 0)
        safety = 1.0 if pb > 20 else 0.65 if pb > 12 else 0.35 if pb > 8 else 0.15
        warnings = opp.get("warnings", [])
        if "Near 52w high" in warnings or ">8% above MA50" in warnings:
            safety -= 0.3
        # Delta constraint: penalize >0.35
        if d > 0.35: safety -= 0.2
    elif mode == "LEAPS":
        ext = opp.get("extrinsic_pct", 100)
        safety = 1.0 if ext < 10 else 0.85 if ext < 15 else 0.65 if ext < 20 else 0.35 if ext < 25 else 0.10
        # Delta constraint: require 0.70-0.90
        if not (0.70 <= d <= 0.90): safety -= 0.3
    safety = max(0.0, min(1.0, safety))

    # Income (0-1)
    ann = opp.get("annualized_return", 0)
    if mode == "LEAPS":
        dte = opp.get("dte", 0)
        income = 1.0 if dte > 600 else 0.75 if dte > 500 else 0.50
    else:
        income = (1.0 if 15 <= ann <= 35 else
                  0.75 if 8 <= ann < 15 else
                  0.50 if ann > 35 else 0.25)

    # Timing (0-1) — IVP context, no hard filter
    ivp = opp.get("ivp", 50)
    if mode == "LEAPS":
        timing = 1.0 if ivp < 20 else 0.65 if ivp < 35 else 0.35 if ivp < 50 else 0.10
    else:
        timing = 1.0 if ivp > 60 else 0.65 if ivp > 40 else 0.35 if ivp > 25 else 0.10

    # Liquidity (0-1)
    oi = opp.get("open_interest", opp.get("oi", 0))
    liquidity = 1.0 if oi >= 1000 else 0.75 if oi >= 500 else 0.50 if oi >= 100 else 0.25

    raw = unified_score(quality, safety, income, timing, liquidity)
    return round(raw * 10, 2)  # scale to 0-10


# ── Stock universe ────────────────────────────────────────────────────────────
CORE_STOCKS = {
    "AAPL", "AMZN", "GOOGL", "MSFT", "NVDA", "TSM",
    "MELI", "IBKR",
}
GROWTH_STOCKS = {
    "NOW", "NFLX", "PLTR",
}
CYCLICAL_STOCKS = {
    "MU", "FIX", "CRDO", "TSLA",
}
OPPORTUNISTIC_STOCKS = {
    "CLS", "GRBK", "IBIT", "KNX", "LULU", "NBIS", "NVO", "POWL",
}
# Very Speculative tier (added 2026-06-22) — was missing from ALL_TICKERS,
# so these were configured but never actually pulled into market data / scanned.
VERY_SPECULATIVE_STOCKS = {
    "UBER", "GRAB", "PATH", "MSTR", "PYPL", "SPCX",
}

ALL_TICKERS = sorted(
    CORE_STOCKS | GROWTH_STOCKS | CYCLICAL_STOCKS | OPPORTUNISTIC_STOCKS
    | VERY_SPECULATIVE_STOCKS
)

# TARGET_RANGES defined at top of file

# Speculative — wider OTM buffers required
SPECULATIVE = {"IBIT", "CLS", "GRBK", "KNX", "LULU", "NBIS", "NVO", "POWL"} | VERY_SPECULATIVE_STOCKS

# LEAPS/CSP only — no CC income generation
LEAPS_ONLY = {"IBIT"}

# DEPRECATED — spike CC now fires for ANY held 100+ share position, not a fixed
# list. Kept for reference only; no longer gates the spike scanner.
SPIKE_CC_CANDIDATES = {"IBIT", "PLTR"}

# Positions dashboard exclusion list — non-tradable, synthetic, or explicitly excluded
# Per spec section 4: excluded symbols must not appear in rankings, actions, or summaries
EXCLUDED_SYMBOLS = {
    "XIOR.CP27",   # Non-tradable synthetic/warrant — excluded per user spec
    "HOM.U",       # Income trust — not actively traded options
    "EDEN", "SHUR", "VNA", "HTWS", "SGRO", "SVI",  # non-tradable / synthetic
    # Add any other non-US or non-tradable holdings here
}

# Grouped tickers — economically equivalent share classes
# Per spec section 6: combined exposure drives recommendation, preferred symbol displayed
GROUPED_TICKERS = {
    "GOOG":  "GOOGL",   # GOOG class C → prefer GOOGL class A for recommendations
}

# Stricter delta rules for specific stocks
STRICT_DELTA = {
    "PLTR": (0.20, 0.25),   # Extreme valuation, stricter
    "TSLA": (0.20, 0.28),   # High volatility, be selective
}

# IVP minimums by strategy (overrides global defaults for specific stocks)
STRICT_IVP = {
    "PLTR": 40,    # Needs elevated IV to justify premium selling
    "BABA": 35,
}

# Options income priority — these 6 have best liquidity/volatility structure
# Scanner scores these higher automatically
OPTIONS_INCOME_PRIORITY = {"NVDA", "MSFT", "AMZN", "TSLA", "META", "NFLX"}


def position_check(ticker: str, ibkr: dict) -> dict:
    """
    Check current position size and return sizing info.
    Returns: status, current_pct, max_pct, room_usd, quantity, tier, avg_cost
    """
    TIER_MAX = {"Core": 8.0, "Growth": 5.0, "Cyclical": 4.0, "Opportunistic": 2.5}

    # Determine tier
    tier = "Core"
    if ticker in CORE_STOCKS:        tier = "Core"
    elif ticker in GROWTH_STOCKS:    tier = "Growth"
    elif ticker in CYCLICAL_STOCKS:  tier = "Cyclical"
    else:                            tier = "Opportunistic"

    max_pct     = TIER_MAX.get(tier, 2.5)
    # Tier-aware max — use canonical TIER_MAX_PCT
    tier_key = ("Core" if ticker in CORE_STOCKS else
                "Growth" if ticker in GROWTH_STOCKS else
                "Cyclical" if ticker in CYCLICAL_STOCKS else "Opportunistic")
    tier_max_frac = TIER_MAX_PCT.get(tier_key, 0.03)
    if max_pct / 100 < tier_max_frac * 0.5:  # if original max_pct is too low, use tier
        max_pct = tier_max_frac * 100
    max_usd     = PORTFOLIO_SIZE * max_pct / 100

    # Look up current position from IBKR/Schwab
    pos         = ibkr.get(ticker, {})
    qty         = float(pos.get("qty", 0) or pos.get("quantity", 0) or 0)
    avg_cost    = float(pos.get("avg_cost", 0) or pos.get("averagePrice", 0) or 0)
    mkt_val     = float(pos.get("market_value", 0) or pos.get("marketValue", qty * avg_cost) or 0)

    current_pct = round(mkt_val / PORTFOLIO_SIZE * 100, 2) if PORTFOLIO_SIZE > 0 else 0
    room_usd    = max(0, max_usd - mkt_val)

    if current_pct >= max_pct:
        status = "OVERWEIGHT"
    elif current_pct >= max_pct * 0.8:
        status = "NEAR_MAX"
    elif current_pct >= max_pct * 0.5:
        status = "NORMAL"
    else:
        status = "LIGHT"

    return {
        "ticker":      ticker,
        "tier":        tier,
        "status":      status,
        "current_pct": current_pct,
        "max_pct":     max_pct,
        "max_usd":     round(max_usd, 0),
        "room_usd":    round(room_usd, 0),
        "quantity":    qty,
        "avg_cost":    avg_cost,
        "market_value": mkt_val,
    }


def stock_quality_check(ticker: str, md: dict, earn_date) -> dict:
    """
    Quality gate for all strategies.
    Returns dict with: passes, hard_stop, leaps_hard_stop, quality_score,
    pullback, pullback_pct, warnings, checks, earnings_status, near_high,
    ma50_extended, pct_above_ma50, days_to_earnings.
    """
    price     = md.get("price", 0)
    w52_high  = md.get("w52_high", price * 1.3)
    w52_low   = md.get("w52_low",  price * 0.7)
    ma50      = md.get("ma50",  price)
    ma200     = md.get("ma200", price * 0.95)
    volume    = md.get("volume", 0)
    avg_vol   = md.get("avg_volume", volume)

    # ── Price / volume checks ────────────────────────────
    price_ok  = price >= 10
    volume_ok = volume >= 50_000 or avg_vol >= 50_000

    # ── Gap / spike check ────────────────────────────────
    day_change = md.get("day_change_pct", 0)
    no_gap     = abs(day_change) < GAP_RISK_PCT      # income mode: strict
    no_gap_opp = abs(day_change) < GAP_RISK_PCT_OPP  # opportunistic mode: lenient

    # ── 52-week high proximity ────────────────────────────
    pir        = position_in_range(price, w52_low, w52_high)
    near_high  = pir > 0.92               # within 8% of 52w high
    pullback   = pullback_from_high(price, w52_high)

    # ── MA50 extension check ──────────────────────────────
    pct_above_ma50 = (price - ma50) / ma50 if ma50 > 0 else 0
    ma50_extended  = pct_above_ma50 > 0.08  # >8% above 50MA

    # ── MA200 check ───────────────────────────────────────
    above_ma200 = price >= ma200 * 0.97   # within 3% tolerance

    # ── Earnings check ────────────────────────────────────
    days_to_earnings = 999
    if earn_date:
        try:
            delta = (earn_date - datetime.now()).days
            days_to_earnings = max(0, delta)
        except:
            pass

    if days_to_earnings < 14:
        earnings_status = "hard_stop"
    elif days_to_earnings < 35:
        earnings_status = "warning"
    else:
        earnings_status = "ok"

    # ── Build checks dict ─────────────────────────────────
    checks = {
        "price_ok":      price_ok,
        "volume_ok":     volume_ok,
        "no_gap":        no_gap,
        "not_near_high": not near_high,
        "ma50_ok":       not ma50_extended,
        "above_ma200":   above_ma200,
    }

    # ── Quality score 0-5 ─────────────────────────────────
    quality_score = sum([
        1 if price_ok else 0,
        1 if volume_ok else 0,
        1 if no_gap else 0,
        1 if not near_high else 0,
        1 if not ma50_extended else 0,
    ])

    # ── Warnings ──────────────────────────────────────────
    warnings = []
    if near_high:          warnings.append("Near 52w high")
    if ma50_extended:      warnings.append(">8% above MA50")
    if not above_ma200:    warnings.append("Below 200MA")
    if earnings_status == "warning": warnings.append(f"Earnings in {days_to_earnings}d")
    if not no_gap:         warnings.append(f"Gap/spike {day_change:.1%}")

    # ── Hard stops ────────────────────────────────────────
    hard_stop = (
        not price_ok or
        not volume_ok or
        not no_gap or
        near_high or
        ma50_extended or
        earnings_status == "hard_stop"
    )

    # LEAPS hard stop is more lenient — only earnings and price matter
    leaps_hard_stop = (
        not price_ok or
        not volume_ok or
        earnings_status == "hard_stop"
    )

    return {
        "checks":           checks,
        "quality_score":    quality_score,
        "pullback":         pullback,
        "pullback_pct":     round(pullback * 100, 1),
        "days_to_earnings": days_to_earnings,
        "earnings_status":  earnings_status,
        "pct_above_ma50":   round(pct_above_ma50 * 100, 1),
        "ma50_extended":    ma50_extended,
        "near_high":        near_high,
        "above_ma200":      above_ma200,
        "warnings":         warnings,
        "hard_stop":        hard_stop,
        "leaps_hard_stop":  leaps_hard_stop,
        "passes":           not hard_stop and quality_score >= 3,
    }


def get_position_status(current_pct: float) -> str:
    """
    Determine position status based on current exposure %.
    Drives delta selection for Position Income Optimization.
    """
    if current_pct < 4:
        return "light"        # aggressive income — 0.30-0.40 delta
    elif current_pct < 8:
        return "normal"       # normal — 0.20-0.25 delta
    elif current_pct < 12:
        return "heavy"        # conservative — 0.10-0.15 delta
    else:
        return "overweight"   # reduce — sell closer calls, willing to assign


def get_pnl_status(avg_cost: float, current_price: float) -> str:
    """Determine P&L status for delta selection."""
    if avg_cost <= 0:
        return "unknown"
    pnl_pct = (current_price - avg_cost) / avg_cost
    if pnl_pct > 0.10:
        return "profit"       # delta 0.30-0.40
    elif pnl_pct > -0.05:
        return "breakeven"    # delta 0.20-0.25
    else:
        return "loss"         # delta 0.10-0.15


def find_position_income_cc(ticker, price, qty, avg_cost, contracts,
                             ivdata, position_status, pnl_status, already_covered=0) -> tuple:
    """
    Position Income Optimization — Mode 4.
    Sell calls on existing holdings to generate income.
    Rules from framework:
    - Sell calls ABOVE break-even (cost basis - premiums collected)
    - Delta based on P&L status:
      profit    → 0.30-0.40 (more aggressive, happy to reduce)
      breakeven → 0.20-0.25 (protect position)
      loss      → 0.10-0.15 (very conservative, protect recovery)
    - Ignores: 200MA rule, pullback requirements
    - Focus: income vs risk, not trade perfection
    - Only for stocks where you hold ≥100 shares
    """
    if qty < 100:
        return None, {}

    # Delta range based on P&L status
    if pnl_status == "profit":
        d_min, d_max = 0.30, 0.40
        status_label = "📈 Profit position"
    elif pnl_status == "loss":
        d_min, d_max = 0.10, 0.15
        status_label = "📉 Loss position — conservative"
    else:
        d_min, d_max = 0.20, 0.25
        status_label = "➡️ Near break-even"

    atm_iv     = ivdata.get("iv_current", ivdata.get("atm_iv", 0.3))
    breakeven  = avg_cost  # simplified — would subtract collected premiums
    candidates = []

    for c in contracts:
        if c.get("option_type") != "C":
            continue
        try:
            expiry = datetime.strptime(c["expiry"], "%Y-%m-%d")
            dte    = (expiry - datetime.now()).days
            if not (14 <= dte <= 60):
                continue

            strike = float(c["strike"])
            # Must be above break-even
            if strike <= breakeven:
                continue
            if strike <= price:
                continue  # must be OTM

            bid    = float(c.get("nbbo_bid", 0) or 0)
            ask    = float(c.get("nbbo_ask", 0) or 0)
            if bid <= 0: continue  # stale/no market — Schwab returned bad quote
            spread_pct = (ask - bid) / ask if ask > 0 else 1.0
            mid    = bid + (ask - bid) * 0.25 if spread_pct > 0.30 else (bid + ask) / 2
            if mid < 0.50: continue

            delta  = float(c.get("delta", 0) or 0)
            if abs(delta) == 0:
                delta = estimate_delta(price, strike, dte, atm_iv, "C")
            if delta is None: continue
            delta = abs(delta)
            if not (d_min <= delta <= d_max):
                continue

            # Liquidity
            oi     = int(c.get("open_interest", 0) or 0)
            vol    = int(c.get("volume", 0) or 0)
            spread = (ask - bid) / ask if ask > 0 else 1
            if oi < MIN_OPEN_INTEREST: continue
            if vol < MIN_DAILY_VOLUME: continue
            if spread > MAX_BID_ASK_SPREAD: continue

            annualized    = (mid / price) * (365 / dte) * 100
            uncovered     = max(0, qty - already_covered)
            max_contracts = max(0, int(uncovered / 100))
            if max_contracts == 0: continue
            prem_pct      = round(mid / strike * 100, 2)

            score = delta * mid * (ivdata["ivp"] / 50)
            candidates.append({
                "strike":            strike,
                "expiry":            expiry.strftime("%Y-%m-%d"),
                "dte":               dte,
                "bid":               round(bid, 2),
                "ask":               round(ask, 2),
                "premium":           round(mid, 2),
                "delta":             round(delta, 2),
                "annualized_return": round(annualized, 1),
                "prem_pct":          prem_pct,
                "max_contracts":     max_contracts,
                "avg_cost":          avg_cost,
                "breakeven":         round(breakeven, 2),
                "status_label":      status_label,
                "pnl_status":        pnl_status,
                "score":             score,
            })
        except:
            continue

    if not candidates:
        return None, {}

    best = max(candidates, key=lambda x: x["score"])
    return best, {"signal": f"✅ Position Income | {status_label}"}


def detect_price_drop(ticker: str, md: dict) -> dict:
    """
    Detect if a stock has dropped 8%+ recently — triggers Post-Drop CSP mode.
    Opposite of spike detection.
    Checks today's move AND position relative to MA50 (extended below = recent drop).
    """
    price          = md.get("price", 0)
    prev_close     = md.get("prev_close", price)
    day_change     = (price - prev_close) / prev_close if prev_close > 0 else 0
    ma50           = md.get("ma50", 0)
    ma200          = md.get("ma200", 0)
    pct_above_ma50 = md.get("pct_above_ma50", 0)
    above_ma200    = md.get("above_ma200", True)

    today_drop     = day_change <= -DROP_TRIGGER_MIN        # fell 8%+ today
    recent_drop    = pct_above_ma50 <= -DROP_TRIGGER_MIN    # below 50MA significantly

    return {
        "is_drop":        today_drop or recent_drop,
        "today_change":   round(day_change * 100, 1),
        "pct_below_ma50": round(pct_above_ma50 * 100, 1),
        "above_ma200":    above_ma200,
        "trigger":        "today" if today_drop else "recent" if recent_drop else "none",
    }


def find_drop_csp(ticker, price, contracts, ivdata, pir, quality,
                  drop_info, tier, sizing) -> tuple:
    """
    Post-Drop CSP — sell fear after sharp downside move.

    Key differences from Income CSP:
    - Gap filter DISABLED (drop is the trigger)
    - Near-high restriction DISABLED
    - More conservative delta (0.20-0.25)
    - Reduced position size (60% of normal)
    - Must be above 200MA (no broken stocks)
    - Only Core and Growth tier stocks
    - IVP > 40 required (elevated fear = elevated premium)
    - Strike below real support (MA50 or swing low)
    """
    # Quality gates
    if not drop_info["above_ma200"]:
        return None, {"signal": "❌ Below 200MA — skip (structurally broken)"}
    if tier not in DROP_CSP_ALLOWED_TIERS:
        return None, {"signal": f"❌ {tier} tier not allowed for post-drop CSP"}
    # IVP no longer a hard gate — scoring only (P2 punchlist)
    # Low IVP post-drop still shown, scores lower on timing
    if (quality.get("days_to_earnings") is not None
            and 0 < quality["days_to_earnings"] <= DROP_EARNINGS_MIN):
        return None, {"signal": f"❌ Earnings in {quality['days_to_earnings']}d — hard stop"}

    atm_iv     = ivdata.get("atm_iv", 0.3)
    ma50       = 0  # will use strike selection logic
    candidates = []

    for c in contracts:
        if c.get("option_type") != "P":
            continue
        try:
            expiry = datetime.strptime(c["expiry"], "%Y-%m-%d")
            dte    = (expiry - datetime.now()).days
            if not (DROP_CSP_DTE_MIN <= dte <= DROP_CSP_DTE_MAX):
                continue

            strike = float(c["strike"])
            if strike >= price: continue  # must be OTM

            bid    = float(c.get("nbbo_bid", 0) or 0)
            ask    = float(c.get("nbbo_ask", 0) or 0)
            if bid <= 0: continue  # stale/no market — Schwab returned bad quote
            spread_pct = (ask - bid) / ask if ask > 0 else 1.0
            mid    = bid + (ask - bid) * 0.25 if spread_pct > 0.30 else (bid + ask) / 2
            if mid < 1.0: continue

            # Use real delta from Schwab if available
            delta  = float(c.get("delta", 0) or 0)
            if abs(delta) == 0:
                delta = estimate_delta(price, strike, dte, atm_iv, "P")
            if delta is None: continue
            delta = abs(delta)
            if not (DROP_CSP_DELTA_MIN <= delta <= DROP_CSP_DELTA_MAX):
                continue

            # Liquidity
            oi     = int(c.get("open_interest", 0) or 0)
            vol    = int(c.get("volume", 0) or 0)
            spread = (ask - bid) / ask if ask > 0 else 1
            if oi < MIN_OPEN_INTEREST: continue
            if vol < MIN_DAILY_VOLUME: continue
            if spread > MAX_BID_ASK_SPREAD: continue

            # Premium efficiency
            prem_pct = mid / strike
            if prem_pct < MIN_PREMIUM_PCT_30_45: continue

            annualized  = (mid / strike) * (365 / dte) * 100

            # Reduced position size — 60% of normal
            normal_size = PORTFOLIO_SIZE * get_max_alloc(ticker)
            reduced_size = normal_size * DROP_SIZE_FACTOR
            max_contracts = max(1, int(reduced_size / (strike * 100)))

            # Extrinsic quality label
            intrinsic   = max(0, strike - price)
            extrinsic   = max(0, mid - intrinsic)
            ext_pct     = (extrinsic / mid * 100) if mid > 0 else 0

            score = (ivdata["ivp"] / 100) * delta * mid * (1 - abs(dte-35)/35)
            candidates.append({
                "strike":          strike,
                "expiry":          expiry.strftime("%Y-%m-%d"),
                "dte":             dte,
                "bid":             round(bid, 2),
                "ask":             round(ask, 2),
                "premium":         round(mid, 2),
                "delta":           round(delta, 2),
                "annualized_return": round(annualized, 1),
                "prem_pct":        round(prem_pct * 100, 2),
                "max_contracts":   max_contracts,
                "collateral":      round(strike * 100 * max_contracts, 0),
                "score":           score,
            })
        except:
            continue

    if not candidates:
        return None, {}

    best = max(candidates, key=lambda x: x["score"])
    return best, {"signal": f"✅ Post-Drop CSP | {drop_info['today_change']:+.1f}% | IVP {ivdata['ivp']:.0f}%"}


def detect_price_spike(ticker: str, md: dict) -> dict:
    """
    Detect if a stock has spiked 8%+ upward recently.
    Opportunistic mode is TRIGGERED by spikes (opposite of income mode which skips them).
    """
    price          = md.get("price", 0)
    prev_close     = md.get("prev_close", price)
    day_change     = (price - prev_close) / prev_close if prev_close > 0 else 0
    pct_above_ma50 = md.get("pct_above_ma50", 0)

    today_spike   = day_change >= OPP_SPIKE_MIN        # big move today
    extended_run  = pct_above_ma50 >= OPP_SPIKE_MIN    # stock has run up recently

    return {
        "is_spike":       today_spike or extended_run,
        "today_change":   round(day_change * 100, 1),
        "pct_above_ma50": round(pct_above_ma50 * 100, 1),
        "trigger":        "today" if today_spike else "extended" if extended_run else "none",
    }


def find_spike_cc(ticker, price, qty, avg_cost, contracts, ivdata, spike_info) -> tuple:
    """
    Opportunistic CC after volatility spike.
    Rules from framework doc:
    - DTE: 14-30 (shorter — capture fast vol contraction)
    - Delta: 0.20-0.35 (strikes above spike high)
    - IVP > 40 required
    - Earnings hard stop: 7 days
    - Must hold shares (qty >= 100)
    """
    if qty < 100:
        return None, {}
    # IVP is scoring context only — no hard gate (P2 punchlist)
    # Low IVP spike still shown, just scores lower on timing

    atm_iv     = ivdata.get("atm_iv", 0.3)
    candidates = []

    for c in contracts:
        if c.get("option_type") != "C":
            continue
        try:
            expiry = datetime.strptime(c["expiry"], "%Y-%m-%d")
            dte    = (expiry - datetime.now()).days
            if not (OPP_CC_DTE_MIN <= dte <= OPP_CC_DTE_MAX):
                continue
            strike = float(c["strike"])
            if strike <= price: continue  # must be OTM
            bid    = float(c.get("nbbo_bid", 0) or 0)
            ask    = float(c.get("nbbo_ask", 0) or 0)
            if bid <= 0: continue  # stale/no market — Schwab returned bad quote
            spread_pct = (ask - bid) / ask if ask > 0 else 1.0
            mid    = bid + (ask - bid) * 0.25 if spread_pct > 0.30 else (bid + ask) / 2
            if mid < 0.50: continue

            delta = float(c.get("delta", 0) or 0)
            if delta == 0:
                delta = estimate_delta(price, strike, dte, atm_iv, "C")
            if delta is None or not (OPP_CC_DELTA_MIN <= abs(delta) <= OPP_CC_DELTA_MAX):
                continue

            oi     = int(c.get("open_interest", 0) or 0)
            vol    = int(c.get("volume", 0) or 0)
            spread = (ask - bid) / ask if ask > 0 else 1
            if oi < MIN_OPEN_INTEREST: continue
            if vol < MIN_DAILY_VOLUME: continue
            if spread > MAX_BID_ASK_SPREAD: continue

            # CC: use strike as collateral basis (matches CSP formula)
            annualized     = (mid / strike) * (365 / dte) * 100
            protection_pct = round((mid / price) * 100, 1)
            max_contracts  = max(1, int(qty / 100))
            score          = (ivdata["ivp"] / 100) * abs(delta) * mid

            candidates.append({
                "strike":            strike,
                "expiry":            expiry.strftime("%Y-%m-%d"),
                "dte":               dte,
                "bid":               round(bid, 2),
                "ask":               round(ask, 2),
                "premium":           round(mid, 2),
                "delta":             round(abs(delta), 2),
                "annualized_return": round(annualized, 1),
                "max_contracts":     max_contracts,
                "avg_cost":          avg_cost,
                "protection_pct":    protection_pct,
                "score":             score,
            })
        except:
            continue

    if not candidates:
        return None, {}

    best = max(candidates, key=lambda x: x["score"])
    return best, {"signal": f"✅ Spike CC | {spike_info['today_change']:+.1f}% move | IVP {ivdata['ivp']:.0f}%"}


def timing_score(strategy, pir, ivp, is_spec=False, ivp_override=None) -> dict:
    """
    Score timing quality for a trade setup.
    Returns dict with: score (0-100), recommend (bool), signal (str)
    strategy: CSP, CC, LEAPS, PMCC
    pir: position in 52w range (0=at low, 1=at high)
    ivp: IV percentile (0-100)
    """
    # IVP is now a scoring input only — no hard gate (patch guide section 4)
    # Low IVP trades still shown on dashboard, just score lower
    effective_ivp_min = 0   # removed hard filter
    high_ivp  = ivp >= 40
    near_low  = pir < 0.30
    near_high = pir > 0.70
    spec = " (use wider strikes — speculative)" if is_spec else ""

    if strategy == "CSP":
        if high_ivp and near_low:
            return {"score":95,"recommend":True,
                    "signal":f"🔥 EXCELLENT — IVP {ivp:.0f}% + near 52w low{spec}"}
        elif high_ivp and not near_high:
            return {"score":80,"recommend":True,
                    "signal":f"✅ GOOD — IVP {ivp:.0f}%, healthy price level{spec}"}
        elif high_ivp and near_high:
            return {"score":55,"recommend":True,
                    "signal":f"⚠️ CAUTION — IVP {ivp:.0f}% but near 52w high{spec}"}
        else:
            return {"score":20,"recommend":False,
                    "signal":f"❌ SKIP — IVP {ivp:.0f}% too low for premium selling"}

    elif strategy == "CC":
        if near_low:
            return {"score":5,"recommend":False,
                    "signal":"❌ AVOID — Never sell CC near 52w low (limits upside in recovery)"}
        elif near_high and high_ivp:
            return {"score":90,"recommend":True,
                    "signal":f"🔥 EXCELLENT — Near highs + IVP {ivp:.0f}%"}
        elif near_high:
            return {"score":75,"recommend":True,
                    "signal":f"✅ GOOD — Stock near highs, income opportunity"}
        elif high_ivp:
            return {"score":65,"recommend":True,
                    "signal":f"✅ OK — IVP {ivp:.0f}% boosts CC premium"}
        else:
            return {"score":30,"recommend":False,
                    "signal":f"⚠️ WEAK — IVP {ivp:.0f}% too low for CC"}

    elif strategy == "LEAPS":
        very_cheap = ivp < 30
        cheap      = ivp <= 50
        expensive  = ivp > 50

        if is_spec and near_high:
            return {"score":5,"recommend":False,
                    "signal":"❌ AVOID — Speculative + near highs, wait for bigger drawdown"}
        elif expensive and near_high:
            return {"score":5,"recommend":False,
                    "signal":f"❌ POOR — IVP {ivp:.0f}% expensive + near highs"}
        elif expensive and not near_low:
            return {"score":20,"recommend":False,
                    "signal":f"❌ SKIP — IVP {ivp:.0f}% too expensive to buy LEAPS (want <50%)"}
        elif very_cheap and near_low:
            return {"score":98,"recommend":True,
                    "signal":f"🔥 EXCEPTIONAL — IVP {ivp:.0f}% (very cheap) + near 52w low"}
        elif very_cheap and not near_high:
            return {"score":85,"recommend":True,
                    "signal":f"🔥 EXCELLENT — IVP {ivp:.0f}% very cheap LEAPS"}
        elif cheap and near_low:
            return {"score":82,"recommend":True,
                    "signal":f"✅ GOOD — IVP {ivp:.0f}% + near 52w low = solid LEAPS entry"}
        elif cheap and not near_high:
            return {"score":68,"recommend":True,
                    "signal":f"✅ ACCEPTABLE — IVP {ivp:.0f}%, reasonable LEAPS entry"}
        elif expensive and near_low:
            return {"score":45,"recommend":True,
                    "signal":f"⚠️ MIXED — Near 52w low but IVP {ivp:.0f}% makes options pricey"}
        else:
            return {"score":25,"recommend":False,
                    "signal":f"⚠️ WEAK — IVP {ivp:.0f}% elevated + not near lows"}

    elif strategy == "PMCC":
        if near_low:
            return {"score":5,"recommend":False,
                    "signal":"❌ AVOID — Don't sell calls near 52w lows"}
        elif high_ivp:
            return {"score":82,"recommend":True,
                    "signal":f"✅ GOOD — IVP {ivp:.0f}% gives fat PMCC premium"}
        else:
            return {"score":40,"recommend":False,
                    "signal":f"⚠️ WEAK — Low IVP {ivp:.0f}% for PMCC short call"}

    return {"score":50,"recommend":True,"signal":"—"}


def get_max_alloc(ticker: str) -> float:
    """Return max allocation as decimal for a ticker based on tier."""
    if ticker in CORE_STOCKS:        return 0.08
    elif ticker in GROWTH_STOCKS:    return 0.05
    elif ticker in CYCLICAL_STOCKS:  return 0.04
    else:                            return 0.025


def find_best_csp(ticker, price, contracts, ivdata, pir, quality, sizing=None, market_weak=False) -> tuple:
    """
    Find best cash-secured put opportunity.
    Returns (csp_dict, timing_dict) or (None, {})
    """
    if not contracts: return None, {}

    atm_iv     = ivdata.get("atm_iv", 0.3)
    candidates = []

    for c in contracts:
        if c.get("option_type") != "P":
            continue
        try:
            opt_type = c.get("option_type", "P")
            if opt_type != "P": continue
            expiry = datetime.strptime(c["expiry"], "%Y-%m-%d")
            dte    = (expiry - datetime.now()).days
            if not (CSP_MIN_DTE <= dte <= CSP_MAX_DTE):
                continue

            strike = float(c["strike"])
            if strike <= 0 or strike >= price: continue  # must be OTM

            bid    = float(c.get("nbbo_bid", 0) or 0)
            ask    = float(c.get("nbbo_ask", 0) or 0)
            if bid <= 0: continue  # stale/no market — Schwab returned bad quote
            spread_pct = (ask - bid) / ask if ask > 0 else 1.0
            mid    = bid + (ask - bid) * 0.25 if spread_pct > 0.30 else (bid + ask) / 2
            if mid < 0.10: continue

            # Use real delta from Schwab if available
            delta  = float(c.get("delta", 0) or 0)
            if abs(delta) == 0:
                delta = estimate_delta(price, strike, dte, atm_iv, "P")
            if delta is None: continue
            delta = abs(delta)

            # Delta range: tighter in WEAK market
            d_min = CSP_DELTA_PLTR_MIN if ticker == "PLTR" else CSP_DELTA_MIN
            d_max = CSP_DELTA_PLTR_MAX if ticker == "PLTR" else CSP_DELTA_MAX
            if ivdata["ivp"] > 50: d_max = CSP_DELTA_HIGH_IVP_MAX
            if market_weak:
                d_min = 0.10   # allow further OTM
                d_max = 0.22   # hard ceiling in weak market
            if not (d_min <= delta <= d_max): continue

            # Liquidity checks
            oi     = int(c.get("open_interest", 0) or 0)
            vol    = int(c.get("volume", 0) or 0)
            spread = (ask - bid) / ask if ask > 0 else 1
            if oi  < MIN_OPEN_INTEREST:    continue
            if vol < MIN_DAILY_VOLUME:     continue
            if spread > MAX_BID_ASK_SPREAD: continue

            # Annualized return — matches spreadsheet formula
            annualized    = (mid / strike) * (365 / dte) * 100
            below_min     = annualized < CSP_MIN_ANNUALIZED
            if annualized > MAX_ANNUALIZED: continue  # filter bad data
            if annualized < 5: continue  # reject truly garbage premiums

            otm_pct       = round((price - strike) / price * 100, 1)
            # Use room_usd from live position data — never suggest more than remaining budget
            if sizing and sizing.get("room_usd", 0) > 0:
                max_contracts = max(0, int(sizing["room_usd"] / (strike * 100)))
            else:
                max_contracts = max(1, int(PORTFOLIO_SIZE * get_max_alloc(ticker) / (strike * 100)))
            collateral    = strike * 100 * max_contracts
            prem_pct      = round(mid / strike * 100, 2)

            iv   = round(float(c.get("iv", 0) or atm_iv) * 100, 1)
            timing = timing_score("CSP", pir, ivdata["ivp"])
            # Canonical score — delta and annualized are not multiplied
            _s = {"tier": quality.get("tier", "Opportunistic") if quality else "Opportunistic",
                  "delta": delta, "dte": dte, "ivp": ivdata["ivp"],
                  "annualized_return": annualized,
                  "pullback_pct": (1 - pir) * 100,
                  "market_weak": market_weak,
                  "warnings": []}
            score = score_csp(_s)
            candidates.append({
                "strike":            strike,
                "expiry":            expiry.strftime("%Y-%m-%d"),
                "dte":               dte,
                "bid":               round(bid, 2),
                "ask":               round(ask, 2),
                "premium":           round(mid, 2),
                "delta":             round(delta, 2),
                "iv":                iv,
                "ivp":               round(ivdata["ivp"], 1),
                "otm_pct":           otm_pct,
                "annualized_return": round(annualized, 1),
                "below_min":         below_min,
                "max_contracts":     max_contracts,
                "collateral":        round(collateral, 0),
                "prem_pct":          prem_pct,
                "timing":            timing,
                "score":             score,
            })
        except Exception:
            continue

    if not candidates:
        if price > 10:  # only log for real stocks
            pass  # uncomment to debug: print(f"   CSP {ticker}: no candidates — type={_rej_type} dte={_rej_dte} otm={_rej_otm} prem={_rej_prem} delta={_rej_delta} liq={_rej_liq} ann={_rej_ann} timing={_rej_timing} total_P={sum(1 for c in contracts if c.get('option_type')=='P')}")
        return None, {}

    best = max(candidates, key=lambda x: x["score"])
    return best, best["timing"]


def find_best_cc(ticker, price, qty, avg_cost, contracts, ivdata, pir, already_covered=0, sell_above=0, buy_under=0):
    """
    Find best covered call.

    Phase 1.5 — Zone-First CC framework:
      - Stock must be in UPPER HALF of its buy_under/sell_above band (price >= midpoint)
      - Strike must be in upper half too (strike >= midpoint)
      - Strike must clear cost_basis * 1.10 (don't lock in <10% gains if called away)
      - Strike must clear buy_under * 1.10 (preserve original entry intent)
      - Existing rule retained: strike + premium must reach sell_above

    Applies uniformly — no exceptions for cc_only or speculative tickers.
    Cost basis protection is the lesson learned from MSTR/OWL drops.
    """
    timing = timing_score("CC", pir, ivdata["ivp"])
    if not contracts or price <= 0 or qty < 100: return None, timing
    # Note: timing["recommend"] is advisory only — dashboard shows all

    # ── Phase 1.5: Zone-first master gate ───────────────────────
    # Only write CCs when stock is in the upper half of its target band.
    # If buy_under/sell_above not set, fall through to cost basis protection.
    _zone_block_reason = None
    _band_midpoint = 0
    if buy_under > 0 and sell_above > 0 and sell_above > buy_under:
        _band_midpoint = (buy_under + sell_above) / 2
        if price < _band_midpoint:
            _zone_block_reason = (
                f"ZONE: price ${price:.2f} < band midpoint ${_band_midpoint:.2f} "
                f"(BB ${buy_under:.0f} / SA ${sell_above:.0f}) — wait for recovery"
            )
            print(f"   DBG CC SKIP {ticker}: {_zone_block_reason}")
            return None, timing

    atm_iv        = ivdata["iv_current"]
    today         = datetime.now()
    uncovered_shares = max(0, qty - already_covered)
    max_contracts    = max(0, int(uncovered_shares / 100))
    if max_contracts == 0: return None, timing  # all shares already covered
    best          = None; best_score = 0

    for c in contracts:
        try:
            opt_type = c.get("option_type", "")
            if opt_type != "C": continue
            strike = float(c.get("strike", 0) or 0)
            if strike <= 0: continue
            expiry = datetime.strptime(c["expiry"], "%Y-%m-%d")
            dte = (expiry - today).days
            if not (CC_DTE_MIN <= dte <= CC_DTE_MAX): continue
        except Exception: continue
        otm_pct = (strike - price) / price * 100
        if not (1 <= otm_pct <= 15): continue
        # ── Phase 1.5: Tightened cost basis protection (was 1.01, now 1.10) ──
        # Don't sell calls that would lock in less than 10% gain if assigned.
        # This is the protection for tickers that dropped (MSTR, OWL etc).
        if avg_cost > 0 and strike < avg_cost * 1.10: continue
        # ── Phase 1.5: Strike must clear buy_under * 1.10 ───────
        # If we'd sell below 110% of our Buy Below target, the position is
        # being given away too cheaply. Wait.
        if buy_under > 0 and strike < buy_under * 1.10: continue
        # ── Phase 1.5: Strike must clear band midpoint ──────────
        # Upper-half zone for both price AND strike.
        if _band_midpoint > 0 and strike < _band_midpoint: continue
        bid = float(c.get("nbbo_bid",0) or 0)
        ask = float(c.get("nbbo_ask",0) or 0)
        mid = (bid + ask) / 2
        if mid < 1.0: continue
        # Hard skip: effective sale (strike + premium) must reach sell_above target
        if sell_above > 0 and (strike + mid) < sell_above:
            continue
        # Liquidity filter
        oi_val  = int(c.get("open_interest", 0) or 0)
        vol_val = int(c.get("volume", 0) or 0)
        spread  = (ask - bid) / ask if ask > 0 else 1
        if oi_val < MIN_OPEN_INTEREST: continue
        if vol_val < MIN_DAILY_VOLUME: continue
        if spread > MAX_BID_ASK_SPREAD: continue
        delta = estimate_delta(price, strike, dte, atm_iv, "C")
        if delta is None or not (CC_DELTA_MIN <= delta <= CC_DELTA_HARD_MAX): continue
        annualized = (mid / price) * (365 / dte) * 100
        if annualized > MAX_ANNUALIZED: continue  # filter bad data only
        if annualized < 3: continue  # reject truly garbage premiums
        score = (timing["score"]/100) * mid * (1 + atm_iv) * (1 - abs(dte-37)/37)
        if score > best_score:
            best_score = score
            best = {"strike":strike,"expiry":expiry.strftime("%Y-%m-%d"),"dte":dte,
                    "bid":round(bid,2),"ask":round(ask,2),"premium":round(mid,2),
                    "otm_pct":round(otm_pct,1),"iv":round(atm_iv*100,1),
                    "ivp":ivdata["ivp"],"delta":delta,
                    "annualized_return":round(annualized,1),
                    "max_contracts":max_contracts,"avg_cost":round(avg_cost,2),
                    "timing":timing}
    return best, timing



def leaps_trend_state(ticker: str, price: float) -> dict:
    """
    Classify recent price behavior for LEAPS timing.
    States: AT_HIGHS | TRENDING_UP | AT_LOWS | STILL_FALLING | REAL_BOUNCE | BASE_FORMING

    Evaluated strict top-down. First match wins.
    Handles both uptrending and downtrending markets correctly.
    """
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            headers={"User-Agent": "Mozilla/5.0"},
            params={"interval": "1d", "range": "15d"},
            timeout=8
        )
        data   = r.json()["chart"]["result"][0]
        closes = data["indicators"]["quote"][0].get("close", [])
        closes = [c for c in closes if c is not None]
        if len(closes) < 6:
            return {"state": "BASE_FORMING", "r1": 0, "r3": 0, "r5": 0, "off_low_5d": 0, "off_high_5d": 0}

        today       = closes[-1]
        p1d         = closes[-2]
        p3d         = closes[-4] if len(closes) >= 4 else closes[0]
        p5d         = closes[-6] if len(closes) >= 6 else closes[0]
        recent_low  = min(closes[-5:])
        recent_high = max(closes[-5:])

        r1       = (today - p1d) / p1d         if p1d  > 0 else 0
        r3       = (today - p3d) / p3d         if p3d  > 0 else 0
        r5       = (today - p5d) / p5d         if p5d  > 0 else 0
        off_low  = (today - recent_low)  / recent_low  if recent_low  > 0 else 0
        off_high = (recent_high - today) / recent_high if recent_high > 0 else 0

        # Strict top-down — first match wins
        if off_high <= 0.02 and r5 >= 0.05:
            state = "AT_HIGHS"          # within 2% of 5d high AND up 5%+ over 5 days
        elif r3 >= 0.04 and r5 >= 0.06:
            state = "TRENDING_UP"       # strong multi-day uptrend
        elif off_low <= 0.02 and r5 < 0:
            state = "AT_LOWS"           # within 2% of 5d low AND trend is down
        elif r3 <= -0.05 or r5 <= -0.08:
            state = "STILL_FALLING"
        elif r1 > 0.01 and r3 >= 0 and off_low >= 0.05:
            state = "REAL_BOUNCE"
        elif abs(r1) < 0.01 and abs(r3) < 0.03 and r5 > -0.05:
            state = "BASE_FORMING"
        else:
            state = "BASE_FORMING"      # neutral default (not falling, not rising)

        return {
            "state":      state,
            "r1":         round(r1  * 100, 1),
            "r3":         round(r3  * 100, 1),
            "r5":         round(r5  * 100, 1),
            "off_low_5d": round(off_low  * 100, 1),
            "off_high_5d":round(off_high * 100, 1),
        }
    except Exception as e:
        return {"state": "BASE_FORMING", "r1": 0, "r3": 0, "r5": 0, "off_low_5d": 0, "off_high_5d": 0}


def leaps_trend_action(trend: dict, ivp: float, price: float, week52_high: float) -> dict:
    """
    Map trend state to action. IV filter applied as modifier after classification.
    Spec §5-6: strict top-down, first match wins, IV overrides BUY only.
    """
    state   = trend.get("state", "STILL_FALLING")
    r1      = trend.get("r1", 0)
    r3      = trend.get("r3", 0)
    off_low = trend.get("off_low_5d", 0)

    off_high = trend.get("off_high_5d", 0)

    # AT HIGHS — stock near 5d high on strong uptrend
    if state == "AT_HIGHS":
        return {
            "action":    "WATCH",
            "label":     "WATCH — NEAR HIGHS",
            "signal":    f"Up {trend.get('r5',0):.1f}% over 5 days, {off_high:.1f}% from recent high — consider waiting for a pullback",
            "recommend": False,
        }

    # TRENDING UP — solid multi-day uptrend
    if state == "TRENDING_UP":
        return {
            "action":    "WATCH",
            "label":     "WATCH — UPTREND",
            "signal":    f"Up {trend.get('r3',0):.1f}% over 3 days — strong momentum, better entry on a pause",
            "recommend": False,
        }

    # AT LOWS — near 5d low on a downtrend
    if state == "AT_LOWS":
        return {
            "action":    "WAIT",
            "label":     "WAIT — AT LOWS",
            "signal":    f"Near 5-day low, down {abs(trend.get('r5',0)):.1f}% over 5 days — wait for stabilization",
            "recommend": False,
        }

    # STILL FALLING
    if state == "STILL_FALLING":
        return {
            "action":    "WAIT",
            "label":     "WAIT — STILL FALLING",
            "signal":    f"Down {abs(r3):.1f}% over 3 days — wait for stabilization",
            "recommend": False,
        }

    # §5.3 REAL BOUNCE — check IV modifier (§6)
    if state == "REAL_BOUNCE":
        if ivp > 60:
            return {
                "action":    "WAIT",
                "label":     "WAIT — IV HIGH",
                "signal":    f"Bounce confirmed but IVP {ivp:.0f}% — wait for IV to drop",
                "recommend": False,
            }
        return {
            "action":    "BUY",
            "label":     "BUY — STABILIZED",
            "signal":    f"+{r1:.1f}% today, {off_low:.1f}% off lows, IVP {ivp:.0f}% — entry conditions met",
            "recommend": True,
        }

    # §5.4 BASE FORMING
    if state == "BASE_FORMING":
        return {
            "action":    "WATCH",
            "label":     "WATCH — BASE FORMING",
            "signal":    f"{off_low:.1f}% off recent low — selling pressure easing, no confirmed bounce yet",
            "recommend": False,
        }

    # §5.5 Default — conservative
    return {
        "action":    "WAIT",
        "label":     "WAIT — STILL FALLING",
        "signal":    "No clear trend — staying cautious",
        "recommend": False,
    }

def _cvx_hard_filters(req_cagr, conv_score, prem_pct, cov30, dte, spread_pct, oi,
                      strike_pct, ann_burden):
    """
    Apply STRICT-MODE hard filters. Returns a list of failure reasons (empty = passes).
    All decimal inputs are fractions except conv_score/cov30 (ratios), dte/oi (ints).
    """
    fails = []
    if dte < CVX_DTE_MIN:
        fails.append(f"DTE {dte} < {CVX_DTE_MIN}")
    if req_cagr > CVX_CAGR_MAX:
        fails.append(f"Req CAGR {req_cagr*100:.0f}% > {CVX_CAGR_MAX*100:.0f}%")
    if conv_score < CVX_SCORE_MIN:
        fails.append(f"Convexity {conv_score:.0f}x < {CVX_SCORE_MIN}")
    if prem_pct > CVX_PREM_MAX:
        fails.append(f"Premium {prem_pct*100:.0f}% > {CVX_PREM_MAX*100:.0f}%")
    if cov30 < CVX_COV30_MIN:
        fails.append(f"Cov@30% {cov30:.2f} < {CVX_COV30_MIN:.2f}")
    if strike_pct < CVX_STRIKE_PCT_MIN:
        fails.append(f"Strike {strike_pct*100:.0f}% < {CVX_STRIKE_PCT_MIN*100:.0f}%")
    if ann_burden > CVX_BURDEN_MAX:
        fails.append(f"Burden {ann_burden*100:.1f}%/yr > {CVX_BURDEN_MAX*100:.0f}%/yr")
    if spread_pct > CVX_SPREAD_MAX:
        fails.append(f"Spread {spread_pct*100:.0f}% > {CVX_SPREAD_MAX*100:.0f}%")
    if oi < CVX_OI_MIN:
        fails.append(f"OI {oi} < {CVX_OI_MIN}")
    return fails


def _cvx_grade(req_cagr, conv_score, prem_pct, cov30, dte):
    """
    Classify a PASSING trade into A (Excellent) or B (Good). Caller guarantees
    hard filters already passed, so only the A-tier preferred thresholds are checked.
    Returns (code, label).
    """
    if (req_cagr < CVX_CAGR_PREF and conv_score > CVX_SCORE_PREF
            and prem_pct < CVX_PREM_EXC and cov30 > CVX_COV30_PREF
            and dte > CVX_DTE_PREF_MIN):
        return ("A", "💎 Excellent Cheap Convexity")
    return ("B", "✅ Good Cheap Convexity")


def _cvx_rank_key(x):
    """
    Spec ranking order for passers:
    1) Required CAGR asc, 2) Cov@30% desc, 3) Convexity desc,
    4) Premium% asc, 5) DTE desc, 6) Spread asc.
    x holds percent/ratio values as stored on the candidate dict.
    """
    return (x["required_cagr"], -x["cov30"], -x["convexity_score"],
            x["premium_pct"], -x["dte"], x["spread_pct"])


def _cvx_note_pass(grade, req_cagr, conv_score, prem_pct, cov30):
    """One-line explanation for a passing trade (spec format)."""
    rc = req_cagr * 100; pp = prem_pct * 100
    clears = (cov30 - 1.0) * 100
    tier = "Excellent" if grade == "A" else "Good"
    return (f"Passes strict Cheap Convexity filter ({tier}): {rc:.0f}% Required CAGR, "
            f"{conv_score:.0f}x Convexity Score, {pp:.0f}% Premium, and 30% CAGR "
            f"scenario clears breakeven by {clears:.0f}%.")


def scan_convexity(ticker, price, contracts, ivp):
    """
    Cheap Convexity LEAPS scanner — STRICT MODE (default).

    Main output: ONLY far-OTM long-dated calls passing ALL hard filters
    (DTE>=700, Req CAGR<=25%, Convexity>=20, Premium<=12%, Cov@30%>=1.05,
    Strike%>=125%, Burden<5%/yr, Spread<15%, OI>=50). Graded A or B.

    Near-misses (fail >=1 hard filter but otherwise plausible, DTE>=365) are
    emitted separately with is_nearmiss=True for the hidden Diagnostics panel.

    Uses ASK for breakeven/CAGR (conservative); reports MID for limit reference.
    Zero passing results is an expected, acceptable outcome.
    """
    if not contracts or price <= 0:
        return []
    today = datetime.now()
    use_aggr = ticker in CVX_AGGR_TICKERS
    passers = []
    for c in contracts:
        try:
            if c.get("option_type") != "C":
                continue
            strike = float(c.get("strike", 0) or 0)
            if strike <= price:           # OTM only
                continue
            dte = (datetime.strptime(c["expiry"], "%Y-%m-%d") - today).days
            if dte < CVX_DTE_MIN or dte > CVX_DTE_MAX:
                continue                  # strict DTE window (>=700)
            bid = float(c.get("nbbo_bid", 0) or 0)
            ask = float(c.get("nbbo_ask", 0) or 0)
            if ask <= 0:
                continue
            oi  = int(c.get("open_interest", 0) or 0)
            vol = int(c.get("volume", 0) or 0)
            if oi == 0 and vol == 0:      # dead contract
                continue
            mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else ask
            spread_pct = ((ask - bid) / mid) if mid > 0 else 1.0
            years = dte / 365.0
            breakeven = strike + ask
            req_gain  = breakeven / price - 1.0
            req_cagr  = (breakeven / price) ** (1.0 / years) - 1.0 if years > 0 else 9.0
            prem_pct  = ask / price
            ann_burden = prem_pct / years if years > 0 else 9.0
            strike_pct = strike / price
            conv_score = strike / ask if ask > 0 else 0.0
            intrinsic  = max(0.0, price - strike)
            extrinsic  = ask - intrinsic
            extrinsic_pct = extrinsic / price
            scen = CVX_SCENARIOS + (CVX_SCENARIOS_AGGR if use_aggr else [])
            cov = {}
            for g in scen:
                fv = price * (1 + g) ** years
                cov[int(g * 100)] = round(fv / breakeven, 2) if breakeven > 0 else 0
            cov30 = cov.get(30, 0)
            cov25 = cov.get(25, 0)
            cov20 = cov.get(20, 0)

            fails = _cvx_hard_filters(req_cagr, conv_score, prem_pct, cov30, dte,
                                      spread_pct, oi, strike_pct, ann_burden)
            if fails:
                continue   # near-misses discarded — main view shows passers only
            grade, label = _cvx_grade(req_cagr, conv_score, prem_pct, cov30, dte)
            note = _cvx_note_pass(grade, req_cagr, conv_score, prem_pct, cov30)

            row = {
                "ticker": ticker, "mode": "CONVEXITY", "price": round(price, 2),
                "ivp": round(ivp, 1),
                "strike": round(strike, 2), "expiry": c["expiry"], "dte": dte,
                "years": round(years, 2),
                "bid": round(bid, 2), "ask": round(ask, 2), "mid": round(mid, 2),
                "premium": round(ask, 2),
                "open_interest": oi, "volume": vol,
                "spread_pct": round(spread_pct * 100, 1),
                "breakeven": round(breakeven, 2),
                "required_gain_pct": round(req_gain * 100, 1),
                "required_cagr": round(req_cagr * 100, 1),
                "premium_pct": round(prem_pct * 100, 1),
                "ann_burden_pct": round(ann_burden * 100, 1),
                "strike_pct": round(strike_pct * 100, 1),
                "convexity_score": round(conv_score, 1),
                "intrinsic": round(intrinsic, 2),
                "extrinsic": round(extrinsic, 2),
                "extrinsic_pct": round(extrinsic_pct * 100, 1),
                "coverage": cov,
                "cov30": cov30, "cov25": cov25, "cov20": cov20,
                "classification": grade, "class_label": label,
                "iv_rank": None,            # not available — display "N/A"
                "convexity_note": note,
                "is_nearmiss": False,
                "signal": f"{label} | CAGR {req_cagr*100:.0f}% | conv {conv_score:.0f}x",
                "passes_quality": True,
            }
            passers.append(row)
        except Exception:
            continue

    # Rank passers per spec; return only the SINGLE best passing trade per ticker.
    # Near-misses are intentionally discarded — main view only, blank if none pass.
    if not passers:
        return []
    passers.sort(key=_cvx_rank_key)
    best = passers[0]
    best["is_recommended"] = True
    return [best]


def find_best_leaps(ticker, price, contracts, ivdata, pir):
    """Deep ITM LEAPS — delta ≥0.75 (no upper cap), BE% and extrinsic% are primary gates."""
    is_spec = ticker in SPECULATIVE
    timing  = timing_score("LEAPS", pir, ivdata["ivp"], is_spec)
    # Per spec: LEAPS timing should not hard-reject Core/Growth
    # Use contract quality first, timing as scoring penalty
    is_core_growth = ticker in CORE_STOCKS or ticker in GROWTH_STOCKS
    if not timing["recommend"] and not is_core_growth:
        return None, timing
    # For Core/Growth: continue but score will reflect poor timing
    if not contracts or price <= 0: return None, timing

    atm_iv = ivdata["iv_current"]
    today  = datetime.now()
    candidates = []

    for c in contracts:
        try:
            opt_type = c.get("option_type", "")
            if opt_type != "C": continue
            strike = float(c.get("strike", 0) or 0)
            if strike <= 0: continue
            expiry = datetime.strptime(c["expiry"], "%Y-%m-%d")
            dte = (expiry - today).days
            if dte < LEAPS_DTE_MIN: continue
        except Exception: continue
        itm_pct = (price - strike) / price * 100  # positive = ITM
        if not (-5 <= itm_pct <= 70): continue
        bid = float(c.get("nbbo_bid",0) or 0)
        ask = float(c.get("nbbo_ask",0) or 0)
        mid = (bid + ask) / 2
        if mid < 5.0: continue
        delta = estimate_delta(price, strike, dte, atm_iv, "C")
        if delta is None or delta < LEAPS_DELTA_MIN: continue  # floor only — BE%/extrinsic% are the real gates
        intrinsic     = max(0, price - strike)
        extrinsic     = max(0, mid - intrinsic)
        extrinsic_pct = (extrinsic / mid * 100) if mid > 0 else 100
        if extrinsic_pct > LEAPS_EXTRINSIC_MAX: continue  # reject per config (default 20%)

        # Extrinsic quality label — PRIMARY signal for LEAPS cost
        # Extrinsic % is what you actually pay in time decay, IVP is secondary
        if extrinsic_pct < 10:
            ext_label = f"🔥 Excellent ({extrinsic_pct:.1f}%) — minimal time decay"
        elif extrinsic_pct < 15:
            ext_label = f"✅ Good ({extrinsic_pct:.1f}%) — reasonable cost"
        elif extrinsic_pct < 20:
            ext_label = f"⚠️ Acceptable ({extrinsic_pct:.1f}%) — moderate time value"
        elif extrinsic_pct < 25:
            ext_label = f"🔶 Expensive ({extrinsic_pct:.1f}%) — significant time decay"
        else:
            ext_label = f"❌ Too expensive ({extrinsic_pct:.1f}%) — avoid"

        # Score heavily penalizes high extrinsic — prefers <20% target
        # <10%: full score, 10-20%: slight penalty, 20-30%: significant penalty
        if extrinsic_pct < 10:
            ext_score = 30
        elif extrinsic_pct < 20:
            ext_score = 20
        elif extrinsic_pct < 30:
            ext_score = 8
        else:
            ext_score = 0

        delta_score = delta * 40
        score       = (timing["score"]/100) * (delta_score + ext_score) * (dte/365)
        candidates.append({
            "strike":strike,"expiry":expiry.strftime("%Y-%m-%d"),"dte":dte,
            "bid":round(bid,2),"ask":round(ask,2),"premium":round(mid,2),
            "itm_pct":round(itm_pct,1),"delta":delta,
            "intrinsic":round(intrinsic,2),"extrinsic":round(extrinsic,2),
            "extrinsic_pct":round(extrinsic_pct,1),
            "ext_label":ext_label,
            "iv":round(atm_iv*100,1),"ivp":ivdata["ivp"],
            "leverage":round(price/mid,1) if mid > 0 else 0,
            "timing":timing,"score":score
        })

    if not candidates: return None, timing
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[0], timing


def find_pmcc_short_call(ticker, price, existing_leaps, contracts, ivdata, pir):
    """Find best short call to sell against an existing LEAPS position."""
    timing = timing_score("PMCC", pir, ivdata["ivp"])
    if not timing["recommend"]: return None, timing
    if not existing_leaps or not contracts: return None, timing

    atm_iv      = ivdata["iv_current"]
    today       = datetime.now()
    leaps_strike    = existing_leaps["strike"]
    leaps_cost_per  = existing_leaps["avg_cost"]  # cost per contract (not x100)
    leaps_qty       = existing_leaps["quantity"]

    # PMCC breakeven = LEAPS strike + LEAPS cost per share (net of credits collected)
    # We conservatively use just LEAPS cost since we don't track credits collected yet
    # Short call strike MUST be above this breakeven to protect profit potential
    pmcc_breakeven  = leaps_strike + leaps_cost_per  # e.g. $120 strike + $15 cost = $135 breakeven
    best            = None; best_score = 0

    for c in contracts:
        try:
            opt_type = c.get("option_type", "")
            if opt_type != "C": continue
            strike = float(c.get("strike", 0) or 0)
            if strike <= 0: continue
            expiry = datetime.strptime(c["expiry"], "%Y-%m-%d")
            dte = (expiry - today).days
            if not (CC_DTE_MIN <= dte <= CC_DTE_MAX): continue
        except Exception: continue

        # ── PMCC safety rules ─────────────────────────────────
        # Rule 1: Short call must be BELOW LEAPS strike (defines max spread width)
        if strike >= leaps_strike: continue
        # Rule 2: Short call must be ABOVE PMCC breakeven (protect profit potential)
        # e.g. if LEAPS strike=$120, cost=$15 → breakeven=$135
        # Never sell short call below $135 or you cap profit below your cost
        if strike < pmcc_breakeven * 0.99:  # 1% tolerance
            continue

        otm_pct = (strike - price) / price * 100
        if not (1 <= otm_pct <= 15): continue
        bid = float(c.get("nbbo_bid",0) or 0)
        ask = float(c.get("nbbo_ask",0) or 0)
        mid = (bid + ask) / 2
        if mid < 0.50: continue
        delta = estimate_delta(price, strike, dte, atm_iv, "C")
        if delta is None or not (CC_DELTA_MIN <= delta <= CC_DELTA_HARD_MAX): continue

        # How many months of premiums to recover LEAPS cost
        leaps_cost_total = leaps_cost_per * 100 * leaps_qty
        months_to_recover = leaps_cost_total / (mid * 100 * leaps_qty / (dte/30)) if mid > 0 and dte > 0 else 999

        score = (timing["score"]/100) * mid * (1 + atm_iv)
        if score > best_score:
            best_score = score
            annualized = (mid / price) * (365 / dte) * 100
            best = {
                "strike":             strike,
                "expiry":             expiry.strftime("%Y-%m-%d"),
                "dte":                dte,
                "bid":                round(bid,2),
                "ask":                round(ask,2),
                "premium":            round(mid,2),
                "otm_pct":            round(otm_pct,1),
                "delta":              delta,
                "iv":                 round(atm_iv*100,1),
                "ivp":                ivdata["ivp"],
                "annualized_return":  round(annualized,1),
                "leaps_strike":       leaps_strike,
                "leaps_cost":         round(leaps_cost_per,2),
                "pmcc_breakeven":     round(pmcc_breakeven,2),
                "months_to_recover":  round(months_to_recover,1) if months_to_recover < 100 else "N/A",
                "max_contracts":      leaps_qty,
                "timing":             timing,
            }
    return best, timing


# OPPORTUNISTIC VOLATILITY SCANNER
# ════════════════════════════════════════════════════════════

def check_volatility_spike(ticker: str, md: dict) -> dict:
    """
    Detect if a stock has spiked ≥8% recently (1-3 days).
    Uses price vs recent closes from Yahoo Finance.
    Returns spike details if triggered, else None.
    """
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            headers={"User-Agent": "Mozilla/5.0"},
            params={"interval": "1d", "range": "5d"},
            timeout=8
        )
        data    = r.json()["chart"]["result"][0]
        closes  = data["indicators"]["quote"][0].get("close", [])
        closes  = [c for c in closes if c is not None]
        if len(closes) < 2:
            return None
        current = closes[-1]
        # Check 1-day and 3-day spike
        spike_1d = (current - closes[-2]) / closes[-2] if len(closes) >= 2 else 0
        spike_3d = (current - closes[-4]) / closes[-4] if len(closes) >= 4 else 0
        best_spike = max(spike_1d, spike_3d)
        if best_spike >= OPP_SPIKE_MIN:
            return {
                "spike_pct":  round(best_spike * 100, 1),
                "spike_days": 1 if spike_1d >= OPP_SPIKE_MIN else 3,
                "current":    round(current, 2),
                "triggered":  True,
            }
        return None
    except:
        return None


def find_opp_cc(ticker: str, price: float, qty: float, avg_cost: float,
                contracts: list, ivdata: dict) -> dict:
    """
    Find best covered call for opportunistic volatility mode.
    Rules: 14-30 DTE, delta 0.20-0.35, IVP > 40, strike above spike high.
    Only valid if holding ≥100 shares.
    """
    if qty < 100:
        return None
    best = None
    best_score = 0
    for c in contracts:
        if c.get("option_type") != "C":
            continue
        strike = float(c.get("strike", 0))
        if strike <= price:
            continue  # must be OTM
        expiry = c.get("expiry", "")
        try:
            exp_dt = datetime.strptime(expiry, "%Y-%m-%d")
        except:
            continue
        dte = (exp_dt - datetime.now()).days
        if not (OPP_CC_DTE_MIN <= dte <= OPP_CC_DTE_MAX):
            continue
        bid = float(c.get("nbbo_bid", 0) or 0)
        ask = float(c.get("nbbo_ask", 0) or 0)
        mid = (bid + ask) / 2
        if mid < 0.50:
            continue
        # Liquidity
        oi  = int(c.get("open_interest", 0) or 0)
        vol = int(c.get("volume", 0) or 0)
        spd = (ask - bid) / ask if ask > 0 else 1
        if oi < MIN_OPEN_INTEREST or vol < MIN_DAILY_VOLUME or spd > MAX_BID_ASK_SPREAD:
            continue
        # Use real delta from Schwab if available, else estimate
        delta = abs(float(c.get("delta", 0) or 0))
        if delta == 0:
            atm_iv = ivdata.get("atm_iv", 0.30)
            delta = abs(estimate_delta(price, strike, dte, atm_iv, "C") or 0)
        if not (OPP_CC_DELTA_MIN <= delta <= OPP_CC_DELTA_MAX):
            continue
        annualized = (mid / price) * (365 / dte) * 100
        max_contracts = int(qty // 100)
        # Canonical CC score — no premium×delta bias
        _s = {"tier": "Opportunistic", "delta": delta, "ivp": ivdata.get("ivp", 50),
              "annualized_return": annualized, "strike": strike, "breakeven": 0}
        score = score_cc(_s) + (oi / 50000)  # small liquidity bonus
        if score > best_score:
            best_score = score
            best = {
                "strike":           round(strike, 2),
                "expiry":           expiry,
                "dte":              dte,
                "bid":              round(bid, 2),
                "ask":              round(ask, 2),
                "premium":          round(mid, 2),
                "delta":            round(delta, 2),
                "annualized_return": round(annualized, 1),
                "max_contracts":    max_contracts,
                "avg_cost":         round(avg_cost, 2),
                "protection_pct":   round(mid / price * 100, 2),
            }
    return best


def fmt_opp_cc(opp: dict) -> str:
    """Format opportunistic CC alert for Telegram."""
    cc  = opp["cc"]
    spk = opp["spike"]
    s   = opp["sizing"]
    lines = [
        f"⚡ *VOLATILITY SPIKE — {opp['ticker']} @ ${opp['price']}*",
        f"_+{spk['spike_pct']}% spike in {spk['spike_days']}d — IV elevated — sell calls now_",
        "",
        f"  [{opp['tier']}] | Breakeven: ${cc.get('avg_cost','—')}",
        f"  Sell Call ${cc['strike']} | {cc['expiry']} | {cc['dte']} DTE",
        f"  Bid ${cc['bid']} / Ask ${cc['ask']} | Premium ${cc['premium']}",
        f"  δ{cc['delta']} | Annualized: {cc['annualized_return']}% | {cc['max_contracts']} contracts",
        f"  Protection: {cc['protection_pct']}% downside buffer",
        f"  IVP: {opp['ivp']:.0f}% | Exit at 50-70% profit",
        f"_Scanned {now_et().strftime('%b %d %H:%M')} PT_"
    ]
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════
# PETER LYNCH DISCOVERY
# ════════════════════════════════════════════════════════════

def peter_lynch_screen(known_tickers, flow_data) -> list:
    flow_tickers = {}
    for alert in flow_data:
        t       = alert.get("ticker","")
        premium = float(alert.get("total_premium",0) or 0)
        if (t and t not in known_tickers and premium > 500_000
                and alert.get("type") == "call"):
            flow_tickers[t] = flow_tickers.get(t,0) + premium

    discoveries = []
    for ticker in sorted(flow_tickers, key=flow_tickers.get, reverse=True)[:8]:
        try:
            fund = get_fundamentals(ticker)
            peg  = fund.get("peg_ratio")
            eps  = fund.get("eps_growth")
            mcap = fund.get("market_cap",0) or 0
            if peg and eps and 0 < peg < 1.5 and eps > 0.15 and mcap > 5_000_000_000:
                discoveries.append({
                    "ticker": ticker,
                    "peg_ratio": round(peg,2),
                    "eps_growth": round(eps*100,1),
                    "whale_flow": f"${flow_tickers[ticker]:,.0f}",
                    "why": f"PEG {peg:.1f}, EPS +{eps*100:.0f}%, whale call flow ${flow_tickers[ticker]/1e6:.1f}M"
                })
        except: continue
    return sorted(discoveries, key=lambda x: x.get("peg_ratio",99))[:3]


# ════════════════════════════════════════════════════════════
# CLAUDE ANALYSIS
# ════════════════════════════════════════════════════════════

def claude_analyze(csps, ccs, leaps_list, pmccs, discoveries, spikes=None, drops=None, pio=None) -> str:
    if not ANTHROPIC_API_KEY: return ""
    all_opps = csps + ccs + leaps_list + pmccs
    if not all_opps: return ""

    prompt = f"""Expert options income trader, $7M portfolio. Framework:
- Quality stock first, premium is secondary
- CSP: delta 0.20-0.30, 30-45 DTE, ≥15% annualized, IVP≥30
- CC: delta 0.15-0.25, ≥10% annualized, only when not near 52w low
- LEAPS: delta ≥0.75 (no upper cap), deep ITM, <25% extrinsic, 2+ years
- PMCC: sell 30-45 DTE calls against existing LEAPS
- Earnings blackout: no CSP/CC within 14 days of earnings

Deal quality checklist:
1. Would I be happy owning at strike price?
2. Is return worth the risk?
3. Is volatility helping?
4. Is chain liquid?
5. Is strike near real support?

CSPs (Income Mode): {json.dumps(csps,indent=2)}
CCs (Income Mode): {json.dumps(ccs,indent=2)}
LEAPS: {json.dumps(leaps_list,indent=2)}
PMCCs: {json.dumps(pmccs,indent=2)}
Post-Drop CSPs (Sell Fear Mode): {json.dumps(drops,indent=2) if drops else 'None'}
Post-Spike CCs (Sell Strength Mode): {json.dumps(spikes,indent=2) if spikes else 'None'}
Position Income CCs (Existing Holdings): {json.dumps(pio,indent=2) if pio else 'None'}

CRITICAL FORMAT RULE: Always use EXACT expiry dates in YYYY-MM-DD format.
Never write "Apr-26" or "April expiry" — always write the full date like "2026-04-17".
There are multiple weekly expirations in any month — the exact date is essential.

Give:
1. Best CSP — ticker, exact strike, EXACT expiry date (YYYY-MM-DD), DTE, bid/ask, delta, annualized return
2. Best CC — same format (if any)
3. Best LEAPS or PMCC — same format (if any)
4. One-line IVP environment summary

Only list items that actually have qualifying trades. Skip any category with nothing. Be concise. Every trade must include the full YYYY-MM-DD expiry date."""

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key":ANTHROPIC_API_KEY,
                     "anthropic-version":"2023-06-01",
                     "content-type":"application/json"},
            json={"model":"claude-sonnet-4-20250514","max_tokens":900,
                  "messages":[{"role":"user","content":prompt}]},
            timeout=30
        )
        if r.status_code == 200:
            return r.json()["content"][0]["text"]
        print(f"Claude {r.status_code}: {r.text[:100]}")
    except Exception as e:
        print(f"Claude error: {e}")
    return ""


# ════════════════════════════════════════════════════════════
# TELEGRAM
# ════════════════════════════════════════════════════════════

def send_telegram(msg: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[TG]\n{msg}\n"); return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id":TELEGRAM_CHAT_ID,"text":msg,"parse_mode":"Markdown"},
            timeout=10
        )
        print("✅" if r.status_code==200 else f"TG err: {r.text[:80]}")
    except Exception as e:
        print(f"TG: {e}")


def fmt_quality(q) -> str:
    """Format quality summary for Telegram alerts."""
    warnings = q.get("warnings", [])
    score    = q.get("quality_score", 0)
    pullback = q.get("pullback_pct", 0)
    earn     = q.get("days_to_earnings")
    earn_status = q.get("earnings_status","ok")

    lines = []
    # Earnings
    if earn_status == "hard_stop":
        lines.append(f"🚨 Earnings in {earn}d — HARD STOP")
    elif earn_status == "warning":
        lines.append(f"⚠️ Earnings in {earn}d — caution")
    elif earn and earn > 0:
        lines.append(f"✅ Earnings in {earn}d — safe")

    # MA50 extension
    if q.get("ma50_extended"):
        lines.append(f"📈 Extended {q.get('pct_above_ma50',0):.1f}% above 50MA")

    # MA200
    if not q.get("checks",{}).get("ma200_ok", True):
        lines.append("⚠️ Below 200MA")

    # Price location
    if q.get("near_high"):
        lines.append(f"📍 Near 52w high — CSP skipped")
    else:
        lines.append(f"✅ {pullback:.1f}% off highs | Quality {score}/6")

    return " | ".join(lines) if lines else f"✅ Quality {score}/6 | {pullback:.1f}% off highs"


def fmt_csp(opp) -> str:
    t = opp["csp"]["timing"]; s = opp["sizing"]; q = opp["quality"]
    d = f" | δ{opp['csp']['delta']}" if opp['csp'].get('delta') else ""
    return "\n".join([
        f"💰 *CSP — {opp['ticker']} @ ${opp['price']}*",
        f"_{t['signal']}_",
        f"  {fmt_quality(q)}",
        *([f"  {opp['darkpool']['label']}"]
           if opp.get('darkpool',{}).get('show') else []),
        f"  [{opp['tier']}] {s['tier']} tier | Max: {s['max_pct']}% (${PORTFOLIO_SIZE*s['max_pct']/100:,.0f})",
        f"  Sell Put ${opp['csp']['strike']} | {opp['csp']['expiry']} | {opp['csp']['dte']} DTE",
        f"  Bid ${opp['csp']['bid']} / Ask ${opp['csp']['ask']}",
        f"  {opp['csp']['otm_pct']}% OTM | IV {opp['csp']['iv']}% | IVP {opp['csp']['ivp']:.0f}%{d}",
        f"  Annualized: {opp['csp']['annualized_return']}% | ${opp['csp']['premium']/opp['csp']['dte']:.2f}/day | {opp['csp']['max_contracts']} contracts",
        *([f"  ⚠️ Below 20% minimum — consider skipping or check wider strike"]
           if opp['csp'].get('below_min') else []),
        f"  Collateral: ${opp['csp']['collateral']:,.0f} | Room: ${s['room_usd']:,.0f}",
        *([f"  ⚠️ OI Signal: {opp['oi_signal']['signal']} (calls {opp['oi_signal']['call_oi_change']:+,} / puts {opp['oi_signal']['put_oi_change']:+,})"]
           if opp.get("oi_signal") else []),
        *([f"  📍 Max Pain: ${opp['expiry_breakdown']['max_pain_strike']} | P/C ratio: {opp['expiry_breakdown']['put_call_ratio']}"]
           if opp.get("expiry_breakdown") and opp["expiry_breakdown"].get("max_pain_strike") else []),
        f"_Scanned {now_et().strftime('%b %d %H:%M')} PT_"
    ])


def fmt_cc(opp) -> str:
    cc = opp["cc"]; t = cc["timing"]; s = opp["sizing"]
    ppd = round(cc["premium"] / max(1, cc["dte"]), 2)
    ivp_label = "Low" if cc["ivp"] < 30 else "Good" if cc["ivp"] < 50 else "Elevated"
    return "\n".join([
        f"📈 *CC — {opp['ticker']} @ ${opp['price']}*",
        f"_{t['signal']}_",
        f"  [{opp['tier']}] | Breakeven: ${cc.get('avg_cost','—')}",
        f"  Sell Call ${cc['strike']} | {cc['expiry']} | {cc['dte']} DTE",
        f"  Bid ${cc['bid']} / Ask ${cc['ask']} | Mid ${cc['premium']}",
        f"  δ{cc['delta']} | {cc['otm_pct']}% OTM | IVP {cc['ivp']:.0f}% ({ivp_label})",
        f"  ${ppd}/day | Annualized: {cc['annualized_return']}%",
        f"_Scanned {now_et().strftime('%b %d %H:%M')} PT_"
    ])


def fmt_leaps(opp) -> str:
    t = opp["leaps"]["timing"]; s = opp["sizing"]; l = opp["leaps"]
    d = f" | δ{l['delta']}" if l.get('delta') else ""
    itm = f"{l['itm_pct']}% ITM" if l['itm_pct'] > 0 else f"{abs(l['itm_pct'])}% OTM"
    ext_pct = l.get("extrinsic_pct", 100)
    ext_label = l.get("ext_label", f"{ext_pct:.1f}%")

    # PRIMARY signal = extrinsic quality (what you actually pay)
    # SECONDARY = IVP context (historical cheapness)
    if ext_pct < 10:
        primary = f"🔥 EXCELLENT — Extrinsic {ext_pct:.1f}% (minimal time decay)"
    elif ext_pct < 15:
        primary = f"✅ GOOD — Extrinsic {ext_pct:.1f}% (reasonable cost)"
    elif ext_pct < 20:
        primary = f"⚠️ ACCEPTABLE — Extrinsic {ext_pct:.1f}% (moderate time value)"
    else:
        primary = f"🔶 EXPENSIVE — Extrinsic {ext_pct:.1f}% (significant time decay)"

    ivp_val = l["ivp"]
    ivp_context = f"IVP {ivp_val:.0f}% ({'Low' if ivp_val < 30 else 'Good' if ivp_val < 50 else 'Elevated'})"

    return "\n".join([
        f"🚀 *LEAPS — {opp['ticker']} @ ${opp['price']}*",
        f"_{primary}_",
        f"_({ivp_context})_",
        f"  52w: ${opp['w52_low']} — ${opp['w52_high']} | {opp['pullback_pct']}% off high",
        f"  Buy Call ${l['strike']} | {l['expiry']} | {l['dte']} DTE",
        f"  Bid ${l['bid']} / Ask ${l['ask']} | Cost ${l['premium']}",
        f"  {itm}{d}",
        f"  Intrinsic: ${l['intrinsic']} | Extrinsic: ${l['extrinsic']} ({ext_pct:.1f}%)",
        f"  Leverage: {l['leverage']}x | Tier: {s['tier']} | Room: ${s['room_usd']:,.0f}",
        f"_Scanned {now_et().strftime('%b %d %H:%M')} PT_"
    ])


def fmt_convex(o) -> str:
    """Format a Cheap Convexity LEAP alert (Grade A only). Flat field schema."""
    grade = "💎 EXCELLENT (A)" if o.get("classification") == "A" else "✅ GOOD (B)"
    cov20 = o.get("cov20"); cov25 = o.get("cov25"); cov30 = o.get("cov30")
    def _c(v): return f"{v:.2f}" if isinstance(v, (int, float)) else "—"
    return "\n".join([
        f"🎲 *CHEAP CONVEXITY — {o['ticker']} @ ${o['price']}*",
        f"_{grade} — Req CAGR {o['required_cagr']}% | Convexity {o['convexity_score']}x_",
        f"  Buy Call ${o['strike']} | {o['expiry']} | {o['dte']} DTE",
        f"  Ask ${o['premium']} / Mid ${o['mid']} | Premium {o['premium_pct']}% of stock",
        f"  Breakeven ${o['breakeven']} (needs {o['required_cagr']}%/yr to win)",
        f"  Strike {o['strike_pct']}% of spot | Burden {o['ann_burden_pct']}%/yr",
        f"  Coverage — 20%: {_c(cov20)} | 25%: {_c(cov25)} | 30%: {_c(cov30)}",
        f"  OI {o['open_interest']} | Spread {o['spread_pct']}% | Max loss = premium",
        f"_Use limit near mid; do not pay ask blindly_" if o.get("spread_pct", 0) > 8 else "",
        f"_Scanned {now_et().strftime('%b %d %H:%M')} PT_"
    ]).replace("\n\n_Scanned", "\n_Scanned")


def fmt_pmcc(opp) -> str:
    t = opp["pmcc"]["timing"]; p = opp["pmcc"]; l = opp["existing_leaps"]
    d = f" | δ{p['delta']}" if p.get('delta') else ""
    breakeven = p.get("pmcc_breakeven", l["strike"] + l.get("avg_cost",0))
    return "\n".join([
        f"⚡ *PMCC — {opp['ticker']} @ ${opp['price']}*",
        f"_{t['signal']}_",
        f"  📋 LEAPS position: ${l['strike']} call | Cost: ${l.get('avg_cost',0):.2f}/share | {l['dte']}DTE | {l['quantity']} contracts",
        f"  ⚠️ PMCC breakeven: ${breakeven:.2f} (LEAPS strike + cost)",
        f"  ✅ Short call ${p['strike']} is above breakeven ${breakeven:.2f}",
        f"  Sell Call ${p['strike']} | {p['expiry']} | {p['dte']} DTE",
        f"  Bid ${p['bid']} / Ask ${p['ask']}",
        f"  {p['otm_pct']}% OTM | IVP {p['ivp']:.0f}%{d}",
        f"  Annualized: {p['annualized_return']}% | Months to recover LEAPS: {p['months_to_recover']}",
        f"_Scanned {now_et().strftime('%b %d %H:%M')} PT_"
    ])


def fmt_pio_cc(opp) -> str:
    """Format Position Income Optimization CC alert."""
    p  = opp["pio_cc"]
    ppd = round(p["premium"] / max(1, p["dte"]), 2)
    ivp_label = "Low" if opp["ivp"] < 30 else "Good" if opp["ivp"] < 50 else "Elevated"
    lines = [
        f"💼 *POSITION INCOME — {opp['ticker']} @ ${opp['price']}*",
        f"  [{opp['tier']}] | Breakeven: ${p.get('breakeven','—')}",
        f"  Sell Call ${p['strike']} | {p['expiry']} | {p['dte']} DTE",
        f"  Bid ${p['bid']} / Ask ${p['ask']} | Mid ${p['premium']}",
        f"  δ{p['delta']} | IVP {opp['ivp']:.0f}% ({ivp_label})",
        f"  ${ppd}/day | Annualized: {p['annualized_return']}%",
        f"_Scanned {now_et().strftime('%b %d %H:%M')} PT_"
    ]
    return "\n".join([l for l in lines if l is not None])


def fmt_drop_csp(opp) -> str:
    """Format post-drop CSP alert for Telegram."""
    s  = opp["sizing"]
    d  = opp["drop_csp"]
    di = opp["drop_info"]
    dp = opp.get("darkpool", {})

    trigger = (f"🔻 Dropped {di['today_change']:+.1f}% today"
               if di["trigger"] == "today"
               else f"🔻 Dropped {di['pct_below_ma50']:+.1f}% below 50MA recently")

    lines = [
        f"🔻 *POST-DROP CSP — {opp['ticker']} @ ${opp['price']}*",
        f"_{trigger} — Selling fear, not chasing a falling knife_",
        f"_⚠️ ELEVATED RISK — Reduced position size ({int(DROP_SIZE_FACTOR*100)}% of normal)_",
        "",
        *([f"  {dp['label']}"] if dp.get("show") else []),
        f"  IVP: {opp['ivp']:.0f}% | 200MA: ✅ Above",
        f"  1d change: {di['today_change']:+.1f}% | Pullback: {opp['pullback_pct']}% off highs",
        f"  Sell Put ${d['strike']} | {d['expiry']} | {d['dte']} DTE",
        f"  Bid ${d['bid']} / Ask ${d['ask']} | Mid ${d['premium']}",
        f"  δ{d['delta']} | Premium: {d['prem_pct']}% of strike",
        f"  Annualized: {d['annualized_return']}% | ${d['premium']/d['dte']:.2f}/day",
        f"  Collateral: ${d['collateral']:,.0f} | {d['max_contracts']} contracts (reduced size)",
        f"  [{opp['tier']}] {s['tier']} tier | Room: ${s['room_usd']:,.0f}",
        "",
        f"  ✅ _Favorable if: market selloff, sector rotation, profit taking_",
        f"  ❌ _Avoid if: earnings miss, guidance cut, broken trend_",
        f"_Scanned {now_et().strftime('%b %d %H:%M')} PT_"
    ]
    return "\n".join([l for l in lines if l is not None])


def fmt_spike_cc(opp) -> str:
    """Format opportunistic spike CC alert for Telegram."""
    s  = opp["sizing"]
    sc = opp["spike_cc"]
    si = opp["spike_info"]
    dp = opp.get("darkpool", {})

    trigger = (f"📈 Up {si['today_change']:+.1f}% today"
               if si["trigger"] == "today"
               else f"📈 Extended {si['pct_above_ma50']:+.1f}% above 50MA")

    lines = [
        f"⚡ *SPIKE CC — {opp['ticker']} @ ${opp['price']}*",
        f"_{trigger} — IV spiked, sell calls before vol contracts_",
        "",
        *([f"  {dp['label']}"] if dp.get("show") else []),
        f"  IVP: {opp['ivp']:.0f}% | {opp['pullback_pct']}% off highs",
        f"  Sell Call ${sc['strike']} | {sc['expiry']} | {sc['dte']} DTE",
        f"  Bid ${sc['bid']} / Ask ${sc['ask']} | Mid ${sc['premium']}",
        f"  δ{sc['delta']} | Annualized: {sc['annualized_return']}% | ${sc['premium']/sc['dte']:.2f}/day",
        f"  Protection: {sc['protection_pct']}% downside buffer",
        f"  Breakeven: ${sc.get('avg_cost','—')} | Max {sc['max_contracts']} contracts",
        "",
        f"  ⚠️ _Exit when 50-70% of premium captured_",
        f"  ⚠️ _Close early if stock reverses sharply_",
        f"_Scanned {now_et().strftime('%b %d %H:%M')} PT_"
    ]
    return "\n".join([l for l in lines if l is not None])


# MAIN SCANNER
# ════════════════════════════════════════════════════════════

def tg_position_alert_worthy(p: dict) -> bool:
    """
    P17 Telegram gate for BIG MOVE / P&L SWING position alerts.
    A bare favorable move on a comfortable position is noise — the Move
    Watcher already told John the name moved. Telegram gets the position
    alert only when there's something to DECIDE:
      - P&L SWING (big recovery since last scan) — always news
      - earnings within 7d AND inside this option's expiry
      - price within TG_POS_NEAR_STRIKE % of the strike
      - credible profit >= TG_POS_MIN_PROFIT % (close-and-be-done territory)
    Dashboard shows every action regardless of this gate.
    """
    if p.get("action") == "P&L SWING":
        return True
    earn = p.get("earn_days", 999)
    if earn <= 7 and earn <= p.get("dte", 0):
        return True
    if p.get("dist_to_strike", 99) <= TG_POS_NEAR_STRIKE:
        return True
    if (p.get("mark_src") in ("chain", "chain_near")
            and p.get("profit_pct", 0) >= TG_POS_MIN_PROFIT):
        return True
    return False


def skip_redundant_scheduled_run(max_age_min: int = 100) -> bool:
    """
    GitHub's cron can deliver a scheduled run 60-105 min late (observed
    Jul 2026). The Move Watcher's watchdog dispatches a replacement scan
    ~10-25 min after a missed slot, so when the original late run finally
    arrives it would duplicate everything (double Telegram batch).
    Rule: a SCHEDULE-event run exits quietly if a scan completed within
    max_age_min. Manual runs and watchdog dispatches (workflow_dispatch)
    always execute. 100 min is safely below the tightest slot spacing
    (16:41→18:47 UTC = 126 min), so legitimate slots are never skipped —
    and if one ever were, the watchdog would rescue it anyway.
    NOTE: scan_time strings say "ET" but now_et() actually returns Pacific —
    parse with PT (see now_et()).
    """
    if os.environ.get("GITHUB_EVENT_NAME") != "schedule":
        return False
    try:
        with open("results.json") as f:
            st = json.load(f).get("scan_time", "")
        last = datetime.strptime(st, "%Y-%m-%d %H:%M ET").replace(tzinfo=PT)
        age_min = (datetime.now(tz.utc).astimezone(PT) - last).total_seconds() / 60
        return 0 <= age_min < max_age_min
    except Exception:
        return False


def run_scanner():
    if skip_redundant_scheduled_run():
        print("⏭  Late-arriving scheduled run — a scan already completed within "
              "100 min (watchdog or earlier slot). Skipping duplicate.")
        return
    print(f"\n{'='*60}")
    print(f"🐋 WHALE INTELLIGENCE v5 — {now_et().strftime('%Y-%m-%d %H:%M')} ET")
    print(f"   Framework: Quality → Pullback → Option Yield")
    print(f"{'='*60}\n")

    global PORTFOLIO_SIZE

    print("📊 IBKR positions...")
    ibkr     = get_ibkr_positions()

    # ── IBKR Flex stale-data protection ──────────────────────────────────────
    # Flex caches responses server-side. Multiple rapid runs (testing) can return
    # an older cached statement with missing or zero options — without any error.
    # Fix: compare fresh option count against last-known-good cache. If fresh has
    # significantly fewer options, emit a loud warning and fall back to cached data.
    _ibkr_fresh_opts = sum(1 for v in ibkr.values() if v.get("asset_class") == "OPT")
    _ibkr_fresh_stk  = sum(1 for v in ibkr.values() if v.get("asset_class") == "STK")
    print(f"   IBKR Flex fresh: {_ibkr_fresh_stk} stocks, {_ibkr_fresh_opts} options")

    _ibkr_cache = {}
    try:
        with open("ibkr_positions_cache.json") as _cf:
            _ibkr_cache = json.load(_cf)
        _cache_opts = sum(1 for v in _ibkr_cache.values() if v.get("asset_class") == "OPT")
        _cache_stk  = sum(1 for v in _ibkr_cache.values() if v.get("asset_class") == "STK")
        print(f"   IBKR Flex cache: {_cache_stk} stocks, {_cache_opts} options")

        # Fall back if fresh is missing more than half the cached options, OR has no options at all
        # (but cached had some). This catches silent stale responses.
        _opts_ok  = _ibkr_fresh_opts >= max(1, _cache_opts * 0.5)
        _stk_ok   = _ibkr_fresh_stk  >= max(1, _cache_stk  * 0.5)
        if not _opts_ok or not _stk_ok:
            print(f"   ⚠️⚠️  IBKR Flex data looks stale/incomplete "
                  f"(fresh: {_ibkr_fresh_stk}stk/{_ibkr_fresh_opts}opt vs "
                  f"cache: {_cache_stk}stk/{_cache_opts}opt) — USING CACHE")
            ibkr = _ibkr_cache
        else:
            print(f"   ✅ IBKR Flex fresh data looks complete — using it")
    except FileNotFoundError:
        print("   ℹ️  No IBKR positions cache found (first run or cache cleared)")
    except Exception as _ce:
        print(f"   ⚠️ Could not load IBKR positions cache: {_ce}")
    # ─────────────────────────────────────────────────────────────────────────

    stk_hold = {k:v for k,v in ibkr.items() if v.get("asset_class")=="STK"}
    all_tickers = ALL_TICKERS
    print(f"💹 Market data ({len(all_tickers)} stocks)...")
    # Use Schwab for real-time quotes if available, else Yahoo Finance
    schwab_quotes = schwab_get_quotes(all_tickers) if SCHWAB_APP_KEY else {}
    mkt = get_market_data(all_tickers)
    # Merge Schwab data over Yahoo — Schwab is more accurate
    for ticker, sq in schwab_quotes.items():
        if ticker in mkt and sq.get("price", 0) > 0:
            mkt[ticker]["price"]       = sq["price"]
            mkt[ticker]["week52_high"] = sq["week52_high"] or mkt[ticker]["week52_high"]
            mkt[ticker]["week52_low"]  = sq["week52_low"]  or mkt[ticker]["week52_low"]
            mkt[ticker]["avg_volume"]  = sq["avg_volume"]  or mkt[ticker]["avg_volume"]
            if sq.get("prev_close", 0) > 0:
                mkt[ticker]["day_change_pct"] = (
                    sq["price"] - sq["prev_close"]
                ) / sq["prev_close"]  # signed — negative = drop, positive = gain
    print(f"   Schwab: {len(schwab_quotes)} real-time | Yahoo fallback: {len(mkt)-len(schwab_quotes)}")

    # Fetch Schwab accounts for position awareness
    schwab_accounts  = schwab_get_accounts() if SCHWAB_APP_KEY else []
    schwab_positions = schwab_parse_positions(schwab_accounts) if schwab_accounts else {}

    # Always initialize these — used later in exposure map building
    schwab_account_map = {}   # ticker -> "IRA" / "CRT" / "Personal"
    schwab_mv_by_acct  = {}   # ticker -> {acct: market_value}

    if schwab_positions:
        _sp_stk = sum(1 for p in schwab_positions.values() if p.get("asset_class")=="STK")
        _sp_opt = sum(1 for p in schwab_positions.values() if p.get("asset_class")=="OPT")
        print(f"   Schwab positions: {_sp_stk} stocks, {_sp_opt} options parsed")

        # Build account maps from Schwab positions BEFORE merging
        # Use mv_by_account (built during parsing) to capture ALL accounts per ticker
        for _sym, _pos in schwab_positions.items():
            if _pos.get("asset_class") != "STK": continue
            _t   = _sym.replace("BRK B","BRK-B").strip()
            _by  = _pos.get("mv_by_account", {})
            _lbl = _pos.get("account_type", "") or ""
            _mv  = float(_pos.get("market_value", 0) or 0)
            print(f"     Schwab STK: {_t} acct={_lbl!r} mv=${_mv:,.0f} by_acct={_by}")
            if _by:
                # Use the full per-account breakdown
                schwab_mv_by_acct[_t] = dict(_by)
                schwab_account_map[_t] = max(_by, key=_by.get)
            elif _lbl:
                # Fallback: single account
                schwab_mv_by_acct[_t] = {_lbl: _mv}
                schwab_account_map[_t] = _lbl
        print(f"   Schwab account map: "
              + str({v: sum(1 for x in schwab_account_map.values() if x==v)
                     for v in sorted(set(schwab_account_map.values()))}))
        print(f"   Multi-account tickers: "
              + str({t: list(v.keys()) for t,v in schwab_mv_by_acct.items() if len(v) > 1}))

        # Merge into ibkr dict
        schwab_stk_added = 0; schwab_opt_added = 0
        for ticker, pos in schwab_positions.items():
            if pos.get("asset_class") == "OPT":
                # Use option symbol as key — never overwrite stock position with option data
                # Prefix with account type so Schwab CRT/IRA options never
                # overwrite IBKR options that share the same option symbol
                _acct_pfx = pos.get("account_type", "schwab")
                opt_key   = f"{_acct_pfx}_{pos.get('option_symbol', ticker) or ticker}"
                ibkr[opt_key] = pos
                schwab_opt_added += 1
            else:
                if ticker in ibkr and ibkr[ticker].get("asset_class") == "STK":
                    ibkr[ticker]["market_value"] = (ibkr[ticker].get("market_value", 0)
                                                    + pos.get("market_value", 0))
                    ibkr[ticker]["quantity"]     = (ibkr[ticker].get("quantity", 0)
                                                    + pos.get("quantity", 0))
                else:
                    ibkr[ticker] = pos
                    schwab_stk_added += 1
        print(f"   Schwab merge: {schwab_stk_added} new stocks, {schwab_opt_added} options added")

    # ── Calculate real portfolio size from live account data ──
    schwab_total = sum(a.get("net_liquidation", 0) for a in schwab_accounts)
    ibkr_total   = sum(v.get("market_value", 0) for v in ibkr.values()
                       if v.get("asset_class") == "STK")
    _ibkr_stk_count = sum(1 for v in ibkr.values() if v.get("asset_class") == "STK")
    print(f"   Portfolio calc: Schwab NLV=${schwab_total:,.0f} | IBKR STK count={_ibkr_stk_count} total MV=${ibkr_total:,.0f}")
    live_total   = schwab_total + ibkr_total
    if live_total >= 100_000:  # sanity check — must be at least $100k to trust
        PORTFOLIO_SIZE = round(live_total, -3)  # round to nearest $1000
        print(f"   💼 Portfolio size: ${PORTFOLIO_SIZE:,.0f} (Schwab: ${schwab_total:,.0f} | IBKR stocks: ${ibkr_total:,.0f})")
    else:
        print(f"   💼 Portfolio size: ${PORTFOLIO_SIZE:,.0f} (fallback — live data unavailable)")

    # ── Compute real-time portfolio exposure (CSP + CC from all brokers) ──
    portfolio_exposure = compute_portfolio_exposure(ibkr, PORTFOLIO_SIZE)

    ok  = sum(1 for v in mkt.values() if v["price"]>0)
    print(f"   {ok}/{len(all_tickers)} prices ✓")

    print("🌊 Market intelligence...")
    flow       = []  # UW flow removed

    tide = {"score": 0, "label": "—", "available": False}

    print("   📈 Fetching S&P 500 regime...")
    spy_md     = get_market_data(["SPY"]).get("SPY", {})
    spy_price  = spy_md.get("price", 0)
    spy_ma200  = spy_md.get("ma200", 0)
    spy_above  = spy_price >= spy_ma200 if spy_ma200 > 0 else True
    spy_ma50   = spy_md.get("ma50", 0)
    spy_above_ma50 = spy_price >= spy_ma50 if spy_ma50 > 0 else True
    market_weak = not spy_above or not spy_above_ma50  # WEAK if below either MA
    spy_day_chg  = spy_md.get("day_change_pct", 0)
    spy_regime = {
        "above_ma200":    spy_above,
        "above_ma50":     spy_above_ma50,
        "market_weak":    market_weak,
        "spy":            spy_price,
        "ma200":          round(spy_ma200, 2),
        "ma50":           round(spy_ma50, 2),
        "day_change":     round(spy_day_chg, 4),
        "label": (f"✅ S&P 500 above 200MA (${spy_ma200:.0f}) — Normal environment"
                  if spy_above else
                  f"⚠️ S&P 500 BELOW 200MA (${spy_ma200:.0f}) — Risk regime: reduce size, lower delta")
    }
    print(f"   {spy_regime['label']}")
    print(f"   Market regime: {'⚠️ WEAK' if spy_regime.get('market_weak') else '✅ NORMAL'} (SPY {'above' if spy_regime.get('above_ma50') else 'BELOW'} 50MA, {'above' if spy_above else 'BELOW'} 200MA) — CSP delta preference: {'0.10–0.20' if spy_regime.get('market_weak') else '0.20–0.30'}")

    print("   😱 Fetching VIX...")
    vix_data   = get_vix()
    print(f"   {vix_data['label']}")

    spike_data = {"available": False}  # Not available on current UW plan

    oi_signals = {}  # OI removed — IVP handles individual trade filtering

    # ── GO / NO-GO DECISION ──────────────────────────────────
    gng = market_go_nogo(tide, vix_data, spy_regime)
    print(f"\n{'='*50}")
    print(f"📡 MARKET CONTEXT")
    print(f"   {gng['quality']}")
    print(f"   VIX: {gng['vix']}")
    print(f"   Scanner always runs — IVP filters per stock")
    print(f"{'='*50}\n")

    # ── MORNING MARKET BRIEFING ─────────────────────────────
    # Structure: 1) Market situation  2) Summary verdict  3) Trades follow
    vix   = gng["vix"]



    # ── Price zone analysis — buy side (CSP) and sell side (CC) ──
    # Stored in results.json and used by both Telegram and the dashboard.
    # support_cache: ticker → calc_support_levels() result — used in CSP/LEAPS scoring.
    support_cache = {}
    for _stk in SYMBOL_SETTINGS:
        _smd = mkt.get(_stk, {})
        _sc21 = _smd.get("closes_21d", [])
        _sc63 = _smd.get("closes_63d", [])
        if _sc21 or _sc63:
            _tier = ("Core" if _stk in CORE_STOCKS else "Growth" if _stk in GROWTH_STOCKS
                     else "Cyclical" if _stk in CYCLICAL_STOCKS else "Opportunistic")
            _sl = calc_support_levels(_sc21, _sc63, _tier)
            if _sl:
                support_cache[_stk] = _sl

    price_watch_list = []
    for _tk, _ss in SYMBOL_SETTINGS.items():
        _bu = _ss.get("buy_under", 0)
        _sa = _ss.get("sell_above", 0)
        _p  = mkt.get(_tk, {}).get("price", 0)
        if _p <= 0: continue

        # Buy side — CSP entry zone
        if _bu > 0:
            _pct_from_buy = (_p - _bu) / _bu * 100   # negative = below (in zone)
            if _pct_from_buy <= 0:
                _csp_status = "IN_ZONE"
            elif _pct_from_buy <= 5:
                _csp_status = "APPROACHING"
            elif _pct_from_buy <= 15:
                _csp_status = "WATCHLIST"
            else:
                _csp_status = "FAR"
        else:
            _pct_from_buy = None
            _csp_status   = None

        # Sell side — CC opportunity zone
        if _sa > 0:
            _pct_from_sell = (_sa - _p) / _sa * 100  # positive = below sell_above (normal), negative = above
            if _pct_from_sell <= 0:
                _cc_status = "IN_ZONE"       # at or above sell_above
            elif _pct_from_sell <= 8:
                _cc_status = "APPROACHING"   # within 8% below
            else:
                _cc_status = "WAIT"
        else:
            _pct_from_sell = None
            _cc_status     = None

        _pw_md = mkt.get(_tk, {})
        _pw_sl = support_cache.get(_tk, {})
        price_watch_list.append({
            "ticker":       _tk,
            "price":        round(_p, 2),
            "buy_under":    _bu,
            "sell_above":   _sa,
            "pct_from_buy":  round(_pct_from_buy,  1) if _pct_from_buy  is not None else None,
            "pct_from_sell": round(_pct_from_sell, 1) if _pct_from_sell is not None else None,
            "csp_status":   _csp_status,
            "cc_status":    _cc_status,
            "chg_1d_pct":   round(_pw_md.get("day_change_pct",  0) * 100, 1),
            "chg_5d_pct":   round(_pw_md.get("change_5d_pct",   0) * 100, 1),
            "chg_30d_pct":  round(_pw_md.get("change_30d_pct",  0) * 100, 1),
            # Dynamic support levels (calc_support_levels) — display + scoring signal
            "support_levels": _pw_sl if _pw_sl else None,
            # Earnings proximity — shown as badge on Price Watch tile
            "days_to_earnings": (lambda _ed: max(0, (_ed - datetime.now()).days) if _ed else 999)(get_earnings_date(_tk)),
        })

    # Sort: IN_ZONE → APPROACHING → WATCHLIST (by distance) → FAR
    _csp_ord = {"IN_ZONE": 0, "APPROACHING": 1, "WATCHLIST": 2, "FAR": 3, None: 4}
    price_watch_list.sort(key=lambda x: (
        _csp_ord.get(x["csp_status"], 4),
        x["pct_from_buy"] if x["pct_from_buy"] is not None else 999
    ))

    # ── Build Telegram briefing sections from price_watch_list ──
    _alerts_csp = []   # IN_ZONE + APPROACHING on buy side
    _alerts_cc  = []   # IN_ZONE + APPROACHING on sell side
    _watchlist  = []   # WATCHLIST (5–15% from buy_under)

    for _pw in price_watch_list:
        _tk = _pw["ticker"]; _p = _pw["price"]
        _bu = _pw["buy_under"]; _sa = _pw["sell_above"]
        _pb = _pw.get("pct_from_buy"); _ps = _pw.get("pct_from_sell")

        # REMOVE tickers: no new entry alerts — only CC alerts on existing positions
        _is_remove = _tk in SPECULATIVE_TICKERS

        if not _is_remove:
            if _pw["csp_status"] == "IN_ZONE" and _pb is not None:
                _alerts_csp.append(f"🚨 *{_tk}* ${_p:.2f} — BELOW buy target ${_bu} ({abs(_pb):.1f}% under) — check options now")
            # Phase 1.5: APPROACHING/WATCHLIST removed from Telegram (dashboard only)
            # Only actionable IN_ZONE alerts surface.

        if _sa > 0 and _pw["cc_status"] == "IN_ZONE":
            _alerts_cc.append(f"💰 *{_tk}* ${_p:.2f} — AT/ABOVE sell target ${_sa} — write covered calls")
        # Phase 1.5: APPROACHING CC alerts removed (dashboard only)

    briefing = (
        f"📡 *MARKET BRIEFING — {now_et().strftime('%b %d, %Y %H:%M')} ET*\n"
        f"\n"
        f"*VIX: {vix}*  {vix_data['label']}\n"
    )
    if _alerts_csp:
        briefing += "\n━━━ CSP ENTRY ALERTS ━━━\n"
        for _a in _alerts_csp:
            briefing += f"{_a}\n"
    if _alerts_cc:
        briefing += "\n━━━ CC OPPORTUNITY ALERTS ━━━\n"
        for _a in _alerts_cc:
            briefing += f"{_a}\n"
    # Phase 1.5: WATCHLIST section removed from Telegram (was noise — dashboard only)
    # ── Notable price moves on tracked tickers ──────────────
    # Triggers: 1d ≥ |5%|  OR  5d ≥ |10%|  (30d shown as context)
    _move_alerts = []
    for _pw in price_watch_list:
        _tk  = _pw["ticker"]
        _p   = _pw["price"]
        _bu  = _pw.get("buy_under", 0)
        _sa  = _pw.get("sell_above", 0)
        _md  = mkt.get(_tk, {})
        _c1  = _md.get("day_change_pct", 0) * 100
        _c5  = _md.get("change_5d_pct",  0) * 100
        _c30 = _md.get("change_30d_pct", 0) * 100

        # Phase 1.5: Tighter thresholds for Telegram — reduce noise.
        # Was: 1d ≥ 5%  OR  5d ≥ 10%
        # Now: 1d ≥ 7%  OR  5d ≥ 15%
        _trigger_drop = _c1 <= -7.0 or _c5 <= -15.0
        _trigger_rise = _c1 >=  7.0 or _c5 >=  15.0

        # REMOVE tickers: skip drop alerts (no new entries), keep rise alerts (may want to write CCs)
        if _tk in SPECULATIVE_TICKERS and _trigger_drop and not _trigger_rise:
            continue
        if not (_trigger_drop or _trigger_rise):
            continue

        # "today" label: use actual weekday name if running on weekend (stale Friday data)
        _now_dow = now_et().weekday()  # 0=Mon … 6=Sun
        _is_weekend = _now_dow >= 5
        if _is_weekend:
            # Last trading day was Friday
            _day_lbl = "Fri"
        else:
            _day_lbl = "today"

        # Build move line: show all timeframes that are non-trivial
        _parts = []
        if abs(_c1) >= 1.0:  _parts.append(f"{_c1:+.1f}% {_day_lbl}")
        if abs(_c5) >= 2.0:  _parts.append(f"{_c5:+.1f}% (5d)")
        if abs(_c30) >= 5.0: _parts.append(f"{_c30:+.1f}% (30d)")
        _move_str = " · ".join(_parts) if _parts else f"{_c1:+.1f}% {_day_lbl}"

        # Zone context — only mention what's actionable
        _zone_parts = []
        _pct_buy = _pw.get("pct_from_buy", 999) or 999
        if _pw["csp_status"] == "IN_ZONE":
            _zone_parts.append(f"IN CSP ZONE at ${_bu} — check options now")
        elif _pw["csp_status"] == "APPROACHING" and _bu:
            _zone_parts.append(f"CSP zone ${_bu} ({_pct_buy:.1f}% away)")
        elif _pw["csp_status"] == "WATCHLIST" and _bu and _pct_buy <= 20:
            _zone_parts.append(f"CSP zone ${_bu} ({_pct_buy:.1f}% away)")

        if _pw.get("cc_status") == "IN_ZONE" and _sa:
            _zone_parts.append(f"above CC target ${_sa} — write calls")
        elif _pw.get("cc_status") == "APPROACHING" and _sa:
            _pct_s = _pw.get("pct_from_sell", 0)
            if 0 < _pct_s <= 20:
                _zone_parts.append(f"CC target ${_sa} ({_pct_s:.1f}% away)")

        # Action suggestion — only on trading days (markets open)
        _action = ""
        if not _is_weekend:
            if _trigger_drop:
                _action = "→ consider CSP/LEAPS"
            elif _trigger_rise:
                _action = "→ consider CC"

        _icon = "📉" if _trigger_drop else "📈"
        _zone_txt = " · ".join(_zone_parts)
        _line = f"{_icon} *{_tk}* {_move_str}"
        if _zone_txt:
            _line += f"\n   {_zone_txt}"
        if _action:
            _line += f" {_action}"
        _move_alerts.append((_c1, _trigger_drop, _line))

    if _move_alerts:
        # Sort: big drops first, then big rises
        _move_alerts.sort(key=lambda x: (0 if x[1] else 1, x[0]))
        briefing += "\n━━━ NOTABLE MOVES ━━━\n"
        for _, _, _line in _move_alerts:
            briefing += f"{_line}\n"

    # ── Earnings this week — positions at risk ────────────────
    _earn_alerts = []
    for _etk in ALL_TICKERS:
        _edate = get_earnings_date(_etk)
        if _edate:
            try:
                _edays = (_edate - datetime.now()).days
                if 0 <= _edays <= 7:
                    _ep = mkt.get(_etk, {}).get("price", 0)
                    _ep_str = f" ${_ep:.2f}" if _ep > 0 else ""
                    _earn_alerts.append((_edays, f"  {_etk}{_ep_str} — earnings in {_edays}d"))
            except Exception:
                pass
    if _earn_alerts:
        _earn_alerts.sort()
        briefing += "\n⚠️ *EARNINGS THIS WEEK*\n"
        for _, _line in _earn_alerts:
            briefing += f"{_line}\n"

    # Phase 1.5: Only send briefing if there's actionable content.
    # Suppresses near-empty pings showing just VIX line.
    _has_briefing_content = bool(_alerts_csp or _alerts_cc or _move_alerts or _earn_alerts)
    if _has_briefing_content:
        send_telegram(briefing)
        time.sleep(2)
    else:
        print("   📱 Briefing skipped — no actionable alerts (VIX-only briefing suppressed)")

    # Scanner always runs — IVP filters individual trades

    csp_opps = []; cc_opps  = []; leaps_opps = []
    pmcc_opps= []; spike_opps = []; drop_opps = []; pio_opps = []
    # Caches for dashboard reuse — avoid re-fetching chains
    contracts_cache = {}
    convex_cache = {}    # ticker -> far-OTM long-dated calls for Cheap Convexity scan
    schwab_ivp_cache = {}
    trend_state_cache = {}  # ticker -> leaps_trend_state result, reused by CSP engine
    qty_cache = {}
    avg_cache = {}

    print(f"\n🔍 Scanning {len(all_tickers)} stocks...")
    for ticker in all_tickers:
        # Determine tier
        if ticker in CORE_STOCKS:        tier = "Core"
        elif ticker in GROWTH_STOCKS:    tier = "Growth"
        elif ticker in CYCLICAL_STOCKS:  tier = "Cyclical"
        else:                            tier = "Opportunistic"
        is_core = (tier == "Core")
        md    = mkt.get(ticker,{})
        price = md.get("price",0)
        if price <= 0: continue

        w52h       = md.get("week52_high", price)
        w52l       = md.get("week52_low",  price)
        pir        = position_in_range(price, w52l, w52h)
        pullback   = pullback_from_high(price, w52h)

        # Earnings check
        earn_date  = get_earnings_date(ticker)

        # Stock quality gate — framework step 1
        quality    = stock_quality_check(ticker, md, earn_date)

        # Use Schwab option chain — fetch short (75d) for CSP/CC and long (750d) for LEAPS
        if SCHWAB_APP_KEY:
            from datetime import timedelta as _td
            from_d       = datetime.now().strftime("%Y-%m-%d")
            to_d_short   = (datetime.now() + _td(days=75)).strftime("%Y-%m-%d")
            to_d_leaps   = (datetime.now() + _td(days=750)).strftime("%Y-%m-%d")
            to_d_convex  = (datetime.now() + _td(days=CVX_FETCH_DAYS)).strftime("%Y-%m-%d")
            contracts_short = schwab_get_option_chain(ticker, from_d, to_d_short)
            contracts_leaps = schwab_get_option_chain(ticker, from_d, to_d_leaps)
            # Dedicated far-OTM long-dated fetch for Cheap Convexity (DTE 540–1100)
            contracts_convex_raw = schwab_get_option_chain(ticker, from_d, to_d_convex)
            convex_contracts = [c for c in contracts_convex_raw
                                if c.get("option_type") == "C"
                                and CVX_DTE_MIN <= (datetime.strptime(c["expiry"],"%Y-%m-%d") - datetime.now()).days <= CVX_DTE_MAX]
            convex_cache[ticker] = convex_contracts
            # Merge: use short for CSP/CC (more contracts, faster), add leaps-range
            leaps_contracts = [c for c in contracts_leaps
                               if (datetime.strptime(c["expiry"],"%Y-%m-%d") - datetime.now()).days >= LEAPS_DTE_MIN]
            contracts = contracts_short + leaps_contracts
        if not SCHWAB_APP_KEY or not contracts:
            contracts = get_option_contracts(ticker)
        if not contracts: continue

        # Compute ATM IV from individual contract volatilities (NOT chain-level)
        # Schwab chain "volatility" field is unreliable (same for all stocks)
        # Per-contract "volatility" IS correct and varies per stock
        _atm_contracts = [c for c in contracts
                          if c.get("option_type") == "P"
                          and abs(float(c.get("delta",0) or 0)) > 0.40
                          and abs(float(c.get("delta",0) or 0)) < 0.60
                          and 20 <= (datetime.strptime(c["expiry"],"%Y-%m-%d") - datetime.now()).days <= 50
                          and float(c.get("iv",0) or 0) > 0.01]
        if _atm_contracts:
            _ivs = sorted([float(c.get("iv",0)) for c in _atm_contracts])
            _atm_iv = _ivs[len(_ivs)//2]  # median ATM IV — correct per-stock value
        else:
            _atm_iv = 0.29  # fallback

        # IVP = realized vol percentile from Schwab 1yr price history
        _schwab_ivp = schwab_get_ivp(ticker) if SCHWAB_APP_KEY else 0
        if _schwab_ivp > 0:
            ivdata = {"iv_current": _atm_iv, "iv_low": _atm_iv*0.5, "iv_high": _atm_iv*2.0, "ivp": _schwab_ivp}
            print(f"   IVP {ticker}: {_schwab_ivp:.1f}% (ATM IV={_atm_iv*100:.1f}%)")
        else:
            # Fallback: estimate IVP from ATM IV relative to typical annual range
            import math as _m
            ivp_est = round(min(95, max(5, 100 * (1 - _m.exp(-_atm_iv / 0.25)))), 1)
            ivdata = {"iv_current": _atm_iv, "iv_low": _atm_iv*0.5, "iv_high": _atm_iv*2.0, "ivp": ivp_est}
            print(f"   IVP {ticker}: {ivp_est:.1f}% estimated (ATM IV={_atm_iv*100:.1f}%, schwab_get_ivp failed)")
        sizing     = position_check(ticker, ibkr)
        qty        = sizing["quantity"]
        avg        = sizing["avg_cost"]
        # Cache full merged contracts (short + leaps) for dashboard scan
        contracts_cache[ticker]  = contracts  # already merged short+leaps
        schwab_ivp_cache[ticker] = ivdata["ivp"]
        qty_cache[ticker]        = qty
        avg_cache[ticker]        = avg
        dp_stock   = {"show": False, "score": 50, "total_notional": 0}
        dp_leaps   = {"show": False, "score": 50, "total_notional": 0}
        dp         = dp_stock
        dp_boost   = 1.2 if dp.get("significant") else 1.0  # only boost on significant dark pool

        oi_sig     = {}
        oi_warning = False
        exp_bdown  = {}

        base = {"ticker":ticker,"price":price,"pir":pir,
                "tier":tier,
                "w52_low":w52l,"w52_high":w52h,
                "pullback_pct":round(pullback*100,1),
                "ivp":ivdata["ivp"],"quality":quality,
                "sizing":sizing,"darkpool":dp_stock,"darkpool_leaps":dp_leaps,
                "oi_signal":oi_sig,"expiry_breakdown":exp_bdown,
                "oi_warning":oi_warning}

        # ── CSP ──────────────────────────────────────────
        if (gng["sell_premium"]
                and sizing["status"] != "OVERWEIGHT"
                and not quality["hard_stop"]
                and not oi_warning
                and ticker not in LEAPS_ONLY):
            # Apply risk regime adjustments when S&P below 200MA
            q_adjusted = dict(quality)
            if gng.get("spy_warning"):
                q_adjusted["quality_score"] = max(0, quality["quality_score"] - 1)
            csp, _ = find_best_csp(ticker, price, contracts, ivdata, pir, q_adjusted, sizing=sizing, market_weak=spy_regime.get('market_weak', False))
            if csp:
                # below_min trades still shown but scored lower
                score_mult = 0.5 if csp.get("below_min") else 1.0
                _s = {"tier": base.get("tier","Opportunistic"),
                      "delta": csp.get("delta",0),
                      "ivp": base.get("ivp",50),
                      "annualized_return": csp.get("annualized_return",0),
                      "pullback_pct": base.get("pullback_pct",0),
                      "warnings": [],
                      "breakeven": csp.get("strike",0) - csp.get("premium",0)}
                csp_opps.append({**base,"csp":csp,
                    "market_weak": spy_regime.get("market_weak", False),
                    "score": score_csp({**_s, "market_weak": spy_regime.get("market_weak", False)}) * (0.5 if csp.get("below_min") else 1.0)})
                print(f"  [{tier}] {ticker}: 💰 CSP ${csp['strike']} {csp['annualized_return']}% ann δ{csp['delta']} IVP{ivdata['ivp']:.0f}%")

        # ── Position Income Optimization (Mode 4) ──────────────
        # Generate income from existing holdings regardless of market conditions
        # Ignores 200MA, pullback, gap rules — pure income focus
        # Phase 1.5: Disabled via ENABLE_PIO flag — too noisy, not used for trading.
        # Zone-first CCs (above) are the only CC opportunity surfaced.
        if ENABLE_PIO and qty >= 100 and avg > 0:
            pos_status = get_position_status(sizing["current_pct"])
            pnl_status = get_pnl_status(avg, price)
            # Always run PIO — different delta rules than standard CC
            # PIO adjusts delta by P&L status, ignores quality filters
            pio_cc, _ = find_position_income_cc(
                ticker, price, qty, avg, contracts, ivdata, pos_status, pnl_status,
                already_covered=portfolio_exposure.get("cc_shares_covered",{}).get(ticker, 0))
            if pio_cc:
                _s = {"tier": base.get("tier","Opportunistic"),
                      "delta": pio_cc.get("delta",0),
                      "ivp": base.get("ivp",50),
                      "annualized_return": pio_cc.get("annualized_return",0),
                      "strike": pio_cc.get("strike",0),
                      "breakeven": pio_cc.get("breakeven", pio_cc.get("avg_cost",0))}
                pio_opps.append({**base, "pio_cc": pio_cc,
                    "pos_status": pos_status, "pnl_status": pnl_status,
                    "score": score_cc(_s)})
                print(f"  [{tier}] {ticker}: 💼 PIO CC ${pio_cc['strike']} "
                      f"{pio_cc['annualized_return']}% ann | {pnl_status} | δ{pio_cc['delta']}")

        # ── Post-Drop CSP (Mode 3) ───────────────────────────
        drop_info = detect_price_drop(ticker, md)
        if (drop_info["is_drop"]
                and tier in DROP_CSP_ALLOWED_TIERS
                and sizing["status"] != "OVERWEIGHT"):
            drop_csp, drop_timing = find_drop_csp(
                ticker, price, contracts, ivdata, pir,
                quality, drop_info, tier, sizing)
            if drop_csp:
                drop_opps.append({**base, "drop_csp": drop_csp,
                    "drop_info": drop_info,
                    "score": score_csp({"tier": base.get("tier","Core"),
                      "delta": drop_csp.get("delta",0), "ivp": base.get("ivp",50),
                      "annualized_return": drop_csp.get("annualized_return",0),
                      "pullback_pct": base.get("pullback_pct",0), "warnings": []})})
                print(f"  [{tier}] {ticker}: 🔻 DROP CSP ${drop_csp['strike']} "
                      f"{drop_csp['annualized_return']}% ann | "
                      f"{drop_info['today_change']:+.1f}% drop | "
                      f"IVP {ivdata['ivp']:.0f}%")

        # ── Opportunistic Spike CC ────────────────────────
        # Triggered BY gap moves — opposite of income mode which skips them.
        # Fires for ANY ticker where you hold 100+ shares and it spikes 8%+.
        # A spike CC is COVERED (you own the shares), so it intentionally
        # overrides spreads_only — selling a call on owned stock is not naked.
        spike_info = detect_price_spike(ticker, md)
        if (spike_info["is_spike"]
                and qty >= 100
                and (quality.get("days_to_earnings") is None
                     or quality["days_to_earnings"] > OPP_EARNINGS_MIN)):
            spike_cc, _ = find_spike_cc(
                ticker, price, qty, avg, contracts, ivdata, spike_info)
            if spike_cc:
                spike_opps.append({**base, "spike_cc": spike_cc,
                    "spike_info": spike_info,
                    "score": score_cc({"tier": base.get("tier","Opportunistic"),
                      "delta": spike_cc.get("delta",0), "ivp": base.get("ivp",50),
                      "annualized_return": spike_cc.get("annualized_return",0),
                      "strike": spike_cc.get("strike",0),
                      "breakeven": spike_cc.get("avg_cost",0),
                      "day_change_pct": base.get("day_change_pct",0)})})
                print(f"  [{tier}] {ticker}: ⚡ SPIKE CC ${spike_cc['strike']} "
                      f"{spike_cc['annualized_return']}% ann | "
                      f"{spike_info['today_change']:+.1f}% move")

        # ── CC ───────────────────────────────────────────
        holding = stk_hold.get(ticker,{})
        qty = holding.get("quantity",0); avg = holding.get("avg_cost",0)
        if (gng["sell_premium"]
                and qty >= 100
                and not quality["hard_stop"]
                and ticker not in LEAPS_ONLY):
            cc, _ = find_best_cc(ticker, price, qty, avg, contracts, ivdata, pir,
                               already_covered=portfolio_exposure.get("cc_shares_covered",{}).get(ticker, 0),
                               sell_above=SYMBOL_SETTINGS.get(ticker, {}).get("sell_above", 0),
                               buy_under=SYMBOL_SETTINGS.get(ticker, {}).get("buy_under", 0))
            if cc:
                cc_opps.append({**base,"cc":cc,
                    "score":score_cc({"tier":base.get("tier","Opportunistic"),
                                      "delta":cc.get("delta",0),"ivp":base.get("ivp",50),
                                      "annualized_return":cc.get("annualized_return",0),
                                      "strike":cc.get("strike",0),
                                      "breakeven":cc.get("avg_cost",0)})})
                print(f"  {ticker}: 📈 CC  ${cc['strike']} {cc['annualized_return']}% ann δ{cc['delta']}")

        # ── LEAPS ────────────────────────────────────────
        # LEAPS use leaps_hard_stop (more lenient) — 200MA not blocking for Core
        leaps_blocked = quality.get("leaps_hard_stop", quality["hard_stop"])
        if gng["buy_leaps"] and not leaps_blocked:
            # Trend classifier — determines entry timing quality
            _trend  = leaps_trend_state(ticker, price)
            trend_state_cache[ticker] = _trend  # cache for CSP engine
            _w52h   = mkt.get(ticker, {}).get("week52_high", price * 1.3)
            _ta     = leaps_trend_action(_trend, ivdata["ivp"], price, _w52h)
            leaps, leaps_timing = find_best_leaps(ticker, price, contracts, ivdata, pir)
            if leaps:
                leaps["trend"]        = _trend
                leaps["trend_action"] = _ta
                leaps["trend_label"]  = _ta["label"]
                leaps["trend_signal"] = _ta["signal"]
                _lbl = _ta["label"]; _act = _ta["action"]; _st = _trend.get("state")
                print(f"   TREND DEBUG {ticker}: label={_lbl!r} action={_act!r} state={_st!r}")
            if leaps is None and ivdata["ivp"] > 0:
                print(f"  [{tier}] {ticker}: LEAPS rejected — IVP {ivdata['ivp']:.0f}% timing: {leaps_timing.get('signal','')[:50]}")
        else:
            leaps = None
            leaps_timing = {}
            if leaps_blocked and tier in ("Core","Growth"):
                print(f"  [{tier}] {ticker}: LEAPS hard stop (earnings/price)")
        if leaps:
            _tl = leaps.get("trend_label", "")
            _ts = leaps.get("trend_signal", "")
            _ta_action = leaps.get("trend_action", {}).get("action", "WATCH")
            leaps_opps.append({**base, "leaps": leaps,
                "trend_label":  _tl,
                "trend_signal": _ts,
                "trend_action": _ta_action,
                "score": score_leaps({"tier": base.get("tier","Opportunistic"),
                                      "delta": leaps.get("delta",0), "ivp": base.get("ivp",100),
                                      "extrinsic_pct": leaps.get("extrinsic_pct",100),
                                      "dte": leaps.get("dte",0)})})
            print(f"  {ticker}: LEAPS ${leaps['strike']} d{leaps['delta']} ext{leaps['extrinsic_pct']}% IVP{ivdata['ivp']:.0f}% | {_tl}")


        # ── PMCC ─────────────────────────────────────────
        existing_leaps = find_existing_leaps(ticker, ibkr)
        if existing_leaps:
            pmcc, _ = find_pmcc_short_call(ticker, price, existing_leaps, contracts, ivdata, pir)
            if pmcc:
                pmcc_opps.append({**base,"pmcc":pmcc,"existing_leaps":existing_leaps,
                    "score":score_cc({"tier":base.get("tier","Opportunistic"),
                                      "delta":pmcc.get("delta",0),"ivp":base.get("ivp",50),
                                      "annualized_return":pmcc.get("annualized_return",0),
                                      "strike":pmcc.get("strike",0),
                                      "breakeven":pmcc.get("pmcc_breakeven",0)})})
                print(f"  {ticker}: ⚡ PMCC ${pmcc['strike']} {pmcc['annualized_return']}% ann")

    # ── Sort & top 3 each ─────────────────────────────────
    for lst in [csp_opps,cc_opps,leaps_opps,pmcc_opps]:
        lst.sort(key=lambda x: x["score"], reverse=True)

    top_csps  = csp_opps[:3];  top_ccs   = cc_opps[:3]
    top_leaps = leaps_opps[:5];top_pmccs = pmcc_opps[:3]  # top_leaps capped for Telegram only; dashboard shows all
    top_spikes = spike_opps[:3]
    top_drops  = drop_opps[:3]
    top_pio    = pio_opps[:5]  # show up to 5 position income trades

    total = sum(len(x) for x in [top_csps,top_ccs,top_leaps,top_pmccs,top_spikes,top_drops,top_pio])
    print(f"\n🏆 {len(top_csps)} CSPs | {len(top_ccs)} CCs | {len(top_leaps)} LEAPS | "
          f"{len(top_pmccs)} PMCCs")

    if total == 0:
        print("✅ No qualifying opportunities today.")
        return

    # ── Opportunistic Volatility Scan ────────────────────
    print("\n⚡ Opportunistic volatility scan...")
    opp_opps = []
    # Scan ALL tickers including ones not normally scanned for spikes
    opp_tickers = ALL_TICKERS + ["NBIS", "PLTR", "IBIT"]
    opp_tickers = list(set(opp_tickers))  # deduplicate
    for ticker in opp_tickers:
        try:
            md_t  = mkt.get(ticker, {})
            price = md_t.get("price", 0)
            if price <= 0:
                continue
            # Check for volatility spike
            spike = check_volatility_spike(ticker, md_t)
            if not spike:
                continue
            # Check earnings blackout
            earn_date = get_earnings_date(ticker)
            if earn_date:
                days_earn = (earn_date - datetime.now()).days
                if 0 < days_earn < OPP_EARNINGS_MIN:
                    print(f"  {ticker}: spike {spike['spike_pct']}% but earnings in {days_earn}d — skip")
                    continue
            # Check IVP
            ivdata_t = calculate_ivp(get_option_contracts(ticker), ticker=ticker)
            if ivdata_t.get("ivp", 0) < OPP_IVP_MIN:
                print(f"  {ticker}: spike {spike['spike_pct']}% but IVP {ivdata_t['ivp']:.0f}% too low")
                continue
            # Check if holding shares (from IBKR or Schwab)
            pos   = ibkr.get(ticker, {})
            qty   = float(pos.get("quantity", 0))
            avg   = float(pos.get("avg_cost", 0))
            if qty < 100:
                print(f"  {ticker}: spike {spike['spike_pct']}% but <100 shares held")
                continue
            # Get contracts and find best CC
            contracts_t = get_option_contracts(ticker)
            if SCHWAB_APP_KEY:
                from datetime import timedelta
                from_d = datetime.now().strftime("%Y-%m-%d")
                to_d   = (datetime.now() + timedelta(days=35)).strftime("%Y-%m-%d")
                sc = schwab_get_option_chain(ticker, from_d, to_d)
                if sc:
                    contracts_t = sc
            tier_t = ("Core" if ticker in CORE_STOCKS else
                      "Growth" if ticker in GROWTH_STOCKS else
                      "Cyclical" if ticker in CYCLICAL_STOCKS else "Opportunistic")
            sizing_t = position_check(ticker, ibkr)
            cc_opp = find_opp_cc(ticker, price, qty, avg, contracts_t, ivdata_t)
            if cc_opp:
                opp_opps.append({
                    "ticker": ticker, "price": price,
                    "tier": tier_t, "spike": spike,
                    "cc": cc_opp, "sizing": sizing_t,
                    "ivp": ivdata_t.get("ivp", 0),
                })
                print(f"  {ticker}: ⚡ SPIKE {spike['spike_pct']}% | CC ${cc_opp['strike']} {cc_opp['annualized_return']}% ann")
        except Exception as e:
            print(f"  {ticker} opp error: {e}")

    if opp_opps:
        print(f"\n⚡ {len(opp_opps)} volatility spike opportunities found")
    else:
        print("   No volatility spike setups today")

    # ── Claude analysis ───────────────────────────────────
    print("\n🧠 Claude analysis...")
    analysis = claude_analyze(top_csps,top_ccs,top_leaps,top_pmccs,[],top_spikes,top_drops,top_pio)
    if analysis: print(f"\n{analysis}")

    # ── Telegram green-light filter ───────────────────────
    # Philosophy: Telegram = take action NOW. Only high-conviction trades get a ping.
    # Dashboard always shows everything (review + execution). PMCC is dashboard-only.
    # Spikes and drops skip the score gate — they are time-sensitive by nature.
    def _tg_green(opps: list, mode: str) -> list:
        """Return only trades scoring >= TELEGRAM_MIN_SCORE_PCT of SCORE_MAX[mode]."""
        mx  = SCORE_MAX.get(mode, 12)
        thr = math.ceil(TELEGRAM_MIN_SCORE_PCT * mx)
        return [o for o in opps if o.get("score", 0) >= thr]

    tg_csps   = _tg_green(top_csps,  "CSP")
    tg_ccs    = _tg_green(top_ccs,   "CC")
    # LEAPS Telegram filter: only BUY trend + high score
    # trend_action is stored as string at top level of opp dict
    # Prevents every scan from flooding Telegram with routine LEAPS
    def _tg_leaps_filter(opps: list) -> list:
        mx  = SCORE_MAX.get("LEAPS", 13)
        thr = math.ceil(TELEGRAM_MIN_SCORE_PCT * mx)
        result = []
        for o in opps:
            if o.get("score", 0) < thr: continue
            # Only send when trend recommends buying (BUY action, not WATCH/WAIT)
            trend_action = o.get("trend_action", "WATCH")
            if isinstance(trend_action, dict):
                trend_action = trend_action.get("action", "WATCH")
            if trend_action != "BUY": continue
            result.append(o)
        return result
    tg_leaps  = _tg_leaps_filter(top_leaps)
    tg_spikes = top_spikes   # time-sensitive — no score gate
    tg_drops  = top_drops    # time-sensitive — no score gate

    # ── Phase 1.6: STRICT_ZONE_TELEGRAM filter ─────────────────
    # When the flag is True, suppress Telegram alerts unless the opportunity
    # is in its actionable zone (CSP: price ≤ buy_under; CC: price ≥ sell_above;
    # LEAPS: price ≤ buy_under × 1.10) OR IVR override applies.
    if STRICT_ZONE_TELEGRAM:
        def _apply_strict_zone(opps: list, strategy: str) -> list:
            kept = []
            for o in opps:
                _tk = o.get("ticker", "")
                _sym = SYMBOL_SETTINGS.get(_tk, {})
                _bu  = _sym.get("buy_under", 0) or 0
                _sa  = _sym.get("sell_above", 0) or 0
                _px  = o.get("price", 0) or 0
                _ivr = o.get("ivp", 0) or 0
                # Pull atm_iv from nested strategy dict (csp/cc/leaps)
                _atm_iv = 0
                for _k in ("csp", "cc", "leaps"):
                    if isinstance(o.get(_k), dict):
                        _atm_iv = o[_k].get("atm_iv", 0) or _atm_iv
                _iz, _reason = compute_in_zone(strategy, _px, _bu, _sa, _ivr, _atm_iv)
                if _iz:
                    kept.append(o)
                else:
                    print(f"   📵 TG SUPPRESS {_tk} {strategy}: {_reason}")
            return kept
        _pre_counts = (len(tg_csps), len(tg_ccs), len(tg_leaps))
        tg_csps  = _apply_strict_zone(tg_csps,  "CSP")
        tg_ccs   = _apply_strict_zone(tg_ccs,   "CC")
        tg_leaps = _apply_strict_zone(tg_leaps, "LEAPS")
        print(f"   📵 STRICT_ZONE_TELEGRAM filter: "
              f"{_pre_counts[0]}→{len(tg_csps)} CSPs, "
              f"{_pre_counts[1]}→{len(tg_ccs)} CCs, "
              f"{_pre_counts[2]}→{len(tg_leaps)} LEAPS")

    # PMCC: dashboard only — too complex/optional for a ping
    # Spike CCs: NOW sent to Telegram (covered calls into a spike — the low-risk,
    # high-priority move). Drops also sent.

    has_real_opps = any([top_csps, top_ccs, top_leaps, top_pmccs, top_drops, top_spikes])
    tg_any        = any([tg_csps, tg_ccs, tg_leaps, tg_drops, tg_spikes])

    print(f"   Telegram filter: {len(tg_csps)} CSPs | {len(tg_ccs)} CCs | "
          f"{len(tg_leaps)} LEAPS | {len(tg_drops)} Drops | {len(tg_spikes)} Spike CCs "
          f"(from {len(top_csps)}+{len(top_ccs)}+{len(top_leaps)} before filter) | "
          f"{len(opp_opps)} spike opps")

    # ── Telegram — ORDER: Summary → Trades ───────────────
    print("\n📱 Sending...")

    # 1. Claude summary — only when there are Telegram-grade opportunities
    #    (was: any opps anywhere; now: only when something will actually be sent)
    if analysis and tg_any:
        send_telegram(f"🧠 *CLAUDE SUMMARY*\n\n{analysis}")
        time.sleep(2)

    # 2. Green-light trade alerts.
    #    Spike CC sent FIRST — it's the low-risk, time-sensitive priority (sell
    #    calls into a spike on shares you already own).
    if tg_spikes:
        send_telegram("━━━ *⚡ SPIKE CC (sell into strength)* ━━━"); time.sleep(1)
        for o in tg_spikes: send_telegram(fmt_spike_cc(o)); time.sleep(2)
    if tg_drops:
        send_telegram("━━━ *🔻 POST-DROP CSP* ━━━"); time.sleep(1)
        for o in tg_drops: send_telegram(fmt_drop_csp(o)); time.sleep(2)
    if tg_csps:
        send_telegram("━━━ *✅ CSP* ━━━"); time.sleep(1)
        for o in tg_csps: send_telegram(fmt_csp(o)); time.sleep(2)
    if tg_ccs:
        send_telegram("━━━ *✅ COVERED CALL* ━━━"); time.sleep(1)
        for o in tg_ccs: send_telegram(fmt_cc(o)); time.sleep(2)
    if tg_leaps:
        send_telegram("━━━ *✅ LEAPS* ━━━"); time.sleep(1)
        for o in tg_leaps: send_telegram(fmt_leaps(o)); time.sleep(2)

    # PMCC — dashboard only (never sent to Telegram)

    # ── Save results.json for dashboard ─────────────────────
    def opp_to_dict(o, strategy_key):
        """Convert opportunity to clean dict for JSON — includes sizing spec fields."""
        s          = o.get(strategy_key, {})
        mode       = strategy_key.upper()
        strike     = s.get("strike", 0)
        contracts  = s.get("max_contracts", 1)
        premium    = s.get("premium", 0)
        ticker     = o.get("ticker", "")

        # Action label — SELL CSP / SCALE CSP / SELL CC / INCREASE COVERAGE
        _has_csp = any(p["ticker"] == ticker for p in portfolio_exposure.get("csp_positions", []))
        _has_cc  = any(p["ticker"] == ticker for p in portfolio_exposure.get("cc_positions", []))
        if mode == "CSP":
            action_label = "SCALE CSP" if _has_csp else "SELL CSP"
        elif mode in ("CC", "PIO", "SPIKE_CC"):
            action_label = "INCREASE COVERAGE" if _has_cc else "SELL CC"
        else:
            action_label = mode

        # Sizing block per spec §5, §6
        if mode == "CSP":
            cso             = round(strike * 100 * contracts, 0)
            current_cso     = portfolio_exposure.get("total_csp_obligation", 0)
            max_cso         = portfolio_exposure.get("max_csp_allocation_usd", PORTFOLIO_SIZE * MAX_CSP_ALLOCATION_PCT)
            resulting_cso   = current_cso + cso
            remaining_after = max(0, portfolio_exposure.get("remaining_csp_capacity", 0) - cso)
            # 80% profit target: close at 20% of original premium
            _dte            = s.get("dte", 30)
            _profit_target_price = round(premium * 0.20, 2)  # buyback at 20% of premium
            # Days estimate: theta decay is front-loaded; ~50-60% of time captures ~80% of decay
            _profit_target_days  = max(1, round(_dte * 0.55))
            sizing = {
                "suggested_contracts":  contracts,
                "capital_required":     cso,
                "premium_received":     round(premium * 100 * contracts, 2),
                "cso":                  cso,
                "action_label":         action_label,
                "current_obligation":   round(current_cso, 0),
                "resulting_obligation": round(resulting_cso, 0),
                "resulting_pct":        round(resulting_cso / PORTFOLIO_SIZE * 100, 1) if PORTFOLIO_SIZE > 0 else 0,
                "remaining_after":      round(remaining_after, 0),
                "within_limit":         resulting_cso <= max_cso,
                "profit_target_price":  _profit_target_price,   # close at this price = 80% profit
                "profit_target_days":   _profit_target_days,    # ~days to reach (estimate)
            }
        elif mode in ("CC", "PIO", "SPIKE_CC"):
            sizing = {
                "suggested_contracts": contracts,
                "capital_required":    0,
                "premium_received":    round(premium * 100 * contracts, 2),
                "nva":                 round(strike * 100 * contracts, 0),
                "shares_covered":      contracts * 100,
                "action_label":        action_label,
            }
        else:
            sizing = {
                "suggested_contracts": contracts,
                "capital_required":    round(strike * 100 * contracts, 0),
                "premium_received":    round(premium * 100 * contracts, 2),
                "action_label":        action_label,
            }

        d = {
            "ticker":            ticker,
            "tier":              o.get("tier",""),
            "price":             o.get("price",0),
            "ivp":               round(o.get("ivp",0),1),
            "mode":              mode,
            "action_label":      action_label,
            "strike":            strike,
            "days_to_earnings":  o.get("quality", {}).get("days_to_earnings", 999),
            "expiry":            s.get("expiry",""),
            "dte":               s.get("dte",0),
            "premium":           premium,
            "annualized_return": s.get("annualized_return",0),
            "delta":             s.get("delta",0),
            "signal":            s.get("timing",{}).get("signal","") or "",
            "below_min":         s.get("below_min", False),
            "risk_note":         None,
            "sizing":            sizing,
        }
        # Pass through trend fields for LEAPS
        if mode == "LEAPS":
            d["trend_label"]  = o.get("trend_label",  s.get("trend_label",  ""))
            d["trend_signal"] = o.get("trend_signal", s.get("trend_signal", ""))
            d["trend_action"] = o.get("trend_action", s.get("trend_action", {}) if isinstance(s.get("trend_action"), dict) else {})
            if isinstance(d["trend_action"], dict):
                d["trend_action"] = d["trend_action"].get("action", "WATCH")
            # Add LEAPS table fields — may come from nested s or top-level o
            _be = s.get("breakeven", o.get("breakeven", 0))
            _ep = s.get("extrinsic_pct", o.get("extrinsic_pct", 0))
            _price = o.get("price", 0)
            d["extrinsic_pct"]  = round(_ep, 1) if _ep else 0
            d["breakeven"]      = round(_be, 2) if _be else round(strike + premium, 2)
            d["breakeven_pct"]  = round((_be - _price) / _price * 100, 1) if _be and _price else 0
            d["leaps_band"]     = o.get("leaps_band", s.get("leaps_band", "sweet_spot"))
            d["leaps_label"]    = o.get("leaps_label", s.get("leaps_label", "🎯 Sweet spot"))
            d["is_recommended"] = o.get("is_recommended", s.get("is_recommended", False))
            d["buy_under"]      = o.get("buy_under", s.get("buy_under", None))
            d["days_to_earnings"] = o.get("days_to_earnings", o.get("quality", {}).get("days_to_earnings", 999))
            # leaps_note: dynamic extrinsic signal
            _ext = d["extrinsic_pct"]
            d["leaps_note"] = (
                f"🔥 Excellent cost — {_ext:.1f}% extrinsic. Prioritize this." if _ext < 20
                else f"⚠️ Extrinsic {_ext:.1f}% — getting expensive. Compare vs lower-strike bands." if _ext < 30
                else f"❌ Extrinsic {_ext:.1f}% — expensive. Check if a cheaper band exists for this ticker." if _ext < 40
                else f"🚫 Extrinsic {_ext:.1f}% — avoid. IV too high or strike too close to ATM."
            )
        return d

    def find_best_csp_relaxed(ticker, price, contracts):
        """Relaxed CSP finder for dashboard — wider filters, no IVP minimum."""
        if not contracts or price <= 0: return None
        best = None; best_score = 0
        for c in contracts:
            try:
                if c.get("option_type") != "P": continue
                expiry = datetime.strptime(c["expiry"], "%Y-%m-%d")
                dte = (expiry - datetime.now()).days
                _mdte = 35 if spy_regime.get("market_weak") else 20
                if not (_mdte <= dte <= 60): continue  # weak: min 35 DTE
                strike = float(c["strike"])
                if strike <= 0 or strike >= price: continue
                bid = float(c.get("nbbo_bid", 0) or 0)
                ask = float(c.get("nbbo_ask", 0) or 0)
                mid = (bid + ask) / 2
                if mid < 0.05: continue
                # Wider delta range for dashboard
                delta = float(c.get("delta", 0) or 0)
                if abs(delta) == 0:
                    delta = estimate_delta(price, strike, dte, 0.30, "P")
                if delta is None: continue
                delta = abs(delta)
                if not (0.10 <= delta <= 0.40): continue  # wider: 0.10-0.40
                # Liquidity — relaxed
                oi = int(c.get("open_interest", 0) or 0)
                if oi < 100: continue  # much lower than 1000
                annualized = (mid / strike) * (365 / dte) * 100
                if annualized < 5 or annualized > 200: continue
                otm_pct = round((price - strike) / price * 100, 1)
                iv = round(float(c.get("iv", 0) or 0) * 100, 1)
                _s = {"tier": "Opportunistic", "delta": delta, "dte": dte, "ivp": 50,
                      "annualized_return": annualized, "pullback_pct": 0,
                      "market_weak": spy_regime.get("market_weak", False), "warnings": []}
                score = score_csp(_s)
                if score > best_score:
                    best_score = score
                    best = {
                        "strike": strike, "expiry": expiry.strftime("%Y-%m-%d"),
                        "dte": dte, "bid": round(bid,2), "ask": round(ask,2),
                        "premium": round(mid,2), "delta": round(delta,2),
                        "iv": iv, "otm_pct": otm_pct,
                        "annualized_return": round(annualized,1),
                        "below_min": annualized < CSP_MIN_ANNUALIZED,
                        "timing": {"signal": f"IVP context only — review before trading"},
                        "collateral": round(strike * 100, 0),
                        "max_contracts": max(1, int(PORTFOLIO_SIZE * 0.03 / (strike * 100))),
                    }
            except: continue
        return best

    def find_best_cc_relaxed(ticker, price, qty, avg_cost, contracts):
        """Relaxed CC finder for dashboard."""
        if not contracts or price <= 0 or qty < 100: return None
        best = None; best_score = 0
        max_contracts = max(1, int(qty / 100))
        for c in contracts:
            try:
                if c.get("option_type") != "C": continue
                expiry = datetime.strptime(c["expiry"], "%Y-%m-%d")
                dte = (expiry - datetime.now()).days
                if not (20 <= dte <= 60): continue
                strike = float(c["strike"])
                if strike <= price * 0.98: continue  # must be near or above current price
                if avg_cost > 0 and strike < avg_cost: continue  # above cost basis
                bid = float(c.get("nbbo_bid", 0) or 0)
                ask = float(c.get("nbbo_ask", 0) or 0)
                mid = (bid + ask) / 2
                if mid < 0.05: continue
                delta = float(c.get("delta", 0) or 0)
                if abs(delta) == 0:
                    delta = estimate_delta(price, strike, dte, 0.30, "C")
                if delta is None: continue
                delta = abs(delta)
                if not (0.15 <= delta <= 0.35): continue  # hard max 0.35 per doc
                oi = int(c.get("open_interest", 0) or 0)
                if oi < 100: continue
                annualized = (mid / strike) * (365 / dte) * 100
                if annualized < 3 or annualized > 200: continue
                # Use canonical CC score — not annualized × delta
                _opp = {"tier": "Opportunistic", "delta": delta, "ivp": 50,
                        "annualized_return": annualized, "strike": strike,
                        "breakeven": avg_cost}
                score = score_cc(_opp)
                if score > best_score:
                    best_score = score
                    best = {
                        "strike": strike, "expiry": expiry.strftime("%Y-%m-%d"),
                        "dte": dte, "bid": round(bid,2), "ask": round(ask,2),
                        "premium": round(mid,2), "delta": round(delta,2),
                        "annualized_return": round(annualized,1),
                        "below_min": annualized < CC_MIN_ANNUALIZED,
                        "avg_cost": round(avg_cost,2),
                        "max_contracts": max_contracts,
                        "timing": {"signal": ""},
                        "otm_pct": round((strike-price)/price*100,1),
                        "iv": 0,
                        "ivp": 0,
                    }
            except: continue
        return best

    # Scoring functions now at module level — see score_cc, score_csp, score_leaps, quality_label

    # ── Dashboard-only scan — ALL candidates, relaxed filters ──
    dashboard_csps  = []
    dashboard_ccs   = []
    dashboard_leaps = []
    dashboard_convexity = []

    for ticker in all_tickers:
        if ticker in CORE_STOCKS:        tier = "Core"
        elif ticker in GROWTH_STOCKS:    tier = "Growth"
        elif ticker in CYCLICAL_STOCKS:  tier = "Cyclical"
        else:                            tier = "Opportunistic"

        md    = mkt.get(ticker, {})
        price = md.get("price", 0)
        if price <= 0: continue

        contracts_d  = contracts_cache.get(ticker, [])
        if not contracts_d: continue

        ivp_d = schwab_ivp_cache.get(ticker, 50)
        qty_d = qty_cache.get(ticker, 0)
        avg_d = avg_cache.get(ticker, 0)
        earn_date_d = get_earnings_date(ticker)
        if earn_date_d:
            try:
                days_earn = (earn_date_d - datetime.now()).days
                if days_earn < 7: continue  # only skip very close earnings
            except: pass

        # ── CSP: very relaxed ────────────────────────────────
        _min_dte = 35 if spy_regime.get("market_weak") else 20
        puts_30_60 = [c for c in contracts_d
                      if c.get("option_type") == "P"
                      and _min_dte <= (datetime.strptime(c["expiry"],"%Y-%m-%d") - datetime.now()).days <= 60
                      and float(c.get("strike",0) or 0) < price]
        # Precompute stock-level inputs for csp_engine
        _drop_1d = md.get("day_change_pct", 0)
        _ma50_t  = md.get("ma50", price)
        _ma200_t = md.get("ma200", price)
        # 5d drop: use cached trend state (r5 from Yahoo 15d history)
        _trend_cached = trend_state_cache.get(ticker)
        if _trend_cached and _trend_cached.get("r5") is not None:
            _drop_5d = _trend_cached["r5"] / 100  # r5 stored as percentage
        else:
            # Fallback: fetch trend state now and cache it
            _trend_cached = leaps_trend_state(ticker, price)
            trend_state_cache[ticker] = _trend_cached
            _drop_5d = _trend_cached.get("r5", 0) / 100
        # off_low_5d from trend state
        _off_low_5d = _trend_cached.get("off_low_5d", 5.0) if _trend_cached else 5.0

        # ── Per-symbol settings for CSP ──────────────────────
        _sym_s = SYMBOL_SETTINGS.get(ticker, {})
        _buy_under     = _sym_s.get("buy_under", 0)
        _csp_delta_min = _sym_s.get("csp_delta_min", 0)
        _csp_delta_max = _sym_s.get("csp_delta_max", 0)
        if _buy_under > 0:
            print(f"   SYM_SETTINGS {ticker}: buy_under={_buy_under} csp_delta={_csp_delta_min}-{_csp_delta_max}")

        best_csp = None; best_csp_score = -1
        for c in puts_30_60:
            try:
                strike = float(c["strike"])
                dte = (datetime.strptime(c["expiry"],"%Y-%m-%d") - datetime.now()).days
                bid = float(c.get("nbbo_bid",0) or 0)
                ask = float(c.get("nbbo_ask",0) or 0)
                mid = (bid+ask)/2
                if mid < 0.05: continue
                delta = abs(float(c.get("delta",0) or 0))
                if delta == 0: delta = abs(estimate_delta(price,strike,dte,0.30,"P") or 0)
                if not (0.08 <= delta <= 0.40): continue
                otm = (price - strike) / price * 100
                if otm < 2: continue
                if int(c.get("open_interest",0) or 0) < 50: continue
                ann = (mid/strike)*(365/dte)*100
                if ann < 2 or ann > 300: continue

                # Suggested contracts based on tier sizing
                _sizes = {"Core": 40000, "Growth": 25000, "Cyclical": 20000, "Opportunistic": 12500}
                _target_cso = _sizes.get(tier, 12500)
                _contracts = max(1, round(_target_cso / (strike * 100)))

                # Run new CSP engine — passes buy_under + per-symbol delta range
                _pullback = round(pullback_from_high(price, md.get("week52_high", price)) * 100, 1)
                _eng_opp = {
                    "tier": tier, "delta": delta, "dte": dte, "strike": strike,
                    "premium": mid, "ivp": ivp_d,
                    "drop_1d": _drop_1d, "drop_5d": _drop_5d,
                    "off_low_5d": _off_low_5d,
                    "pullback_pct": _pullback,
                    "price": price, "ma50": _ma50_t, "ma200": _ma200_t,
                    "contracts": _contracts,
                    "over_allocation": False,
                    "csp_exposure_pct": 0,
                }
                _result = csp_engine(_eng_opp, spy_day_chg=spy_regime.get("day_change", 0),
                                     buy_under=_buy_under,
                                     csp_delta_min=_csp_delta_min,
                                     csp_delta_max=_csp_delta_max,
                                     ticker=ticker)
                if _result["action"] == "SKIP":
                    print(f"   DBG CSP SKIP {ticker}: dte={dte} d={delta:.2f} flags={_result['flags']} drop1d={_drop_1d:.2%} drop5d={_drop_5d:.2%} yield={_result['yield_30d']:.2f}% contracts={_contracts}")
                    continue
                sort_key = _result["sort_key"]
                if sort_key > best_csp_score:
                    best_csp_score = sort_key
                    best_csp = {
                        "strike": strike, "expiry": c["expiry"], "dte": dte,
                        "bid": round(bid,2), "ask": round(ask,2), "premium": round(mid,2),
                        "delta": round(delta,2), "annualized_return": round(ann,1),
                        "below_min": ann < CSP_MIN_ANNUALIZED, "ivp": ivp_d,
                        "action": _result["action"],
                        "drop_type": _result["drop_type"],
                        "yield_30d": _result["yield_30d"],
                        "csp_flags": _result["flags"],
                        "sort_key": sort_key,
                        "contracts": _contracts,
                        "effective_entry": round(strike - mid, 2),
                        "buy_under": _buy_under if _buy_under > 0 else None,
                    }
            except: continue
        if best_csp:
            w52h = md.get("week52_high",price); w52l = md.get("week52_low",price)
            pullback = round(pullback_from_high(price,w52h)*100,1)
            near_high = price >= w52h*0.92
            below_ma200 = price < md.get("ma200",0)*0.97 if md.get("ma200",0) > 0 else False
            warnings = []
            if near_high: warnings.append("Near 52w high")
            if below_ma200: warnings.append("Below 200MA")
            if md.get("pct_above_ma50",0)*100 > 8: warnings.append(">8% above MA50")
            # Risk level: based on tier and warnings
            if tier == "Core" and not warnings:          risk_level = "Low"
            elif tier in ("Core","Growth") and warnings: risk_level = "Medium"
            elif tier == "Opportunistic":                risk_level = "Elevated"
            else:                                        risk_level = "Medium"
            # Breakeven = strike (for CSP, breakeven = strike - premium)
            breakeven = round(best_csp["strike"] - best_csp["premium"], 2)
            ppd = round(best_csp["premium"] / max(1, best_csp["dte"]), 2)
            csp_entry = {
                "ticker":ticker,"tier":tier,"price":price,"ivp":ivp_d,"mode":"CSP",
                "strike":best_csp["strike"],"expiry":best_csp["expiry"],"dte":best_csp["dte"],
                "premium":best_csp["premium"],"annualized_return":best_csp["annualized_return"],
                "delta":best_csp["delta"],"below_min":best_csp["below_min"],
                "warnings":warnings,"passes_quality":not bool(warnings),
                "risk_level":risk_level,"breakeven":breakeven,"premium_per_day":ppd,
                "pullback_pct":pullback,"above_ma200": not below_ma200,
                "signal":f"{pullback:.0f}% off highs",  # IVP shown in header
                "risk_note":", ".join(warnings) if warnings else None,
            }
            csp_entry["market_weak"]      = spy_regime.get("market_weak", False)
            csp_entry["action"]           = best_csp.get("action", "WATCH")
            csp_entry["drop_type"]        = best_csp.get("drop_type", "")
            csp_entry["yield_30d"]        = best_csp.get("yield_30d", 0)
            csp_entry["csp_flags"]        = best_csp.get("csp_flags", [])
            csp_entry["sort_key"]         = best_csp.get("sort_key", 0)
            csp_entry["contracts"]        = best_csp.get("contracts", 1)
            csp_entry["effective_entry"]  = best_csp.get("effective_entry")   # strike - premium
            csp_entry["buy_under"]        = best_csp.get("buy_under")         # None if no setting
            csp_entry["support_levels"]   = support_cache.get(ticker, {}) or {}
            csp_entry["score"]        = score_csp(csp_entry)  # kept for display score badge
            csp_entry["normalized"]   = normalized_score(csp_entry["score"], "CSP")
            csp_entry["quality_label"] = quality_label(csp_entry["score"], SCORE_MAX["CSP"])
            # Phase 1.6: In-zone determination for UI filter
            _iz, _iz_reason = compute_in_zone(
                "CSP", price, csp_entry.get("buy_under") or 0, 0,
                ivp_d, ivdata.get("iv_current", 0) or 0
            )
            csp_entry["in_zone"]      = _iz
            csp_entry["zone_reason"]  = _iz_reason
            dashboard_csps.append(csp_entry)

        # ── CC: owned positions only ─────────────────────────
        # Delta rules per framework doc:
        #   Normal income:    target 0.20-0.30, hard max 0.35
        #   Overweight pos:   allow up to 0.50 (happy to be called away)
        # Strike: must be above cost basis
        # Phase 1.5: Zone-first — block all CCs (regular + PIO) when stock is
        # in lower half of buy_under/sell_above band. Wait for recovery.
        if qty_d >= 100:
            # Per-symbol settings for CC
            _sym_cc     = SYMBOL_SETTINGS.get(ticker, {})
            _sell_above = _sym_cc.get("sell_above", 0)
            _buy_under_cc = _sym_cc.get("buy_under", 0)
            _cc_dmin    = _sym_cc.get("cc_delta_min", 0)
            _cc_dmax    = _sym_cc.get("cc_delta_max", 0)

            # ── Phase 1.5: Zone-first master gate ─────────────────
            # Compute band midpoint and check if price is in upper half.
            # Applies to BOTH regular CC and PIO below. cc_only tickers
            # (MSTR, OWL) also subject to this — no special exceptions.
            _cc_band_mid = 0
            _cc_zone_blocked = False
            _cc_zone_reason = ""
            if _buy_under_cc > 0 and _sell_above > 0 and _sell_above > _buy_under_cc:
                _cc_band_mid = (_buy_under_cc + _sell_above) / 2
                _cc_band_pos_pct = (price - _buy_under_cc) / (_sell_above - _buy_under_cc) * 100
                if price < _cc_band_mid:
                    _cc_zone_blocked = True
                    _cc_zone_reason = (f"Stock at {_cc_band_pos_pct:.0f}% of band "
                                       f"(${_buy_under_cc:.0f}–${_sell_above:.0f}). "
                                       f"Wait for ${_cc_band_mid:.0f}+ before writing CCs.")
                    print(f"   DBG CC/PIO SKIP {ticker}: ZONE price ${price:.2f} < mid ${_cc_band_mid:.2f}")

            # Determine if overweight to set delta range
            global_pct_d = (qty_d * price / PORTFOLIO_SIZE * 100) if PORTFOLIO_SIZE > 0 else 0
            tier_max = {"Core":10,"Growth":6,"Cyclical":5,"Opportunistic":3}.get(tier,3)
            is_overweight = global_pct_d > tier_max
            # Use per-symbol cc_delta range when available, else fall back to global defaults
            d_min = _cc_dmin if _cc_dmin > 0 else 0.20
            d_max = 0.50 if is_overweight else (_cc_dmax if _cc_dmax > 0 else 0.35)
            d_target = 0.40 if is_overweight else 0.25  # scoring target

            _already_cc  = portfolio_exposure.get("cc_shares_covered",{}).get(ticker, 0)
            _uncovered   = max(0, qty_d - _already_cc)
            if _uncovered < 100:
                pass  # no uncovered shares — skip CC (still show PIO below)
            calls_30_60 = [c for c in contracts_d
                           if c.get("option_type") == "C"
                           and 20 <= (datetime.strptime(c["expiry"],"%Y-%m-%d") - datetime.now()).days <= 60
                           and float(c.get("strike",0) or 0) > price*0.99]
            best_cc = None; best_cc_score = 0
            # Phase 1.5: zone-blocked tickers skip the loop entirely
            if _cc_zone_blocked:
                calls_30_60 = []  # no candidates considered
            for c in calls_30_60:
                try:
                    strike = float(c["strike"])
                    # Phase 1.5: Tightened cost basis protection (was avg_d, now avg_d*1.10).
                    # Prevents selling calls that lock in less than 10% gain if assigned.
                    if avg_d > 0 and strike < avg_d * 1.10: continue
                    # Phase 1.5: Strike must clear buy_under * 1.10 (preserve entry intent)
                    if _buy_under_cc > 0 and strike < _buy_under_cc * 1.10: continue
                    # Phase 1.5: Strike must clear band midpoint (upper-half zone)
                    if _cc_band_mid > 0 and strike < _cc_band_mid: continue
                    dte = (datetime.strptime(c["expiry"],"%Y-%m-%d") - datetime.now()).days
                    bid = float(c.get("nbbo_bid",0) or 0)
                    ask = float(c.get("nbbo_ask",0) or 0)
                    mid = (bid+ask)/2
                    if mid < 0.05: continue
                    delta = abs(float(c.get("delta",0) or 0))
                    if delta == 0: delta = abs(estimate_delta(price,strike,dte,0.30,"C") or 0)
                    # Delta is a FILTER not optimization target — hard max enforced
                    if not (d_min <= delta <= d_max): continue
                    # Sell Above: effective sale (strike + premium) must be >= sell_above.
                    # Prevents locking in a CC that caps gains below our desired exit price.
                    if _sell_above > 0:
                        effective_sale = strike + mid
                        if effective_sale < _sell_above:
                            continue  # hard skip — effective exit below target
                    if int(c.get("open_interest",0) or 0) < 50: continue
                    ann = (mid/strike)*(365/dte)*100
                    if ann < 3 or ann > 300: continue
                    ppd = mid / dte
                    # Canonical CC score — consistent with main scan
                    _opp = {"tier": tier, "delta": delta, "ivp": ivp_d,
                            "annualized_return": ann, "strike": strike,
                            "breakeven": avg_d}
                    score = score_cc(_opp)
                    if score > best_cc_score:
                        best_cc_score = score
                        best_cc = {"strike":strike,"expiry":c["expiry"],"dte":dte,
                                   "bid":round(bid,2),"ask":round(ask,2),"premium":round(mid,2),
                                   "delta":round(delta,2),"annualized_return":round(ann,1),
                                   "avg_cost":round(avg_d,2),"below_min":ann<CC_MIN_ANNUALIZED,"ivp":ivp_d,
                                   "effective_sale": round(strike + mid, 2),
                                   "sell_above": _sell_above if _sell_above > 0 else None}
                except: continue
            if best_cc:
                pnl_pct_cc = (price - avg_d)/avg_d*100 if avg_d > 0 else 0
                pos_status = "Profit" if pnl_pct_cc>5 else "Loss" if pnl_pct_cc<-5 else "Break-even"
                ppd_cc = round(best_cc["premium"] / max(1, best_cc["dte"]), 2)
                ow_warn = ["Overweight — higher delta allowed"] if is_overweight else []
                # ── Phase 1.5: Build reasoning string for dashboard card ──
                _reasoning_parts = []
                if _cc_band_mid > 0:
                    _pos_pct = (price - _buy_under_cc) / (_sell_above - _buy_under_cc) * 100
                    _reasoning_parts.append(
                        f"✅ Zone: stock at {_pos_pct:.0f}% of band (${_buy_under_cc:.0f}–${_sell_above:.0f}), in upper half"
                    )
                if avg_d > 0:
                    _gain_if_called = (best_cc['strike'] + best_cc['premium'] - avg_d) / avg_d * 100
                    _reasoning_parts.append(
                        f"✅ Cost basis ${avg_d:.0f}: strike ${best_cc['strike']:.0f} locks in {_gain_if_called:.0f}% gain if called away"
                    )
                if _sell_above > 0:
                    _eff_sale = best_cc['strike'] + best_cc['premium']
                    _reasoning_parts.append(
                        f"✅ Effective sale ${_eff_sale:.2f} ≥ Sell Above ${_sell_above:.0f}"
                    )
                if ivp_d >= 50:
                    _reasoning_parts.append(f"✅ IVP {ivp_d:.0f}% — premium environment is paying")
                elif ivp_d < 30:
                    _reasoning_parts.append(f"⚠️ IVP {ivp_d:.0f}% — premium is light, modest income")
                _reasoning_parts.append(
                    f"📊 {best_cc['annualized_return']:.0f}% annualized = ${ppd_cc}/day per contract"
                )
                _reasoning = " | ".join(_reasoning_parts)

                cc_entry = {
                    "ticker":ticker,"tier":tier,"price":price,"ivp":ivp_d,"mode":"CC",
                    "strike":best_cc["strike"],"expiry":best_cc["expiry"],"dte":best_cc["dte"],
                    "premium":best_cc["premium"],"annualized_return":best_cc["annualized_return"],
                    "delta":best_cc["delta"],"below_min":best_cc["below_min"],
                    "warnings":ow_warn,"passes_quality":not is_overweight,
                    "risk_level":"Medium" if is_overweight else "Low",
                    "breakeven":round(avg_d,2) if avg_d > 0 else None,
                    "premium_per_day":ppd_cc,"position_status":pos_status,
                    "signal":f"{pos_status} | {'⚠️ Overweight — reduce via CC' if is_overweight else 'Income'} | IVP {ivp_d:.0f}%",
                    "reasoning": _reasoning,
                    "risk_note":"Overweight position — delta up to 0.50 allowed" if is_overweight else None,
                    "effective_sale": best_cc.get("effective_sale"),  # strike + premium
                    "sell_above":     best_cc.get("sell_above"),      # None if no setting
                    "buy_under":      _buy_under_cc if _buy_under_cc > 0 else None,
                    "band_midpoint":  round(_cc_band_mid, 2) if _cc_band_mid > 0 else None,
                }
                cc_entry["day_change_pct"] = md.get("day_change_pct", 0)
                cc_entry["score"] = score_cc(cc_entry)
                cc_entry["normalized"] = normalized_score(cc_entry["score"], "CC")
                cc_entry["quality_label"] = quality_label(cc_entry["score"], SCORE_MAX["CC"])
                # Sizing — spec §5: only uncovered shares count
                _cc_contracts = max(0, int(_uncovered / 100))
                _cc_strike    = best_cc["strike"]
                _nva          = round(_cc_strike * 100 * _cc_contracts, 0)
                _has_cc_open  = any(p["ticker"] == ticker for p in portfolio_exposure.get("cc_positions", []))
                cc_entry["action_label"] = "INCREASE COVERAGE" if _has_cc_open else "SELL CC"
                cc_entry["sizing"] = {
                    "suggested_contracts": _cc_contracts,
                    "capital_required":    0,
                    "premium_received":    round(best_cc["premium"] * 100 * _cc_contracts, 2),
                    "nva":                 _nva,
                    "shares_covered":      _cc_contracts * 100,
                    "already_covered":     int(_already_cc),
                    "total_shares":        int(qty_d),
                    "action_label":        cc_entry["action_label"],
                }
                # Phase 1.6: In-zone determination for UI filter
                _iz_cc, _iz_cc_reason = compute_in_zone(
                    "CC", price, _buy_under_cc or 0, _sell_above or 0,
                    ivp_d, ivdata.get("iv_current", 0) or 0
                )
                cc_entry["in_zone"]     = _iz_cc
                cc_entry["zone_reason"] = _iz_cc_reason
                dashboard_ccs.append(cc_entry)

            # ── PIO: position income ─────────────────────────
            # Phase 1.5: PIO follows same zone-first rules as regular CC.
            # No more aggressive shortcut for "exit-waiting" or loss positions.
            # The reason: writing CCs in lower band = locking in poor exit prices.
            # Phase 1.5: Disabled via ENABLE_PIO flag — only zone-first CCs surface.
            if ENABLE_PIO and avg_d > 0 and not _cc_zone_blocked:
                pnl_pct = (price - avg_d)/avg_d*100
                # PIO delta per framework doc:
                # profit → 0.30-0.40 (more aggressive, happy to reduce)
                # breakeven → 0.20-0.25 (protect position)
                # loss → 0.10-0.15 (very conservative, protect recovery)
                if pnl_pct > 5:      d_min,d_max = 0.25,0.35; pnl_lbl = "📈 Profit"  # in profit — slightly aggressive
                elif pnl_pct > -5:   d_min,d_max = 0.20,0.30; pnl_lbl = "➡️ Break-even"  # protect position
                else:                d_min,d_max = 0.15,0.20; pnl_lbl = "📉 Loss"  # very conservative
                best_pio = None; best_pio_score = 0
                for c in calls_30_60:
                    try:
                        strike = float(c["strike"])
                        # Phase 1.5: Tightened cost basis (was avg_d, now avg_d*1.10)
                        if strike < avg_d * 1.10: continue
                        # Phase 1.5: Strike must clear buy_under * 1.10 if set
                        if _buy_under_cc > 0 and strike < _buy_under_cc * 1.10: continue
                        # Phase 1.5: Strike must clear band midpoint (upper-half zone)
                        if _cc_band_mid > 0 and strike < _cc_band_mid: continue
                        dte = (datetime.strptime(c["expiry"],"%Y-%m-%d") - datetime.now()).days
                        bid = float(c.get("nbbo_bid",0) or 0)
                        ask = float(c.get("nbbo_ask",0) or 0)
                        mid = (bid+ask)/2
                        if mid < 0.10: continue
                        delta = abs(float(c.get("delta",0) or 0))
                        if delta == 0: delta = abs(estimate_delta(price,strike,dte,0.30,"C") or 0)
                        if not (d_min <= delta <= d_max): continue
                        ann = (mid/strike)*(365/dte)*100
                        if ann < 3: continue
                        _s = {"tier": tier, "delta": delta, "ivp": ivp_d,
                              "annualized_return": ann, "strike": strike,
                              "breakeven": avg_d}
                        score = score_cc(_s)
                        if score > best_pio_score:
                            best_pio_score = score
                            best_pio = {"strike":strike,"expiry":c["expiry"],"dte":dte,
                                        "premium":round(mid,2),"delta":round(delta,2),
                                        "annualized_return":round(ann,1),"avg_cost":round(avg_d,2)}
                    except: continue
                if best_pio:
                    # ── Phase 1.5: Build PIO reasoning string ──
                    _ppd_pio = round(best_pio["premium"] / max(1, best_pio["dte"]), 2)
                    _pio_reasoning_parts = []
                    if _cc_band_mid > 0:
                        _pos_pct = (price - _buy_under_cc) / (_sell_above - _buy_under_cc) * 100
                        _pio_reasoning_parts.append(
                            f"✅ Zone: stock at {_pos_pct:.0f}% of band (${_buy_under_cc:.0f}–${_sell_above:.0f})"
                        )
                    _gain_if_called = (best_pio['strike'] + best_pio['premium'] - avg_d) / avg_d * 100
                    _pio_reasoning_parts.append(
                        f"✅ Cost basis ${avg_d:.0f}: strike ${best_pio['strike']:.0f} = {_gain_if_called:.0f}% gain if called"
                    )
                    if _sell_above > 0 and (best_pio['strike'] + best_pio['premium']) >= _sell_above:
                        _pio_reasoning_parts.append(
                            f"✅ Effective sale ${best_pio['strike']+best_pio['premium']:.2f} ≥ Sell Above ${_sell_above:.0f}"
                        )
                    _pio_reasoning_parts.append(f"📊 {pnl_lbl} position — {pnl_pct:+.0f}% from cost")
                    _pio_reasoning_parts.append(
                        f"📊 {best_pio['annualized_return']:.0f}% annualized = ${_ppd_pio}/day per contract"
                    )
                    if ivp_d < 30:
                        _pio_reasoning_parts.append(f"⚠️ IVP {ivp_d:.0f}% — light premium, modest income")
                    _pio_reasoning = " | ".join(_pio_reasoning_parts)

                    dashboard_ccs.append({
                        "ticker":ticker,"tier":tier,"price":price,"ivp":ivp_d,"mode":"PIO",
                        "strike":best_pio["strike"],"expiry":best_pio["expiry"],"dte":best_pio["dte"],
                        "premium":best_pio["premium"],"annualized_return":best_pio["annualized_return"],
                        "delta":best_pio["delta"],"below_min":best_pio["annualized_return"]<8,
                        "warnings":[],"passes_quality":True,
                        "signal":f"{pnl_lbl} | Above cost basis ${avg_d:.0f} | IVP {ivp_d:.0f}%",
                        "reasoning": _pio_reasoning,
                        "risk_note":None,
                        "buy_under": _buy_under_cc if _buy_under_cc > 0 else None,
                        "sell_above": _sell_above if _sell_above > 0 else None,
                        "band_midpoint": round(_cc_band_mid, 2) if _cc_band_mid > 0 else None,
                    })
            elif ENABLE_PIO and avg_d > 0 and _cc_zone_blocked:
                # Log why PIO was blocked (visible in scanner output)
                print(f"   DBG PIO BLOCKED {ticker}: {_cc_zone_reason}")

        # ── LEAPS: all with decent timing ────────────────────
        if ticker not in LEAPS_ONLY:
            _sym_leaps       = SYMBOL_SETTINGS.get(ticker, {})
            _leaps_buy_under = _sym_leaps.get("buy_under", 0)
            _leaps_delta_min = _sym_leaps.get("leaps_delta_min", LEAPS_DELTA_MIN)
            _leaps_delta_max = _sym_leaps.get("leaps_delta_max", LEAPS_DELTA_MAX)
            leaps_calls = [c for c in contracts_d
                           if c.get("option_type") == "C"
                           and (datetime.strptime(c["expiry"],"%Y-%m-%d") - datetime.now()).days >= LEAPS_DTE_MIN]
            if ticker in ("TSLA", "NVDA", "AAPL"):
                all_calls = [c for c in contracts_d if c.get("option_type") == "C"]
                print(f"   DBG LEAPS {ticker}: contracts_d={len(contracts_d)} calls={len(all_calls)} leaps_calls(DTE>={LEAPS_DTE_MIN})={len(leaps_calls)}")
                if leaps_calls:
                    sample = leaps_calls[0]
                    print(f"     sample: strike={sample.get('strike')} expiry={sample.get('expiry')} delta={sample.get('delta')} mid={(float(sample.get('nbbo_bid',0))+float(sample.get('nbbo_ask',0)))/2:.2f}")
            # ── 3-band LEAPS selection ──────────────────────────────
            # Conservative (δ 0.89–0.92): core stock replacement, lower risk
            # Sweet spot   (δ 0.85–0.88): best balance for most positions
            # Aggressive   (δ 0.80–0.84): more leverage, more extrinsic
            # Each band picks the contract with lowest extrinsic% within the band.
            _bands = {
                "conservative": {"d_lo": 0.90, "d_hi": 0.99, "label": "🛡️ Conservative",
                                 "note": "Very deep ITM · near stock-like · minimal decay"},
                "sweet_spot":   {"d_lo": 0.83, "d_hi": 0.899,"label": "🎯 Sweet spot",
                                 "note": "Best balance · high delta · capital efficient"},
                "aggressive":   {"d_lo": 0.75, "d_hi": 0.829,"label": "⚡ More leverage",
                                 "note": "More OTM · more extrinsic · higher leverage"},
            }
            _band_best = {b: None for b in _bands}  # best contract per band (lowest ext%)

            # Determine recommendation: Core → Conservative by default
            # Shift toward aggressive when price is near/below buy_under
            _near_buy_l = _leaps_buy_under > 0 and price <= _leaps_buy_under * 1.05
            _at_buy_l   = _leaps_buy_under > 0 and price <= _leaps_buy_under
            if _at_buy_l:
                _recommended_band = "aggressive"   # at/below target → cheapest entry
            elif _near_buy_l:
                _recommended_band = "sweet_spot"   # near target → good balance
            elif tier == "Core":
                _recommended_band = "conservative" # Core → safer stock replacement
            else:
                _recommended_band = "sweet_spot"   # Growth/Trading → sweet spot

            for c in leaps_calls:
                try:
                    strike = float(c["strike"])
                    dte = (datetime.strptime(c["expiry"],"%Y-%m-%d") - datetime.now()).days
                    bid = float(c.get("nbbo_bid",0) or 0)
                    ask = float(c.get("nbbo_ask",0) or 0)
                    mid = (bid+ask)/2
                    if mid < 5: continue
                    itm_pct = (price-strike)/price*100
                    if not (-5 <= itm_pct <= 70): continue
                    delta = abs(float(c.get("delta",0) or 0))
                    if delta == 0: delta = abs(estimate_delta(price,strike,dte,0.30,"C") or 0)
                    if not (delta >= 0.75): continue  # floor only — no upper cap
                    intrinsic = max(0, price-strike)
                    extrinsic = max(0, mid-intrinsic)
                    ext_pct = (extrinsic/mid*100) if mid > 0 else 100
                    if ext_pct > 40: continue
                    breakeven = round(strike + mid, 2)
                    be_pct    = round((breakeven - price) / price * 100, 1) if price > 0 else 0
                    if ext_pct < 10:   ext_lbl = f"🔥 Excellent ({ext_pct:.1f}%)"
                    elif ext_pct < 15: ext_lbl = f"✅ Good ({ext_pct:.1f}%)"
                    elif ext_pct < 20: ext_lbl = f"⚠️ Acceptable ({ext_pct:.1f}%)"
                    elif ext_pct < 25: ext_lbl = f"🔶 Expensive ({ext_pct:.1f}%)"
                    else:              ext_lbl = f"❌ Very expensive ({ext_pct:.1f}%)"
                    candidate = {"strike":strike,"expiry":c["expiry"],"dte":dte,
                                 "premium":round(mid,2),"delta":round(delta,2),
                                 "extrinsic_pct":round(ext_pct,1),"ext_label":ext_lbl,
                                 "itm_pct":round(itm_pct,1),"ivp":ivp_d,
                                 "breakeven":breakeven,"be_pct":be_pct}
                    # File into matching band (lowest ext% wins within band)
                    for _bname, _bdef in _bands.items():
                        if _bdef["d_lo"] <= delta <= _bdef["d_hi"]:
                            prev = _band_best[_bname]
                            if prev is None:
                                _band_best[_bname] = candidate
                            else:
                                # Prefer longer DTE unless shorter saves >3% extrinsic
                                # This avoids picking Sep 2027 over Jan 2028 for trivial savings
                                _ext_saving = prev["extrinsic_pct"] - ext_pct
                                _dte_gain   = prev["dte"] - dte  # positive = prev is longer
                                if _dte_gain > 30 and _ext_saving < 3.0:
                                    pass  # keep prev (longer expiry, not worth the small saving)
                                elif ext_pct < prev["extrinsic_pct"]:
                                    _band_best[_bname] = candidate
                            break
                except: continue

            # Build one leaps_entry per band that found a contract
            _trend_computed = False
            _trend_state_v = "UNKNOWN"; _trend_label_v = "WATCH"
            _trend_signal_v = ""; _trend_action_v = "WATCH"
            _w52h = md.get("week52_high",price)
            _pullback = round(pullback_from_high(price,_w52h)*100,1)

            for _bname, _bdef in _bands.items():
                _bl = _band_best[_bname]
                if not _bl: continue
                ext_pct = _bl["extrinsic_pct"]
                warnings = []
                if ext_pct > 20: warnings.append(f"High extrinsic {ext_pct:.1f}%")
                if ivp_d > 50:   warnings.append(f"IVP elevated {ivp_d:.0f}%")
                _is_rec = (_bname == _recommended_band)
                leaps_entry = {
                    "ticker":       ticker, "tier": tier, "price": price, "ivp": ivp_d,
                    "mode":         "LEAPS",
                    "leaps_band":   _bname,
                    "leaps_label":  _bdef["label"],
                    "leaps_note":   (
                        f"🔥 Excellent cost — {ext_pct:.1f}% extrinsic. Prioritize this." if ext_pct < 20
                        else f"⚠️ Extrinsic {ext_pct:.1f}% — getting expensive. Compare vs lower-strike bands."  if ext_pct < 30
                        else f"❌ Extrinsic {ext_pct:.1f}% — expensive. Check if a cheaper band exists for this ticker."  if ext_pct < 40
                        else f"🚫 Extrinsic {ext_pct:.1f}% — avoid. IV too high or strike too close to ATM."
                    ),
                    "is_recommended": _is_rec,
                    "strike":       _bl["strike"], "expiry": _bl["expiry"], "dte": _bl["dte"],
                    "premium":      _bl["premium"], "annualized_return": 0,
                    "delta":        _bl["delta"], "extrinsic_pct": ext_pct,
                    "below_min":    ext_pct > 20, "warnings": warnings,
                    "passes_quality": ext_pct <= 20 and ivp_d <= 50,
                    "breakeven":    _bl["breakeven"],
                    "breakeven_pct": _bl["be_pct"],
                    "signal":       f"{_bl['ext_label']} | {_pullback:.0f}% off highs",
                    "risk_note":    ", ".join(warnings) if warnings else None,
                    "buy_under":    _leaps_buy_under if _leaps_buy_under > 0 else None,
                    "days_to_earnings": (lambda _ed: max(0, (_ed - datetime.now()).days) if _ed else 999)(earn_date),
                }
                leaps_entry["score"] = score_leaps(leaps_entry)
                leaps_entry["normalized"] = normalized_score(leaps_entry["score"], "LEAPS")
                leaps_entry["quality_label"] = quality_label(leaps_entry["score"], SCORE_MAX["LEAPS"])
                leaps_entry["support_levels"] = support_cache.get(ticker, {}) or {}
                # Compute trend once per ticker — share across bands
                if not _trend_computed:
                    try:
                        _trend_d  = leaps_trend_state(ticker, price)
                        _ta_d     = leaps_trend_action(_trend_d, ivp_d, price, _w52h)
                        _trend_state_v  = str(_trend_d.get("state","UNKNOWN"))
                        _trend_label_v  = str(_ta_d.get("label","WATCH"))
                        _trend_signal_v = str(_ta_d.get("signal",""))
                        _trend_action_v = str(_ta_d.get("action","WATCH"))
                        print(f"   LEAPS trend {ticker}: {_trend_label_v}")
                    except Exception as _te:
                        print(f"   LEAPS trend error {ticker}: {_te}")
                    _trend_computed = True
                leaps_entry["trend_state"]  = _trend_state_v
                leaps_entry["trend_label"]  = _trend_label_v
                leaps_entry["trend_signal"] = _trend_signal_v
                leaps_entry["trend_action"] = _trend_action_v
                # Phase 1.6: In-zone determination for UI filter
                _iz_l, _iz_l_reason = compute_in_zone(
                    "LEAPS", price, _leaps_buy_under or 0, 0,
                    ivp_d, ivdata.get("iv_current", 0) or 0
                )
                leaps_entry["in_zone"]     = _iz_l
                leaps_entry["zone_reason"] = _iz_l_reason
                dashboard_leaps.append(leaps_entry)
                print(f"   LEAPS [{_bdef['label']}] {ticker} ${_bl['strike']} δ{_bl['delta']} ext{ext_pct:.1f}% BE${_bl['breakeven']} {'★REC' if _is_rec else ''}")

        # ── Cheap Convexity LEAPS: far-OTM long-dated calls ──────
        _convex_contracts = convex_cache.get(ticker, [])
        if _convex_contracts:
            try:
                _cvx_results = scan_convexity(ticker, price, _convex_contracts, ivp_d)
                for _cv in _cvx_results:
                    _cv["tier"] = tier
                    _cv["support_levels"] = support_cache.get(ticker, {}) or {}
                    _cv["days_to_earnings"] = (lambda _ed: max(0, (_ed - datetime.now()).days) if _ed else 999)(earn_date_d)
                    # In-zone: convexity entry favored when stock near/below buy_under (cheaper premium)
                    _cv_bu = SYMBOL_SETTINGS.get(ticker, {}).get("buy_under", 0)
                    _iz_cv, _iz_cv_reason = compute_in_zone(
                        "LEAPS", price, _cv_bu or 0, 0, ivp_d, ivdata.get("iv_current", 0) or 0)
                    _cv["in_zone"]     = _iz_cv
                    _cv["zone_reason"] = _iz_cv_reason
                    _cv["buy_under"]   = _cv_bu if _cv_bu > 0 else None
                    dashboard_convexity.append(_cv)
                    _tag = "NEAR-MISS" if _cv.get("is_nearmiss") else _cv['class_label']
                    print(f"   CVX [{_tag}] {ticker} ${_cv['strike']} CAGR{_cv['required_cagr']:.0f}% conv{_cv['convexity_score']:.0f}x prem{_cv['premium_pct']:.0f}% cov30={_cv['cov30']} {'★REC' if _cv.get('is_recommended') else ''}")
            except Exception as _cve:
                print(f"   CVX error {ticker}: {_cve}")

    # Apply unified score for cross-strategy normalization (patch 5)
    for o in dashboard_csps:
        o["unified_score"] = score_unified(o, "CSP")
    for o in dashboard_ccs:
        o["unified_score"] = score_unified(o, o.get("mode","CC"))
    for o in dashboard_leaps:
        o["unified_score"] = score_unified(o, "LEAPS")
    # Convexity: ranking already applied in scan_convexity (passers ranked per spec,
    # then near-misses). Preserve that order; passers always ahead of near-misses.
    # unified_score is only for any cross-strategy views — use inverse CAGR as proxy.
    for o in dashboard_convexity:
        o["unified_score"] = (0 if o.get("is_nearmiss") else 100) - o.get("required_cagr", 99)
    dashboard_convexity.sort(key=lambda x: (x.get("is_nearmiss", False),
                                            x.get("required_cagr", 99),
                                            -x.get("cov30", 0)))

    # ── Cheap Convexity → Telegram (Grade A only) ──────────────
    # Dashboard is authoritative; Telegram is derived. Convexity is built after
    # the main Telegram block, so it sends here. Only Grade A (Excellent) passers
    # alert — these are rare by design, so this won't flood.
    tg_convex = [o for o in dashboard_convexity
                 if o.get("classification") == "A" and not o.get("is_nearmiss")]
    if STRICT_ZONE_TELEGRAM and tg_convex:
        _pre = len(tg_convex)
        _kept = []
        for o in tg_convex:
            _bu = SYMBOL_SETTINGS.get(o.get("ticker",""), {}).get("buy_under", 0) or 0
            _iz, _rsn = compute_in_zone("LEAPS", o.get("price",0) or 0, _bu, 0,
                                        o.get("ivp",0) or 0, 0)
            if _iz:
                _kept.append(o)
            else:
                print(f"   📵 TG SUPPRESS {o.get('ticker','')} CONVEXITY: {_rsn}")
        tg_convex = _kept
        print(f"   📵 STRICT_ZONE_TELEGRAM convexity: {_pre}→{len(tg_convex)}")
    if tg_convex:
        print(f"   📱 Sending {len(tg_convex)} Grade-A convexity alert(s)...")
        send_telegram("━━━ *🎲 CHEAP CONVEXITY (Grade A)* ━━━"); time.sleep(1)
        for o in tg_convex:
            send_telegram(fmt_convex(o)); time.sleep(2)
    else:
        print(f"   🎲 Convexity: no Grade-A passers → no Telegram")

    # Sort by canonical score descending — never by annualized return
    dashboard_csps = csp_promote_best(dashboard_csps)
    dashboard_csps.sort(key=lambda x: x.get("sort_key", x.get("score",0)), reverse=True)
    dashboard_ccs.sort(key=lambda x: x.get("score", 0), reverse=True)
    dashboard_leaps.sort(key=lambda x: x.get("score", 0), reverse=True)
    pio_count = sum(1 for o in dashboard_ccs if o.get("mode") == "PIO")
    cc_count  = sum(1 for o in dashboard_ccs if o.get("mode") == "CC")
    print(f"   📊 Dashboard: {len(dashboard_csps)} CSPs | {cc_count} CCs | {pio_count} PIOs | {len(dashboard_leaps)} LEAPS | {len(dashboard_convexity)} CVX")

    all_opps = []
    for o in top_csps:   all_opps.append(opp_to_dict(o, "csp"))
    for o in top_ccs:    all_opps.append(opp_to_dict(o, "cc"))
    for o in top_leaps:  all_opps.append(opp_to_dict(o, "leaps"))
    for o in top_pmccs:  all_opps.append(opp_to_dict(o, "pmcc"))
    for o in spike_opps:
        s = o.get("spike_cc", {})
        all_opps.append({
            "ticker": o.get("ticker",""), "tier": o.get("tier",""),
            "price": o.get("price",0), "ivp": round(o.get("ivp",0),1),
            "mode": "SPIKE_CC", "strike": s.get("strike",0),
            "expiry": s.get("expiry",""), "dte": s.get("dte",0),
            "premium": s.get("premium",0),
            "annualized_return": s.get("annualized_return",0),
            "delta": s.get("delta",0), "signal": "", "below_min": False, "risk_note": None,
        })
    for o in drop_opps:
        s = o.get("drop_csp", {})
        all_opps.append({
            "ticker": o.get("ticker",""), "tier": o.get("tier",""),
            "price": o.get("price",0), "ivp": round(o.get("ivp",0),1),
            "mode": "DROP_CSP", "strike": s.get("strike",0),
            "expiry": s.get("expiry",""), "dte": s.get("dte",0),
            "premium": s.get("premium",0),
            "annualized_return": s.get("annualized_return",0),
            "delta": s.get("delta",0),
            "signal": s.get("timing",{}).get("signal","") or "",
            "below_min": s.get("below_min", False), "risk_note": "60% normal size",
        })
    for o in pio_opps:
        s = o.get("pio_cc", {})
        all_opps.append({
            "ticker": o.get("ticker",""), "tier": o.get("tier",""),
            "price": o.get("price",0), "ivp": round(o.get("ivp",0),1),
            "mode": "PIO", "strike": s.get("strike",0),
            "expiry": s.get("expiry",""), "dte": s.get("dte",0),
            "premium": s.get("premium",0),
            "annualized_return": s.get("annualized_return",0),
            "delta": s.get("delta",0), "signal": o.get("pnl_status",""),
            "below_min": False, "risk_note": None,
        })

    # ── Build allocation dashboard (Positions tab) ─────────
    # Spec: strict rules for ownership, exclusions, grouping, action logic

    # -- Build exposure map: combined IBKR + Schwab --
    exposure_map  = {}
    mv_map_total  = {}
    mv_map_acct   = {}
    for ticker, pos in ibkr.items():
        if pos.get("asset_class") == "STK":
            mv = float(pos.get("market_value", 0) or 0)
            if mv > 0 and PORTFOLIO_SIZE > 0:
                t = ticker.replace("BRK B","BRK-B").strip()
                _exp = round(mv / PORTFOLIO_SIZE * 100, 2)
                exposure_map[t] = max(_exp, 0.01)  # min 0.01% so small positions show as Owned
                mv_map_total[t] = mv
                # Build per-account MV: start from Schwab accounts, then add IBKR remainder
                _schwab_by_acct = dict(schwab_mv_by_acct[t]) if t in schwab_mv_by_acct else {}
                _schwab_total   = sum(_schwab_by_acct.values())
                _ibkr_mv        = round(mv - _schwab_total, 0)
                if _ibkr_mv > 50:  # meaningful IBKR position (>$50 to avoid rounding noise)
                    _schwab_by_acct["IBKR"] = _ibkr_mv
                mv_map_acct[t] = _schwab_by_acct if _schwab_by_acct else {"IBKR": mv}

    # Debug: print exposure_map to verify all tickers captured
    print(f"   exposure_map tickers ({len(exposure_map)}): {sorted(exposure_map.keys())}")

    # -- Build account_map: IBKR first, then Schwab overrides --
    # IBKR stocks default to "IBKR". schwab_account_map overrides with IRA/CRT/Personal.
    # Must be built BEFORE the grouped-ticker merge below which references it.
    account_map      = {}  # ticker -> primary account label
    account_map_all  = {}  # ticker -> list of ALL accounts (for multi-account filtering)
    for _t in ibkr:
        if ibkr[_t].get("asset_class") == "STK":
            _tk = _t.replace("BRK B","BRK-B").strip()
            account_map[_tk]     = "IBKR"
            account_map_all[_tk] = ["IBKR"]
    # Schwab overrides — primary label is largest holding account
    account_map.update(schwab_account_map)
    # All accounts per ticker from Schwab
    for _t, _by in schwab_mv_by_acct.items():
        account_map_all[_t] = list(_by.keys())
    # Add IBKR to account_map_all for tickers with IBKR shares (detected via mv_map_acct)
    for _t, _by_acct in mv_map_acct.items():
        if "IBKR" in _by_acct and _t in account_map_all and "IBKR" not in account_map_all[_t]:
            account_map_all[_t].append("IBKR")
        elif "IBKR" in _by_acct and _t not in account_map_all:
            account_map_all[_t] = ["IBKR"]
    # Option-only tickers (e.g. MSFT puts in IRA but no MSFT shares)
    for _optpos in (portfolio_exposure.get("csp_positions", []) +
                    portfolio_exposure.get("cc_positions", []) +
                    portfolio_exposure.get("leaps_positions", [])):
        _tk = _optpos.get("ticker", "")
        if _tk and _tk not in account_map:
            for _sym, _ipos in ibkr.items():
                if (_ipos.get("asset_class") == "OPT" and
                        _ipos.get("underlying","").upper() == _tk.upper()):
                    _a = _ipos.get("account_type", "") or ""
                    if _a:
                        account_map[_tk] = _a
                        break
            if _tk not in account_map:
                account_map[_tk] = "IBKR"

    # ── Apply grouped ticker rule (Spec §6) ──
    # GOOG + GOOGL → combined exposure under GOOGL
    for alias, canonical in GROUPED_TICKERS.items():
        if alias in exposure_map:
            exposure_map[canonical] = round(
                exposure_map.get(canonical, 0) + exposure_map.pop(alias), 1)
        # Also merge account_map so IBKR GOOG shares appear under GOOGL filter
        if alias in account_map:
            alias_accts  = account_map_all.get(alias, [account_map[alias]])
            canon_accts  = account_map_all.get(canonical, [])
            merged       = list(dict.fromkeys(canon_accts + alias_accts))
            account_map_all[canonical] = merged
            if canonical not in account_map:
                account_map[canonical] = account_map[alias]
            account_map.pop(alias, None)
            account_map_all.pop(alias, None)

    # ── Exclusion rule (Spec §4) ──
    for sym in list(exposure_map.keys()):
        if sym in EXCLUDED_SYMBOLS:
            exposure_map.pop(sym)


    # ── Ownership precedence rule (Spec §5) ──
    # owned_tickers includes ALL stocks with market value (universe + non-universe)
    owned_tickers    = set(exposure_map.keys())
    watchlist_tickers = set(ALL_TICKERS) - EXCLUDED_SYMBOLS - set(GROUPED_TICKERS.keys())
    # Tickers with options but no stock position
    option_only_tickers = set(account_map.keys()) - owned_tickers - EXCLUDED_SYMBOLS
    all_allocation_tickers = owned_tickers | watchlist_tickers | option_only_tickers
    print(f"   📋 Allocation: {len(owned_tickers)} owned, {len(watchlist_tickers)} watchlist, {len(EXCLUDED_SYMBOLS)} excluded")
    # Debug specific tickers
    for _dbg in ["TSLA","MSTR","IBIT","OWL","NLCP","MU","CLS","CRDO","LULU","NBIS","MELI","PLTR","TSM","FIX","IBKR"]:
        _dbg_pos = ibkr.get(_dbg, {})
        print(f"   DEBUG {_dbg}: asset={_dbg_pos.get('asset_class','NOT IN IBKR')} mv={_dbg_pos.get('market_value',0):.0f} acct={_dbg_pos.get('account_type','')} in_exposure={_dbg in exposure_map} in_account_map={_dbg in account_map}")
    # Account breakdown from account_map (authoritative)
    _acct_debug = {}
    for _lbl in account_map.values():
        _acct_debug[_lbl] = _acct_debug.get(_lbl, 0) + 1
    print(f"   Account breakdown: {_acct_debug}")

    def canonical_action(pos_status: str, price_opp: str) -> str:
        """
        Spec §10: Strict mapping — no contradictory signals.
        Below target + Pullback  → BUY
        Below target + Neutral   → ADD
        Below target + Near High → HOLD
        On target + Pullback     → ADD
        On target + Neutral      → HOLD
        On target + Near High    → TRIM
        Above target + any       → TRIM
        Not Held (speculative)   → WATCH (no automatic buy pressure)
        """
        if pos_status == "Not Held":
            return "WATCH"
        elif pos_status == "Overweight":
            return "TRIM"
        elif pos_status == "On Target":
            if price_opp == "Pullback":   return "ADD"
            elif price_opp == "Neutral":  return "HOLD"
            else:                         return "TRIM"   # Near High
        else:  # Underweight
            if price_opp == "Pullback":   return "BUY"
            elif price_opp == "Neutral":  return "ADD"
            else:                         return "HOLD"   # Near High — never BUY

    pos_list = []
    for ticker in all_allocation_tickers:
        # Skip explicitly excluded symbols (Spec §4)
        if ticker in EXCLUDED_SYMBOLS: continue
        # Skip duplicate alias symbols (Spec §6)
        if ticker in GROUPED_TICKERS:  continue

        tier = ("Core" if ticker in CORE_STOCKS else
                "Growth" if ticker in GROWTH_STOCKS else
                "Cyclical" if ticker in CYCLICAL_STOCKS else
                "Opportunistic" if ticker in OPPORTUNISTIC_STOCKS else "Other")

        target_low, target_high = ticker_target_range(ticker, tier)
        exposure = exposure_map.get(ticker, 0.0)

        # Combined exposure: stock + LEAPS market value + CSP obligation (if assigned)
        _leaps_mv_ticker = sum(
            p.get("market_value", 0) or p.get("avg_cost", 0) * p.get("contracts", 0) * 100
            for p in portfolio_exposure.get("leaps_positions", []) if p.get("ticker") == ticker
        )
        _stock_mv_ticker = mv_map_total.get(ticker, 0)
        _csp_mv_ticker   = sum(p.get("cso", 0) for p in portfolio_exposure.get("csp_positions", []) if p.get("ticker") == ticker)
        # Use CSP obligation only when no stock owned (shows what's at risk if assigned)
        _csp_contrib     = _csp_mv_ticker if _stock_mv_ticker == 0 else 0
        combined_exposure = round((_stock_mv_ticker + _leaps_mv_ticker + _csp_contrib) / PORTFOLIO_SIZE * 100, 2) if PORTFOLIO_SIZE > 0 else exposure

        # Ownership precedence (Spec §5): any exposure > 0 = Owned
        # Also owned if has open CSP, CC, or LEAPS contracts (even with no stock)
        _has_open_options = (
            any(p.get("ticker") == ticker for p in portfolio_exposure.get("csp_positions", [])) or
            any(p.get("ticker") == ticker for p in portfolio_exposure.get("cc_positions", [])) or
            any(p.get("ticker") == ticker for p in portfolio_exposure.get("leaps_positions", []))
        )
        status = "Owned" if exposure > 0 or _leaps_mv_ticker > 0 or _has_open_options else "Watchlist"
        pos_status = ticker_position_status(ticker, tier, combined_exposure)

        # Price opportunity
        md_t = mkt.get(ticker, {})
        price_t = md_t.get("price", 0)
        w52h_t  = md_t.get("week52_high", price_t * 1.3)
        pullback_t = pullback_from_high(price_t, w52h_t) if price_t > 0 else 0
        if pullback_t > 0.20:    price_opp = "Pullback"
        elif pullback_t > 0.08:  price_opp = "Neutral"
        else:                    price_opp = "Near High"

        # Single canonical decision (spec: one output only)
        action = canonical_action(pos_status, price_opp)

        # Score for sorting
        pos_row = {"tier": tier, "pos_status": pos_status,
                   "price_opp": price_opp, "above_ma200": md_t.get("above_ma200", True)}
        score = score_allocation(pos_row)

        # ── CC/CSP exposure for this ticker from portfolio_exposure ──
        _stk_pos       = ibkr.get(ticker, {})
        _shares_owned  = int(float(_stk_pos.get("quantity", 0) or _stk_pos.get("qty", 0) or 0))
        _price_t       = md_t.get("price", 0)
        # CC data
        _cc_covered      = portfolio_exposure.get("cc_shares_covered", {}).get(ticker, 0)
        _cc_contracts    = sum(p["contracts"] for p in portfolio_exposure.get("cc_positions", []) if p["ticker"] == ticker)
        _leaps_contracts = sum(p["contracts"] for p in portfolio_exposure.get("leaps_positions", []) if p["ticker"] == ticker)
        _leaps_coverage  = _leaps_contracts * 100  # each LEAPS contract covers 100 shares
        _effective_shares= _shares_owned + _leaps_coverage
        _uncovered       = max(0, _effective_shares - _cc_covered)
        _cov_pct         = round(_cc_covered / _effective_shares * 100, 1) if _effective_shares > 0 else 0
        _add_cc          = int(_uncovered / 100)
        # CSP data
        _csp_contracts = sum(p["contracts"] for p in portfolio_exposure.get("csp_positions", []) if p["ticker"] == ticker)
        _csp_obligation= sum(p["cso"]       for p in portfolio_exposure.get("csp_positions", []) if p["ticker"] == ticker)
        # Account source
        # Use account_map which covers both stock and option-only positions
        _raw_acct = _stk_pos.get("account_type", "") or ""
        # All accounts this ticker appears in (for multi-account filtering)
        _acct_primary = account_map.get(ticker, _raw_acct if _raw_acct else "IBKR") or "IBKR"
        _acct_all     = account_map_all.get(ticker, [_acct_primary])
        _account      = _acct_primary
        # Status label
        _has_stock = _shares_owned > 0
        _has_cc    = _cc_contracts > 0
        _has_csp   = _csp_contracts > 0
        if _has_stock and _has_cc and _has_csp:   _exp_status = "Stock + CC + CSP"
        elif _has_stock and _has_cc:
            if _cov_pct >= 100:                   _exp_status = "Fully Covered"
            elif _cov_pct >= 50:                  _exp_status = f"{round(_cov_pct/25)*25}% Covered"
            else:                                 _exp_status = f"{int(_cov_pct)}% Covered"
        elif _has_stock and _has_csp:             _exp_status = "Stock + CSP"
        elif _has_stock:                          _exp_status = "Stock Only"
        elif _has_csp:                            _exp_status = "CSP Only"
        else:                                     _exp_status = "Watchlist"

        pos_list.append({
            "ticker":          ticker,
            "tier":            tier,
            "status":          status,
            "exposure_pct":    combined_exposure,  # stock + LEAPS MV / portfolio
            "target_range":    f"{target_low:.1f}–{target_high:.1f}%",
            "pos_status":      pos_status,
            "price_opp":       price_opp,
            "action":          action,
            "score":           score,
            "pullback_pct":    round(pullback_t * 100, 1),
            "above_ma200":     md_t.get("above_ma200", True),
            # Exposure fields (spec §3, §4)
            "shares_owned":    _shares_owned,
            "cc_contracts":    _cc_contracts,
            "shares_covered":  _cc_covered,
            "coverage_pct":    _cov_pct,
            "uncovered_shares":_uncovered,
            "add_cc_contracts":_add_cc,
            "csp_contracts":   _csp_contracts,
            "csp_obligation":  _csp_obligation,
            "exp_status":      _exp_status,
            "account":         _account,
            "accounts_all":    _acct_all,
            # Market value: total and per-account
            "market_value":    round(mv_map_total.get(ticker, 0), 0),
            "mv_by_account":   mv_map_acct.get(ticker, {}),
            # LEAPS positions for this ticker
            "leaps":           [p for p in portfolio_exposure.get("leaps_positions",[]) if p["ticker"]==ticker],
            # Combined exposure including LEAPS market value
            "leaps_mv":        round(sum(p.get("market_value",0) or p.get("avg_cost",0)*p.get("contracts",0)*100
                                         for p in portfolio_exposure.get("leaps_positions",[]) if p["ticker"]==ticker), 0),
            "combined_exposure_pct": round((mv_map_total.get(ticker,0) +
                                             sum(p.get("market_value",0) or p.get("avg_cost",0)*p.get("contracts",0)*100
                                                 for p in portfolio_exposure.get("leaps_positions",[]) if p["ticker"]==ticker))
                                            / PORTFOLIO_SIZE * 100, 2) if PORTFOLIO_SIZE > 0 else exposure,
            # BCS positions for this ticker (long legs only for display)
            "bcs":             [p for p in portfolio_exposure.get("bcs_positions",[]) if p["ticker"]==ticker],
        })

    # Sort by score descending — BUY at top, TRIM at bottom
    pos_list.sort(key=lambda x: x["score"], reverse=True)
    # Validate: no row may have sizing_action different from action
    for _p in pos_list:
        assert "sizing_action" not in _p, f"FATAL: sizing_action found in {_p['ticker']} — dual signal violation"
    print(f"   ✅ Decision system: single action field only, no dual signals")
    print(f"   📋 Positions tab: {len(pos_list)} rows ({sum(1 for p in pos_list if p['status']=='Owned')} owned, {sum(1 for p in pos_list if p['status']=='Watchlist')} watchlist)")


    # Add spike and drop opps to dashboard
    dash_spikes = []
    for o in top_spikes:
        s = o.get("spike_cc", {})
        if not s: continue
        ppd = round(s.get("premium",0) / max(1, s.get("dte",1)), 2)
        dash_spikes.append({
            "ticker": o.get("ticker",""), "tier": o.get("tier",""),
            "price": o.get("price",0), "ivp": round(o.get("ivp",0),1),
            "mode": "SPIKE_CC",
            "strike": s.get("strike",0), "expiry": s.get("expiry",""),
            "dte": s.get("dte",0), "premium": s.get("premium",0),
            "annualized_return": s.get("annualized_return",0),
            "delta": s.get("delta",0),
            "below_min": False, "warnings": [], "passes_quality": True,
            "risk_level": "Medium",
            "breakeven": None, "premium_per_day": ppd,
            "signal": s.get("timing",{}).get("signal","") or f"IVP {o.get('ivp',0):.0f}% spike",
            "risk_note": "⚠️ Exit at 50-70% profit. Close early if stock reverses.",
        })

    dash_drops = []
    for o in top_drops:
        s = o.get("drop_csp", {})
        if not s: continue
        ppd = round(s.get("premium",0) / max(1, s.get("dte",1)), 2)
        breakeven = round(s.get("strike",0) - s.get("premium",0), 2)
        dash_drops.append({
            "ticker": o.get("ticker",""), "tier": o.get("tier",""),
            "price": o.get("price",0), "ivp": round(o.get("ivp",0),1),
            "mode": "DROP_CSP",
            "strike": s.get("strike",0), "expiry": s.get("expiry",""),
            "dte": s.get("dte",0), "premium": s.get("premium",0),
            "annualized_return": s.get("annualized_return",0),
            "delta": s.get("delta",0),
            "below_min": False, "warnings": [], "passes_quality": True,
            "risk_level": "Elevated",
            "breakeven": breakeven, "premium_per_day": ppd,
            "signal": s.get("timing",{}).get("signal","") or f"Post-drop | IVP {o.get('ivp',0):.0f}%",
            "risk_note": "60% normal size — post-drop rules apply",
        })

    dash_pio = []
    for o in top_pio:
        s = o.get("pio_cc", {})
        if not s: continue
        ppd = round(s.get("premium",0) / max(1, s.get("dte",1)), 2)
        dash_pio.append({
            "ticker": o.get("ticker",""), "tier": o.get("tier",""),
            "price": o.get("price",0), "ivp": round(o.get("ivp",0),1),
            "mode": "PIO",
            "strike": s.get("strike",0), "expiry": s.get("expiry",""),
            "dte": s.get("dte",0), "premium": s.get("premium",0),
            "annualized_return": s.get("annualized_return",0),
            "delta": s.get("delta",0),
            "below_min": False, "warnings": [], "passes_quality": True,
            "risk_level": "Low",
            "breakeven": s.get("breakeven", s.get("avg_cost",0)),
            "premium_per_day": ppd,
            "position_status": o.get("pnl_status","").capitalize(),
            "signal": f"{o.get('pnl_status','').capitalize()} position | Above cost basis ${s.get('avg_cost',0):.0f}",
            "risk_note": None,
        })

    # ── P5: Separate execution vs review candidates ──────────
    # execution_candidates: passed ALL strict filters (Telegram-ready)
    # review_candidates: relaxed filters, for dashboard human review
    execution_candidates = all_opps  # already filtered by main scan strict rules

    # Dashboard always uses three-band dashboard_leaps — never deduplicate against leaps_from_opps.
    # leaps_from_opps (strict Telegram path) returns only one contract per ticker.
    # dashboard_leaps has all three bands with full extrinsic/BE fields — always prefer it.
    leaps_from_opps = [o for o in all_opps if o.get("mode") == "LEAPS"]
    review_candidates = []
    for o in dashboard_csps + dashboard_ccs + dashboard_leaps + dashboard_convexity + dash_spikes + dash_drops + dash_pio:
        o["candidate_type"] = "execution" if o.get("passes_quality") and not o.get("below_min") and not o.get("warnings") else "review"
        review_candidates.append(o)

    # Ensure all LEAPS review_candidates have consistent string trend fields
    for _o in review_candidates:
        if _o.get("mode") != "LEAPS":
            continue
        if not _o.get("trend_label"):
            try:
                _tr = leaps_trend_state(_o["ticker"], _o["price"])
                _w  = mkt.get(_o["ticker"], {}).get("week52_high", _o["price"] * 1.3)
                _ta = leaps_trend_action(_tr, _o.get("ivp", 50), _o["price"], _w)
                _o["trend_state"]  = str(_tr.get("state", "UNKNOWN"))
                _o["trend_label"]  = str(_ta.get("label", "WATCH"))
                _o["trend_signal"] = str(_ta.get("signal", ""))
                _o["trend_action"] = str(_ta.get("action", "WATCH"))
            except Exception:
                _o["trend_state"]  = "UNKNOWN"
                _o["trend_label"]  = "WATCH"
                _o["trend_signal"] = ""
                _o["trend_action"] = "WATCH"
        else:
            # Normalize existing fields to strings
            if isinstance(_o.get("trend_action"), dict):
                _o["trend_action"] = str(_o["trend_action"].get("action", "WATCH"))
            _o["trend_label"]  = str(_o.get("trend_label", "WATCH"))
            _o["trend_signal"] = str(_o.get("trend_signal", ""))
            _o["trend_action"] = str(_o.get("trend_action", "WATCH"))

    # Aggregate premium totals into exposure block
    _prem_csp = sum(o.get("sizing",{}).get("premium_received",0) for o in all_opps if o.get("mode") in ("CSP","DROP_CSP"))
    _prem_cc  = sum(o.get("sizing",{}).get("premium_received",0) for o in all_opps if o.get("mode") in ("CC","PIO","SPIKE_CC"))
    portfolio_exposure["total_premium_csp"] = round(_prem_csp, 2)
    portfolio_exposure["total_premium_cc"]  = round(_prem_cc,  2)
    portfolio_exposure["total_premium_all"] = round(_prem_csp + _prem_cc, 2)

    # ── Position Management Engine ─────────────────────────────────────
    _spy_day = spy_regime.get("day_change", 0)
    _total_cso = portfolio_exposure.get("total_csp_obligation", 0)
    _pos_actions = []

    # Previous scan's per-position P&L (results.json is committed back by the
    # workflow every run, so the last scan's file is on disk at startup).
    # Keyed by (ticker, type, strike, expiry) → profit_pct. Feeds the
    # P&L SWING alert and the "swing since last scan" context line.
    _prev_pnl = {}
    _prev_scan_time = ""
    _prev_tg_alerts = {}   # position-key -> date of last Telegram ping (P17 dedup)
    try:
        with open("results.json") as _pf:
            _prev_res = json.load(_pf)
        _prev_scan_time = _prev_res.get("scan_time", "")
        _prev_tg_alerts = _prev_res.get("tg_position_alerts", {}) or {}
        for _pa in _prev_res.get("position_actions", []):
            if _pa.get("mark_src") in ("chain", "chain_near"):  # only credible P&L
                _key = (_pa.get("ticker",""), _pa.get("type",""),
                        round(float(_pa.get("strike",0) or 0), 2),
                        str(_pa.get("expiry",""))[:10])
                _prev_pnl[_key] = _pa.get("profit_pct")
        print(f"   📈 Prev-scan P&L loaded: {len(_prev_pnl)} positions ({_prev_scan_time})")
    except Exception as _pe:
        print(f"   📈 No prev-scan P&L available ({_pe})")

    for _pos in portfolio_exposure.get("csp_positions", []):
        _ticker = _pos.get("ticker","")
        _tier = ("Core" if _ticker in CORE_STOCKS else
                 "Growth" if _ticker in GROWTH_STOCKS else
                 "Cyclical" if _ticker in CYCLICAL_STOCKS else "Opportunistic")
        # Get current option mark from contracts cache
        _contracts_opt = contracts_cache.get(_ticker, [])
        _strike = _pos.get("strike", 0)
        _expiry = _pos.get("expiry", "")
        _mark = 0
        _mark_src = "none"
        # Normalize expiry to YYYY-MM-DD for comparison
        _exp_norm = str(_expiry).replace("-","")[:8]  # YYYYMMDD
        for _c in _contracts_opt:
            _c_exp = str(_c.get("expiry","")).replace("-","")[:8]
            if (abs(float(_c.get("strike",0)) - _strike) < 0.5 and
                    _c_exp == _exp_norm and
                    _c.get("option_type") == "P"):
                _b = float(_c.get("nbbo_bid",0) or 0)
                _a = float(_c.get("nbbo_ask",0) or 0)
                if _a > 0:
                    _mark = (_b + _a) / 2 if _b > 0 else _a
                    _mark_src = "chain"
                break
        # If still 0, find closest strike
        if _mark == 0:
            _puts = [_c for _c in _contracts_opt if _c.get("option_type") == "P"
                     and abs(float(_c.get("strike",0)) - _strike) < 1.0]
            if _puts:
                _c = _puts[0]
                _b = float(_c.get("nbbo_bid",0) or 0)
                _a = float(_c.get("nbbo_ask",0) or 0)
                if _a > 0:
                    _mark = (_b + _a) / 2 if _b > 0 else _a
                    _mark_src = "chain_near"
        # Final fallback: use mark derived from IBKR market_value (may be stale)
        if _mark == 0:
            _mark = _pos.get("mark_from_mv", 0)
            _mark_src = "position_mv" if _mark > 0 else "none"
        # Earnings
        _earn = get_earnings_date(_ticker)
        _earn_days = (_earn - datetime.now()).days if _earn else 999

        _prev_pp = _prev_pnl.get((_ticker, "CSP", round(float(_strike or 0), 2),
                                  str(_expiry)[:10]))
        _result = position_management_engine(
            {**_pos, "type": "CSP", "tier": _tier, "mark": _mark, "mark_src": _mark_src,
             "days_to_earnings": _earn_days, "prev_profit_pct": _prev_pp},
            mkt, PORTFOLIO_SIZE, _total_cso, _spy_day
        )
        _pos_actions.append({
            "account":        _pos.get("account",""),
            "ticker":         _ticker,
            "tier":           _tier,
            "type":           "CSP",
            "contracts":      _pos.get("contracts",1),
            "strike":         _strike,
            "expiry":         str(_expiry)[:10],
            "dte":            _result["dte"],
            "underlying":     round(mkt.get(_ticker,{}).get("price",0),2),
            "mark":           round(_mark,2),
            "mark_src":       _mark_src,
            "premium_received": round(_pos.get("premium_received",0),2),
            "profit_pct":     _result["profit_pct"],
            "prev_profit_pct": _result["prev_profit_pct"],
            "pnl_dollar":     _result["pnl_dollar"],
            "breakeven":      _result["breakeven"],
            "dist_to_be_pct": _result["dist_to_be_pct"],
            "dist_to_strike": _result["dist_to_strike"],
            "remaining_prem": _result["remaining_prem"],
            "day_chg_pct":    round(mkt.get(_ticker,{}).get("day_change_pct",0)*100,2),
            "chg_3d_pct":     round(mkt.get(_ticker,{}).get("change_3d_pct",0)*100,2),
            "cso":            _pos.get("cso",0),
            "earn_days":      _result["earn_days"],
            "earn_zone":      _result["earn_zone"],
            "action":         _result["action"],
            "reason":         _result["reason"],
            "sort_priority":  _result["sort_priority"],
        })

    for _pos in portfolio_exposure.get("cc_positions", []):
        _ticker = _pos.get("ticker","")
        _tier = ("Core" if _ticker in CORE_STOCKS else
                 "Growth" if _ticker in GROWTH_STOCKS else
                 "Cyclical" if _ticker in CYCLICAL_STOCKS else "Opportunistic")
        _strike = _pos.get("strike", 0)
        _expiry = _pos.get("expiry", "")
        # Prefer the LIVE chain NBBO (fresh Schwab quote this scan) over the
        # position feed's market-value mark, which can be badly stale on a fast
        # intraday move. Fall back to the stale mark only if no chain quote.
        _mark_cc = 0
        _mark_src = "none"
        _contracts_opt = contracts_cache.get(_ticker, [])
        _exp_norm_cc = str(_expiry).replace("-","")[:8]
        for _c in _contracts_opt:
            _c_exp = str(_c.get("expiry","")).replace("-","")[:8]
            if (abs(float(_c.get("strike",0)) - _strike) < 0.5 and
                    _c_exp == _exp_norm_cc and
                    _c.get("option_type") == "C"):
                _b = float(_c.get("nbbo_bid",0) or 0)
                _a = float(_c.get("nbbo_ask",0) or 0)
                if _a > 0:   # need a real ask for a usable mark
                    _mark_cc = (_b + _a) / 2 if _b > 0 else _a
                    _mark_src = "chain"
                break
        if _mark_cc <= 0:
            # Fallback: position feed market-value mark (may be stale)
            _mark_cc = _pos.get("mark", 0)
            _mark_src = "position_mv" if _mark_cc > 0 else "none"
        _earn = get_earnings_date(_ticker)
        _earn_days = (_earn - datetime.now()).days if _earn else 999
        _prev_pp = _prev_pnl.get((_ticker, "CC", round(float(_strike or 0), 2),
                                  str(_expiry)[:10]))
        _result = position_management_engine(
            {**_pos, "type": "CC", "tier": _tier, "mark": _mark_cc, "mark_src": _mark_src,
             "days_to_earnings": _earn_days, "prev_profit_pct": _prev_pp},
            mkt, PORTFOLIO_SIZE, _total_cso, _spy_day
        )
        _pos_actions.append({
            "account":        _pos.get("account",""),
            "ticker":         _ticker,
            "tier":           _tier,
            "type":           "CC",
            "contracts":      _pos.get("contracts",1),
            "strike":         _strike,
            "expiry":         str(_expiry)[:10],
            "dte":            _result["dte"],
            "underlying":     round(mkt.get(_ticker,{}).get("price",0),2),
            "mark":           round(_mark_cc,2),
            "mark_src":       _mark_src,
            "premium_received": round(_pos.get("premium_received",0),2),
            "profit_pct":     _result["profit_pct"],
            "prev_profit_pct": _result["prev_profit_pct"],
            "pnl_dollar":     _result["pnl_dollar"],
            "breakeven":      _result["breakeven"],
            "dist_to_be_pct": _result["dist_to_be_pct"],
            "dist_to_strike": _result["dist_to_strike"],
            "remaining_prem": _result["remaining_prem"],
            "day_chg_pct":    round(mkt.get(_ticker,{}).get("day_change_pct",0)*100,2),
            "chg_3d_pct":     round(mkt.get(_ticker,{}).get("change_3d_pct",0)*100,2),
            "shares_covered": _pos.get("shares_covered",0),
            "earn_days":      _result["earn_days"],
            "earn_zone":      _result["earn_zone"],
            "action":         _result["action"],
            "reason":         _result["reason"],
            "sort_priority":  _result["sort_priority"],
        })

    _pos_actions.sort(key=lambda x: x.get("sort_priority", 3))
    _def_count   = sum(1 for p in _pos_actions if p["action"] == "DEFENSIVE")
    _close_count = sum(1 for p in _pos_actions if p["action"] in ("BIG MOVE", "P&L SWING"))
    print(f"   📋 Position actions: {len(_pos_actions)} total | {_def_count} DEFENSIVE | {_close_count} BIG MOVE")

    # ── BIG MOVE — REVIEW → Telegram ──────────────────────────────────
    # Event-driven: a big favorable move on a name you hold a short option.
    # P17 gate: only decision-pressure alerts reach Telegram, once per
    # position per day (the dashboard shows every action regardless).
    _today_str = now_et().strftime("%Y-%m-%d")
    # Carry forward keys already pinged today (results.json survives scans)
    _tg_alert_log = {k: d for k, d in _prev_tg_alerts.items() if d == _today_str}
    _tg_bigmove = []
    _tg_suppressed = 0
    for p in _pos_actions:
        if p["action"] not in ("BIG MOVE", "P&L SWING"):
            continue
        _tgk = f"{p['ticker']}|{p['type']}|{p['strike']:g}|{p['expiry']}"
        if _tgk in _tg_alert_log or not tg_position_alert_worthy(p):
            _tg_suppressed += 1
            continue
        _tg_bigmove.append((_tgk, p))
    if _tg_suppressed:
        print(f"   🔕 {_tg_suppressed} position alert(s) dashboard-only (P17 gate / already pinged today)")
    if _tg_bigmove:
        print(f"   📱 Sending {len(_tg_bigmove)} BIG MOVE alert(s)...")
        send_telegram("━━━ *🚨 BIG MOVE — REVIEW YOUR SHORT OPTION* ━━━"); time.sleep(1)
        for _tgk, p in _tg_bigmove:
            _tg_alert_log[_tgk] = _today_str
            _emoji = "🟢" if p["type"] == "CSP" else "🔵"
            _pl = f"{p['profit_pct']:.0f}% profit" if p['profit_pct'] >= 0 else f"{abs(p['profit_pct']):.0f}% loss"
            _src = p.get("mark_src", "none")
            # On a fast move, the position-feed mark can lag. Flag it so a
            # confident-but-stale number isn't taken at face value.
            if _src in ("position_mv", "none"):
                _price_line = (f"  ⚠️ Option price may be stale (${p['mark']:.2f}) — "
                               f"check live quote before acting")
            else:
                _price_line = (f"  Premium in: ${p['premium_received']:,.0f} | "
                               f"now ${p['mark']:.2f} to close ({_pl})")
            _msg = "\n".join([
                f"{_emoji} *{p['type']} — {p['ticker']} ${p['strike']:g}*",
                f"  {p['reason']}",
                f"  Underlying ${p['underlying']} | strike ${p['strike']:g} | {p['dte']}d left",
                _price_line,
                f"_Scanned {now_et().strftime('%b %d %H:%M')} PT_"
            ])
            send_telegram(_msg); time.sleep(2)
    else:
        print(f"   🚨 No BIG MOVE reviews this scan")

    results = {
        "scan_time":           now_et().strftime("%Y-%m-%d %H:%M ET"),
        "scan_date":           now_et().strftime("%Y-%m-%d"),
        "schwab_live":         len(schwab_quotes) > 0,
        "schwab_last_success": now_et().strftime("%Y-%m-%d %H:%M ET") if len(schwab_quotes) > 0 else None,
        "execution_candidates": execution_candidates,   # strict — Telegram quality
        "review_candidates":    review_candidates,      # relaxed — dashboard review
        "dashboard_opportunities": review_candidates,   # alias for dashboard compat
        "market": {
            "vix":        gng["vix"],
            "vix_label":  vix_data.get("label",""),
            "spy":        round(spy_regime.get("spy",0),2),
            "spy_ma200":  round(spy_regime.get("ma200",0),2),
            "spy_above":  spy_regime.get("above_ma200",True),
            "verdict":    gng.get("quality",""),
        },
        "exposure":       portfolio_exposure,
        "exposure":       portfolio_exposure,
        "opportunities":  all_opps,
        "positions":      pos_list,
        "position_actions": _pos_actions,
        "tg_position_alerts": _tg_alert_log,   # P17 dedup: pos-key -> date pinged
        "analysis":       analysis,
        "total_opps":     len(all_opps),
        "price_watch":    price_watch_list,
    }

    # Debug: print trend fields for all LEAPS in review_candidates
    _leaps_rc = [o for o in review_candidates if o.get("mode") == "LEAPS"]
    print(f"   LEAPS in review_candidates: {len(_leaps_rc)}")
    for _lo in _leaps_rc[:5]:
        print(f"     {_lo['ticker']}: trend_label={_lo.get('trend_label')!r} trend_action={_lo.get('trend_action')!r}")

    with open("results.json","w") as f:
        json.dump(results, f, indent=2)
    print("   💾 results.json saved")

    # ── Save IBKR positions cache (for stale-Flex protection on next run) ──
    try:
        _ibkr_only = {k: v for k, v in ibkr.items() if v.get("source", "ibkr") == "ibkr"}
        _ibkr_opts = sum(1 for v in _ibkr_only.values() if v.get("asset_class") == "OPT")
        if _ibkr_opts > 0:
            with open("ibkr_positions_cache.json", "w") as _cf:
                json.dump(_ibkr_only, _cf)
            print(f"   💾 ibkr_positions_cache.json saved ({_ibkr_opts} IBKR options cached)")
    except Exception as _ce:
        print(f"   ⚠️ Could not save IBKR cache: {_ce}")

    print("\n✅ Done!")


if __name__ == "__main__":
    run_scanner()
