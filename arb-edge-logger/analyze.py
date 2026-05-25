"""Honest analysis of the edge_observations.sqlite3 data.

Produces a single Markdown report with three answers, in order of trust:

  1. Naive edge -- top-of-book sum < $0.985. Almost always false positive.
  2. Realistic edge -- walked depth, fees, oracle haircut subtracted. The honest number.
  3. Filtered edge -- realistic edge AFTER filtering out unreliable observations
     (skew > 100ms cross-venue; depth_exhausted on either side; staleness > 1s).

Run anytime:
    python -m analyze                       # full report
    python -m analyze --min-obs 100         # only show pairs with >= 100 observations
    python -m analyze --size 100            # only show $100 size column
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "edge_observations.sqlite3"


def connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print(f"ERROR: no database at {DB_PATH}. Run logger.py first.", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def report_yesno(conn, min_obs: int, only_size: float | None) -> None:
    print("\n" + "=" * 78)
    print(" SINGLE-VENUE YES+NO COMPLEMENTARITY")
    print("=" * 78)

    # Overall stats
    row = conn.execute(
        """SELECT COUNT(*) AS n,
                  MIN(observed_at_utc) AS first,
                  MAX(observed_at_utc) AS last,
                  COUNT(DISTINCT market_key) AS markets,
                  COUNT(DISTINCT venue) AS venues
           FROM yes_no_observations"""
    ).fetchone()
    print(f"\nObservations: {row['n']:,}")
    print(f"Markets:      {row['markets']:,}")
    print(f"Venues:       {row['venues']}")
    print(f"Time range:   {row['first']}  to  {row['last']}")

    # Size breakdown
    print("\n--- Realistic edge (walked depth, fees included) ---")
    print(f"{'Size':>8}  {'Total Obs':>10}  {'Net>0 count':>12}  {'Net>0 %':>9}  "
          f"{'Avg net edge':>13}  {'Best net edge':>14}  {'Best market':>40}")
    sql = """
        SELECT size_usd,
               COUNT(*) AS total_obs,
               SUM(CASE WHEN net_edge_usd > 0 THEN 1 ELSE 0 END) AS positive,
               AVG(net_edge_usd) AS avg_net,
               MAX(net_edge_usd) AS best,
               (SELECT market_key FROM yes_no_observations AS y2
                WHERE y2.size_usd = y.size_usd
                ORDER BY y2.net_edge_usd DESC LIMIT 1) AS best_mkt
          FROM yes_no_observations AS y
         WHERE depth_ok = 1 AND net_edge_usd IS NOT NULL
        GROUP BY size_usd
        ORDER BY size_usd
    """
    for r in conn.execute(sql):
        if only_size is not None and abs(r["size_usd"] - only_size) > 0.01:
            continue
        pct = 100.0 * r["positive"] / r["total_obs"] if r["total_obs"] else 0.0
        print(f"${r['size_usd']:>6.0f}  {r['total_obs']:>10,}  {r['positive']:>12,}  "
              f"{pct:>8.2f}%  ${r['avg_net'] or 0:>11.4f}  ${r['best'] or 0:>12.4f}  {(r['best_mkt'] or '')[:40]}")

    # Per-market analysis: which markets had repeatable positive edge?
    print(f"\n--- Markets with repeatable positive realistic edge (depth_ok, min {min_obs} obs/size) ---")
    sql = """
        SELECT venue, asset, duration_class, market_key, size_usd,
               COUNT(*) AS n,
               SUM(CASE WHEN net_edge_usd > 0 THEN 1 ELSE 0 END) AS positive,
               AVG(net_edge_usd) AS avg_edge,
               MIN(net_edge_usd) AS worst, MAX(net_edge_usd) AS best
          FROM yes_no_observations
         WHERE depth_ok = 1 AND net_edge_usd IS NOT NULL
        GROUP BY venue, market_key, size_usd
        HAVING n >= ? AND positive > 0
        ORDER BY avg_edge DESC
        LIMIT 30
    """
    rows = conn.execute(sql, (min_obs,)).fetchall()
    if not rows:
        print(f"  (none -- no market has >= {min_obs} observations with positive realistic edge)")
        print(f"  This is the EXPECTED honest result if the market is efficient.")
        return
    print(f"{'Venue':>10}  {'Asset':>5}  {'Dur':>4}  {'Size':>6}  {'Obs':>5}  "
          f"{'Pos%':>5}  {'Avg edge':>10}  {'Best':>8}  {'Worst':>9}  Market")
    for r in rows:
        pct = 100.0 * r["positive"] / r["n"]
        print(f"{r['venue']:>10}  {r['asset']:>5}  {r['duration_class'] or '?':>4}  "
              f"${r['size_usd']:>4.0f}  {r['n']:>5}  {pct:>4.1f}%  "
              f"${r['avg_edge']:>8.4f}  ${r['best']:>6.4f}  ${r['worst']:>7.4f}  "
              f"{r['market_key'][:50]}")


def report_cross(conn, min_obs: int, only_size: float | None) -> None:
    print("\n" + "=" * 78)
    print(" CROSS-VENUE ARBITRAGE")
    print("=" * 78)

    row = conn.execute("SELECT COUNT(*) AS n FROM cross_venue_observations").fetchone()
    print(f"\nObservations: {row['n']:,}")
    if row["n"] == 0:
        print("  (no cross-venue observations yet -- check discover.py output for pair count)")
        return

    # Skew distribution -- how often were the two venues fetched close enough in time?
    skew = conn.execute(
        """SELECT
               SUM(CASE WHEN skew_unreliable = 0 THEN 1 ELSE 0 END) AS reliable,
               SUM(CASE WHEN skew_unreliable = 1 THEN 1 ELSE 0 END) AS unreliable,
               AVG(skew_ms) AS avg_skew,
               MAX(skew_ms) AS max_skew
           FROM cross_venue_observations"""
    ).fetchone()
    total = (skew["reliable"] or 0) + (skew["unreliable"] or 0)
    if total > 0:
        pct_reliable = 100.0 * (skew["reliable"] or 0) / total
        print(f"\nTimestamp skew:")
        print(f"  Reliable (<100ms):   {skew['reliable']:,} ({pct_reliable:.1f}%)")
        print(f"  Unreliable (>100ms): {skew['unreliable']:,} ({100-pct_reliable:.1f}%)")
        print(f"  Average skew: {skew['avg_skew'] or 0:.1f}ms")
        print(f"  Max skew:     {skew['max_skew'] or 0:.1f}ms")

    # Size breakdown -- naive vs realistic
    print("\n--- Realistic edge (skew-reliable + depth_ok, fees + oracle haircut) ---")
    print(f"{'Size':>8}  {'Reliable Obs':>13}  {'Net>0 count':>12}  {'Net>0 %':>9}  "
          f"{'Avg net':>10}  {'Best net':>10}")
    sql = """
        SELECT size_usd,
               COUNT(*) AS reliable_obs,
               SUM(CASE WHEN net_edge_usd > 0 THEN 1 ELSE 0 END) AS positive,
               AVG(net_edge_usd) AS avg_net,
               MAX(net_edge_usd) AS best
          FROM cross_venue_observations
         WHERE skew_unreliable = 0 AND depth_ok = 1 AND net_edge_usd IS NOT NULL
        GROUP BY size_usd
        ORDER BY size_usd
    """
    for r in conn.execute(sql):
        if only_size is not None and abs(r["size_usd"] - only_size) > 0.01:
            continue
        pct = 100.0 * r["positive"] / r["reliable_obs"] if r["reliable_obs"] else 0.0
        print(f"${r['size_usd']:>6.0f}  {r['reliable_obs']:>13,}  {r['positive']:>12,}  "
              f"{pct:>8.2f}%  ${r['avg_net'] or 0:>8.4f}  ${r['best'] or 0:>8.4f}")

    # Per-pair analysis
    print(f"\n--- Pairs with repeatable positive realistic edge (skew-reliable, min {min_obs} obs/size) ---")
    sql = """
        SELECT pair_key, asset, duration_class, size_usd,
               COUNT(*) AS n,
               SUM(CASE WHEN net_edge_usd > 0 THEN 1 ELSE 0 END) AS positive,
               AVG(net_edge_usd) AS avg_edge,
               MAX(net_edge_usd) AS best
          FROM cross_venue_observations
         WHERE skew_unreliable = 0 AND depth_ok = 1 AND net_edge_usd IS NOT NULL
        GROUP BY pair_key, size_usd
        HAVING n >= ? AND positive > 0
        ORDER BY avg_edge DESC
        LIMIT 30
    """
    rows = conn.execute(sql, (min_obs,)).fetchall()
    if not rows:
        print(f"  (none -- no cross-venue pair has >= {min_obs} reliable observations with positive edge)")
        print(f"  This is the EXPECTED honest result if cross-venue arbs are illusion.")
        return
    print(f"{'Asset':>5}  {'Dur':>4}  {'Size':>6}  {'Obs':>5}  {'Pos%':>5}  "
          f"{'Avg edge':>10}  {'Best':>8}  Pair")
    for r in rows:
        pct = 100.0 * r["positive"] / r["n"]
        print(f"{r['asset']:>5}  {r['duration_class'] or '?':>4}  ${r['size_usd']:>4.0f}  "
              f"{r['n']:>5}  {pct:>4.1f}%  ${r['avg_edge']:>8.4f}  ${r['best']:>6.4f}  "
              f"{(r['pair_key'] or '')[:60]}")


def report_naive_vs_realistic(conn) -> None:
    print("\n" + "=" * 78)
    print(" NAIVE vs REALISTIC -- how much edge is illusion?")
    print("=" * 78)
    row = conn.execute(
        """SELECT
               SUM(CASE WHEN naive_sum_top_asks < 0.985 THEN 1 ELSE 0 END) AS naive_pos,
               SUM(CASE WHEN realistic_sum_avg_asks < 0.985 AND depth_ok = 1 THEN 1 ELSE 0 END) AS realistic_pos,
               SUM(CASE WHEN net_edge_usd > 0 AND depth_ok = 1 THEN 1 ELSE 0 END) AS net_pos,
               COUNT(*) AS total
           FROM yes_no_observations"""
    ).fetchone()
    if row["total"] == 0:
        print("  (no observations yet)")
        return
    n = row["total"]
    print(f"\nOf {n:,} yes+no observations:")
    print(f"  Naive ' sum_top_asks < 0.985 ':                 {row['naive_pos']:,} "
          f"({100*row['naive_pos']/n:.2f}%)  <-- what fake-edge bots count")
    print(f"  Realistic 'walked sum < 0.985 with depth':      {row['realistic_pos']:,} "
          f"({100*row['realistic_pos']/n:.2f}%)")
    print(f"  Net positive edge after fees:                   {row['net_pos']:,} "
          f"({100*row['net_pos']/n:.2f}%)  <-- the only column that matters")
    if row['naive_pos'] > 0 and row['net_pos'] == 0:
        print(f"\n  CONCLUSION: 100% of apparent edge was illusion. Top-of-book is lying to you.")
    elif row['net_pos'] > 0:
        retention = 100.0 * row['net_pos'] / row['naive_pos'] if row['naive_pos'] else 0.0
        print(f"\n  {retention:.1f}% of naive opportunities survived realistic costing.")


def parse_args():
    p = argparse.ArgumentParser(description="Analyze edge observations")
    p.add_argument("--min-obs", type=int, default=10, help="Min observations per market/pair (default 10)")
    p.add_argument("--size", type=float, default=None, help="Filter to one size (e.g. 100)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    print(f"Edge analysis  |  generated at {datetime.now(tz=timezone.utc).isoformat()}")
    print(f"DB: {DB_PATH}")
    conn = connect()
    try:
        report_naive_vs_realistic(conn)
        report_yesno(conn, args.min_obs, args.size)
        report_cross(conn, args.min_obs, args.size)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
