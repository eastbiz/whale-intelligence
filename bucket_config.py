"""
bucket_config.py — Phase 1 of sophisticated options system

Loads ticker-to-bucket mapping and provides threshold lookup functions
for bucket-aware CSP/CC/spread/LEAPS scanning.

Special flags supported:
  - spreads_only: Only PUT_SPREAD/CALL_SPREAD allowed (NBIS, CRDO)
  - leaps_only: Only LEAPS allowed, no premium selling (BABA)
  - cc_only: Only CC allowed — for exit-waiting positions (MSTR, OWL)
  - WATCHLIST tier: Entry strategies blocked unless price in lower zones (META)
"""

import csv
from typing import Dict, Optional, Tuple


BUCKET_DEFAULTS = {
    "A": {
        "min_ann_csp": 12.0, "min_ann_cc": 10.0, "min_ror_spread": 0.0,
        "delta_csp_min": 0.20, "delta_csp_max": 0.30,
        "delta_cc_min": 0.20, "delta_cc_max": 0.30,
        "dte_min": 30, "dte_max": 45,
        "max_position_pct": 12.0,
        "leaps_allowed": True, "leaps_only": False,
        "spreads_only": False, "cc_only": False,
    },
    "B": {
        "min_ann_csp": 18.0, "min_ann_cc": 14.0, "min_ror_spread": 0.0,
        "delta_csp_min": 0.20, "delta_csp_max": 0.30,
        "delta_cc_min": 0.20, "delta_cc_max": 0.30,
        "dte_min": 30, "dte_max": 45,
        "max_position_pct": 8.0,
        "leaps_allowed": True, "leaps_only": False,
        "spreads_only": False, "cc_only": False,
    },
    "C": {
        "min_ann_csp": 28.0, "min_ann_cc": 22.0, "min_ror_spread": 30.0,
        "delta_csp_min": 0.15, "delta_csp_max": 0.20,
        "delta_cc_min": 0.20, "delta_cc_max": 0.30,
        "dte_min": 21, "dte_max": 35,
        "max_position_pct": 5.0,
        "leaps_allowed": False, "leaps_only": False,
        "spreads_only": False, "cc_only": False,
    },
    "D": {
        "min_ann_csp": 40.0, "min_ann_cc": 30.0, "min_ror_spread": 25.0,
        "delta_csp_min": 0.10, "delta_csp_max": 0.15,
        "delta_cc_min": 0.15, "delta_cc_max": 0.20,
        "dte_min": 21, "dte_max": 35,
        "max_position_pct": 3.0,
        "leaps_allowed": False, "leaps_only": False,
        "spreads_only": True, "cc_only": False,
    },
}

FALLBACK_BUCKET = "C"


def load_buckets(csv_path: str = "buckets.csv") -> Dict[str, dict]:
    buckets = {}
    try:
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ticker = row["ticker"].strip().upper()
                buckets[ticker] = {
                    "bucket": row["bucket"].strip().upper(),
                    "tier_legacy": row.get("tier_legacy", "").strip(),
                    "target_pct": float(row.get("target_pct", 0) or 0),
                    "min_ann_csp": float(row.get("min_ann_csp", 0) or 0),
                    "min_ann_cc": float(row.get("min_ann_cc", 0) or 0),
                    "min_ror_spread": float(row.get("min_ror_spread", 0) or 0),
                    "leaps_allowed": str(row.get("leaps_allowed", "FALSE")).upper() == "TRUE",
                    "leaps_only": str(row.get("leaps_only", "FALSE")).upper() == "TRUE",
                    "spreads_only": str(row.get("spreads_only", "FALSE")).upper() == "TRUE",
                    "cc_only": str(row.get("cc_only", "FALSE")).upper() == "TRUE",
                    "delta_csp_min": float(row.get("delta_csp_min", 0.20) or 0.20),
                    "delta_csp_max": float(row.get("delta_csp_max", 0.30) or 0.30),
                    "delta_cc_min": float(row.get("delta_cc_min", 0.20) or 0.20),
                    "delta_cc_max": float(row.get("delta_cc_max", 0.30) or 0.30),
                    "dte_min": int(row.get("dte_min", 30) or 30),
                    "dte_max": int(row.get("dte_max", 45) or 45),
                    "max_position_pct": float(row.get("max_position_pct", 4.0) or 4.0),
                    "notes": row.get("notes", "").strip(),
                }
        print(f"✅ Loaded {len(buckets)} ticker bucket assignments from {csv_path}")
    except FileNotFoundError:
        print(f"⚠️  {csv_path} not found — using defaults for all tickers")
    except Exception as e:
        print(f"❌ Error loading buckets: {e}")
    return buckets


def get_bucket(ticker: str, buckets: Dict) -> str:
    return buckets.get(ticker.upper(), {}).get("bucket", FALLBACK_BUCKET)


def _resolve(ticker: str, buckets: Dict, key: str, default=None):
    """Resolve a config value: use CSV row if present and non-zero/non-empty, else bucket default."""
    entry = buckets.get(ticker.upper())
    if entry and key in entry:
        val = entry[key]
        if val not in (0, None, ""):
            return val
    bucket = entry["bucket"] if entry else FALLBACK_BUCKET
    return BUCKET_DEFAULTS.get(bucket, {}).get(key, default)


def get_min_annualized_csp(ticker: str, buckets: Dict) -> float:
    return float(_resolve(ticker, buckets, "min_ann_csp", 18.0))


def get_min_annualized_cc(ticker: str, buckets: Dict) -> float:
    return float(_resolve(ticker, buckets, "min_ann_cc", 14.0))


def get_min_ror_spread(ticker: str, buckets: Dict) -> float:
    return float(_resolve(ticker, buckets, "min_ror_spread", 25.0))


def get_delta_range_csp(ticker: str, buckets: Dict) -> Tuple[float, float]:
    return (float(_resolve(ticker, buckets, "delta_csp_min", 0.20)),
            float(_resolve(ticker, buckets, "delta_csp_max", 0.30)))


def get_delta_range_cc(ticker: str, buckets: Dict) -> Tuple[float, float]:
    return (float(_resolve(ticker, buckets, "delta_cc_min", 0.20)),
            float(_resolve(ticker, buckets, "delta_cc_max", 0.30)))


def get_dte_range(ticker: str, buckets: Dict) -> Tuple[int, int]:
    return (int(_resolve(ticker, buckets, "dte_min", 30)),
            int(_resolve(ticker, buckets, "dte_max", 45)))


def get_max_position_pct(ticker: str, buckets: Dict) -> float:
    return float(_resolve(ticker, buckets, "max_position_pct", 4.0))


def _bool_flag(ticker: str, buckets: Dict, key: str) -> bool:
    entry = buckets.get(ticker.upper(), {})
    return bool(entry.get(key, False))


def is_spreads_only(ticker: str, buckets: Dict) -> bool:
    return _bool_flag(ticker, buckets, "spreads_only")


def is_leaps_only(ticker: str, buckets: Dict) -> bool:
    return _bool_flag(ticker, buckets, "leaps_only")


def is_cc_only(ticker: str, buckets: Dict) -> bool:
    """Exit-waiting positions (MSTR, OWL): only CC allowed, no new entries."""
    return _bool_flag(ticker, buckets, "cc_only")


def is_leaps_allowed(ticker: str, buckets: Dict) -> bool:
    entry = buckets.get(ticker.upper())
    if entry:
        return bool(entry.get("leaps_allowed", True))
    return BUCKET_DEFAULTS.get(FALLBACK_BUCKET, {}).get("leaps_allowed", True)


def is_watchlist_only(ticker: str, buckets: Dict) -> bool:
    entry = buckets.get(ticker.upper(), {})
    return entry.get("tier_legacy", "").upper() == "WATCHLIST"


def is_exit_only(ticker: str, buckets: Dict) -> bool:
    """EXIT_CC_ONLY tier — same idea as cc_only but explicit semantic label."""
    entry = buckets.get(ticker.upper(), {})
    return entry.get("tier_legacy", "").upper().startswith("EXIT")


def classify_price_zone(price: float, buy_below: float, sell_above: float) -> str:
    if not (buy_below and sell_above and buy_below < sell_above):
        return "unknown"
    if price < buy_below: return "below_BB"
    if price > sell_above: return "above_SA"
    band = sell_above - buy_below
    q1 = buy_below + band * 0.25
    q2 = buy_below + band * 0.50
    q3 = buy_below + band * 0.75
    if price <= q1: return "lower_band"
    if price <= q2: return "mid_low"
    if price <= q3: return "mid_high"
    return "upper_band"


def classify_ivr_regime(ivr: float) -> str:
    if ivr is None: return "unknown"
    if ivr < 25: return "crushed"
    if ivr < 50: return "normal"
    if ivr < 70: return "elevated"
    return "extreme"


def strategy_allowed(ticker: str, buckets: Dict, strategy: str,
                     ivr: float, price_zone: str) -> Tuple[bool, str]:
    """
    Master gate: is this strategy allowed for this ticker right now?

    strategy in {"CSP", "CC", "LEAPS", "PUT_SPREAD", "CALL_SPREAD"}
    Returns (allowed: bool, reason: str)
    """
    bucket = get_bucket(ticker, buckets)
    ivr_regime = classify_ivr_regime(ivr)

    # cc_only override: only CC allowed (exit-waiting positions like MSTR, OWL)
    if is_cc_only(ticker, buckets) and strategy != "CC":
        return False, f"{ticker}: CC-only (exit-waiting), {strategy} not allowed"

    # leaps_only override: only LEAPS allowed (BABA)
    if is_leaps_only(ticker, buckets) and strategy != "LEAPS":
        return False, f"{ticker}: LEAPS-only, {strategy} not allowed"

    # Watchlist tickers (META): only allow entry strategies in deep pullback
    if is_watchlist_only(ticker, buckets) and strategy in ("CSP", "PUT_SPREAD", "LEAPS"):
        if price_zone not in ("below_BB", "lower_band"):
            return False, f"{ticker}: watchlist — wait for below_BB or lower_band (current: {price_zone})"

    # LEAPS rules
    if strategy == "LEAPS":
        if not is_leaps_allowed(ticker, buckets):
            return False, f"{bucket}: LEAPS not allowed for this bucket"
        if ivr_regime in ("elevated", "extreme"):
            return False, f"IVR {ivr_regime} ({ivr:.0f}): LEAPS too expensive (vega risk)"
        if price_zone in ("upper_band", "above_SA"):
            return False, f"Price in {price_zone}: not LEAPS territory"
        return True, f"LEAPS OK — IVR {ivr_regime}, zone {price_zone}"

    # CSP rules
    if strategy == "CSP":
        if is_spreads_only(ticker, buckets):
            return False, f"{bucket}: spreads-only — use PUT_SPREAD"
        if price_zone in ("upper_band", "above_SA"):
            return False, f"Price in {price_zone}: stop new CSPs"
        if bucket in ("C", "D") and ivr_regime in ("crushed", "normal"):
            return False, f"{bucket} bucket needs IVR>50 — currently {ivr:.0f}"
        return True, f"CSP OK — bucket {bucket}, IVR {ivr_regime}, zone {price_zone}"

    # CC rules — assumes shares are owned
    if strategy == "CC":
        if price_zone == "below_BB" and not is_cc_only(ticker, buckets):
            return False, "Price below Buy Below — no CCs (let it run)"
        # For cc_only tickers (exit-waiting), CC allowed in all zones to harvest premium
        return True, f"CC OK — zone {price_zone}, IVR {ivr_regime}"

    # PUT_SPREAD
    if strategy == "PUT_SPREAD":
        if price_zone in ("upper_band", "above_SA"):
            return False, f"Price in {price_zone}: not put spread territory"
        if ivr_regime == "crushed":
            return False, "IVR crushed: spreads pay too little"
        return True, f"PUT_SPREAD OK — bucket {bucket}, IVR {ivr_regime}"

    # CALL_SPREAD
    if strategy == "CALL_SPREAD":
        if ivr_regime in ("elevated", "extreme"):
            return False, f"IVR {ivr_regime}: debit spreads overpriced"
        if price_zone == "above_SA":
            return False, "Price above Sell Above"
        return True, f"CALL_SPREAD OK — IVR {ivr_regime}"

    return False, f"Unknown strategy: {strategy}"


# ── Sanity test ──
if __name__ == "__main__":
    buckets = load_buckets("buckets.csv")

    print(f"\n📊 Bucket distribution:")
    by_bucket = {}
    for tkr, cfg in buckets.items():
        by_bucket.setdefault(cfg["bucket"], []).append(tkr)
    for b in sorted(by_bucket):
        print(f"  {b} ({len(by_bucket[b])}): {', '.join(sorted(by_bucket[b]))}")

    print(f"\n🏷️  Special flags:")
    for tkr in sorted(buckets.keys()):
        flags = []
        if is_leaps_only(tkr, buckets): flags.append("LEAPS_ONLY")
        if is_spreads_only(tkr, buckets): flags.append("SPREADS_ONLY")
        if is_cc_only(tkr, buckets): flags.append("CC_ONLY")
        if is_watchlist_only(tkr, buckets): flags.append("WATCHLIST")
        if flags:
            print(f"  {tkr}: {', '.join(flags)}")

    print(f"\n🧪 Strategy gating tests:")
    tests = [
        # (ticker, strategy, ivr, zone, expected_allowed, label)
        ("AAPL", "CSP", 35, "lower_band", True, "AAPL bucket A passes"),
        ("BABA", "CSP", 60, "lower_band", False, "BABA leaps_only blocks CSP"),
        ("BABA", "LEAPS", 20, "lower_band", True, "BABA LEAPS at low IVR OK"),
        ("BABA", "LEAPS", 80, "lower_band", False, "BABA LEAPS at high IVR blocked"),
        ("NBIS", "CSP", 65, "lower_band", True, "NBIS CSP OK at IVR>50 (spreads_only removed)"),
        ("NBIS", "PUT_SPREAD", 65, "lower_band", True, "NBIS spread OK"),
        ("META", "CSP", 60, "mid_low", False, "META watchlist blocks mid-zone"),
        ("META", "CSP", 60, "lower_band", True, "META CSP OK in lower"),
        ("TSLA", "LEAPS", 80, "lower_band", False, "TSLA C bucket no LEAPS"),
        ("NVDA", "LEAPS", 20, "lower_band", True, "NVDA LEAPS OK at low IVR"),
        ("AAPL", "CSP", 35, "upper_band", False, "AAPL no CSP in upper band"),
        ("TSLA", "CSP", 35, "lower_band", False, "TSLA C bucket needs IVR>50"),
        ("TSLA", "CSP", 65, "lower_band", True, "TSLA CSP at IVR 65 OK"),
        # NEW cc_only tests
        ("MSTR", "CSP", 60, "lower_band", False, "MSTR cc_only blocks CSP"),
        ("MSTR", "LEAPS", 20, "lower_band", False, "MSTR cc_only blocks LEAPS"),
        ("MSTR", "PUT_SPREAD", 60, "lower_band", False, "MSTR cc_only blocks spreads"),
        ("MSTR", "CC", 60, "mid_low", True, "MSTR CC OK in any zone"),
        ("MSTR", "CC", 60, "below_BB", True, "MSTR CC OK even below_BB (exit-waiting)"),
        ("OWL", "CSP", 60, "lower_band", False, "OWL cc_only blocks CSP"),
        ("OWL", "CC", 50, "upper_band", True, "OWL CC OK in upper band"),
    ]
    passed = 0
    for tkr, strat, ivr, zone, expected, label in tests:
        ok, reason = strategy_allowed(tkr, buckets, strat, ivr, zone)
        match = "✅" if ok == expected else "❌"
        if ok == expected: passed += 1
        print(f"  {match} {label:48} → {ok}: {reason}")
    print(f"\n  {passed}/{len(tests)} tests passed")
