"""Main logger. Reads pairs.json, polls both venues every POLL_INTERVAL seconds,
walks full orderbook depth at multiple notional sizes, writes raw observations
to SQLite.

NO filtering at logging time. Everything goes in. Analysis script decides cutoffs.

NO trading. Pure observation.

Self-test runs at startup; refuses to start if any test fails.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

from math_core import (
    BookLevel,
    SKEW_RELIABLE_THRESHOLD_MS,
    evaluate_cross_venue,
    evaluate_yes_no_complementarity,
)
import selftest

POLL_INTERVAL_SEC = float(os.environ.get("LOGGER_POLL_INTERVAL_SEC", "30"))
DISCOVERY_REFRESH_SEC = float(os.environ.get("DISCOVERY_REFRESH_SEC", "3600"))  # reload pairs.json hourly
SIZE_LADDER_USD = [float(s) for s in os.environ.get("SIZE_LADDER_USD", "25,100,500,1000").split(",")]
LIMITLESS_BASE = os.environ.get("LIMITLESS_BASE", "https://api.limitless.exchange")
POLYMARKET_CLOB = os.environ.get("POLYMARKET_CLOB", "https://clob.polymarket.com")
POLYMARKET_GAMMA = os.environ.get("POLYMARKET_GAMMA", "https://gamma-api.polymarket.com")

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "edge_observations.sqlite3"
PAIRS_PATH = DATA_DIR / "pairs.json"


# ---------- DB ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS run_meta (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_utc TEXT NOT NULL,
    poll_interval_sec REAL NOT NULL,
    size_ladder TEXT NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS yes_no_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at_utc TEXT NOT NULL,
    observed_ts_ns INTEGER NOT NULL,
    venue TEXT NOT NULL,
    asset TEXT,
    duration_class TEXT,
    market_key TEXT NOT NULL,
    raw_title TEXT,
    response_age_ms REAL,
    yes_response_ts_ns INTEGER,         -- when the YES book landed (Polymarket only — Limitless has 1 fetch)
    no_response_ts_ns INTEGER,          -- when the NO book landed (Polymarket only)
    intra_skew_ms REAL,                 -- |yes_ts - no_ts| in ms; NULL/0 for Limitless (derived NO has no skew)
    intra_skew_unreliable INTEGER,      -- 1 if intra_skew_ms > SKEW_RELIABLE_THRESHOLD_MS
    size_usd REAL NOT NULL,
    naive_sum_top_asks REAL,
    realistic_sum_avg_asks REAL,
    yes_avg_price REAL,
    no_avg_price REAL,
    yes_filled_shares REAL,
    no_filled_shares REAL,
    yes_depth_exhausted INTEGER,
    no_depth_exhausted INTEGER,
    depth_ok INTEGER,
    fees_yes_usd REAL,
    fees_no_usd REAL,
    fees_source TEXT,
    gross_edge_usd REAL,
    net_edge_usd REAL,
    edge_per_share_usd REAL
);

CREATE TABLE IF NOT EXISTS cross_venue_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at_utc TEXT NOT NULL,
    observed_ts_ns INTEGER NOT NULL,
    pair_key TEXT NOT NULL,
    asset TEXT,
    duration_class TEXT,
    venue_a TEXT,
    venue_b TEXT,
    a_response_ts_ns INTEGER,
    b_response_ts_ns INTEGER,
    skew_ms REAL,
    skew_unreliable INTEGER,
    size_usd REAL NOT NULL,
    naive_a_yes_top REAL,
    naive_b_no_top REAL,
    naive_sum REAL,
    realistic_sum REAL,
    a_avg_price REAL,
    b_avg_price REAL,
    a_filled_shares REAL,
    b_filled_shares REAL,
    a_depth_exhausted INTEGER,
    b_depth_exhausted INTEGER,
    depth_ok INTEGER,
    fees_a_usd REAL,
    fees_b_usd REAL,
    fees_source TEXT,
    oracle_haircut_usd REAL,
    gross_edge_usd REAL,
    net_edge_usd REAL,
    edge_per_share_usd REAL
);

CREATE INDEX IF NOT EXISTS ix_yesno_market_size ON yes_no_observations(market_key, size_usd);
CREATE INDEX IF NOT EXISTS ix_yesno_observed_at ON yes_no_observations(observed_at_utc);
CREATE INDEX IF NOT EXISTS ix_cross_pair_size ON cross_venue_observations(pair_key, size_usd);
CREATE INDEX IF NOT EXISTS ix_cross_observed_at ON cross_venue_observations(observed_at_utc);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at_utc TEXT NOT NULL,
    kind TEXT,
    market_key TEXT,
    detail TEXT
);
"""


def open_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.commit()
    return conn


@contextmanager
def db_session():
    conn = open_db()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def record_error(conn: sqlite3.Connection, kind: str, market_key: str, detail: str):
    conn.execute(
        "INSERT INTO errors (observed_at_utc, kind, market_key, detail) VALUES (?, ?, ?, ?)",
        (datetime.now(tz=timezone.utc).isoformat(), kind, market_key, detail[:500]),
    )


# ---------- Book fetchers ----------

async def _fetch_json(session: aiohttp.ClientSession, url: str, **kwargs):
    """Returns (body, response_ts_ns, age_ms_estimate). Age is None if unknown."""
    t_start = time.time_ns()
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10), **kwargs) as resp:
            t_end = time.time_ns()
            if resp.status != 200:
                return None, t_end, None
            body = await resp.json(content_type=None)
            # Date header for staleness estimate
            date_hdr = resp.headers.get("Date")
            age_ms = None
            if date_hdr:
                try:
                    from email.utils import parsedate_to_datetime
                    server_dt = parsedate_to_datetime(date_hdr)
                    server_ts_ns = int(server_dt.timestamp() * 1_000_000_000)
                    age_ms = max(0.0, (t_end - server_ts_ns) / 1_000_000.0)
                except Exception:
                    pass
            return body, t_end, age_ms
    except Exception:
        return None, time.time_ns(), None


def _parse_book(side_list, ascending: bool) -> list[BookLevel]:
    """Convert a list of {price, size} or [price, size] entries into BookLevel objects."""
    out: list[BookLevel] = []
    for entry in side_list or []:
        try:
            if isinstance(entry, dict):
                price = float(entry.get("price") or entry.get("p"))
                size = float(entry.get("size") or entry.get("s") or entry.get("amount") or 0.0)
            elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                price, size = float(entry[0]), float(entry[1])
            else:
                continue
            if price > 0 and size > 0:
                out.append(BookLevel(price=price, size=size))
        except (ValueError, TypeError, KeyError):
            continue
    out.sort(key=lambda x: x.price, reverse=not ascending)
    return out


async def fetch_limitless_book(session, market_slug: str):
    """Returns (yes_asks, no_asks, yes_bids, no_bids, response_ts_ns, age_ms) or None.

    Limitless `/markets/:slug/orderbook` returns ONLY the YES side as
    `{bids:[{price,size,side:'BUY'}...], asks:[{price,size,side:'SELL'}...], tokenId,...}`.
    We derive the NO orderbook by complement (CTF property):
        buying NO at price p == selling YES at price (1-p)
    So:
        no_asks (people selling NO) = yes_bids inverted, price -> (1-price), same size
        no_bids (people buying NO) = yes_asks inverted, same size

    `size` values are in collateral-base-units (USDC = 6 decimals on Limitless). Divide by 1e6.
    """
    url = f"{LIMITLESS_BASE}/markets/{market_slug}/orderbook"
    body, ts_ns, age_ms = await _fetch_json(session, url)
    if body is None or not isinstance(body, dict):
        return None

    raw_asks = body.get("asks") or []
    raw_bids = body.get("bids") or []
    if not raw_asks and not raw_bids:
        return None

    # Parse, normalizing size from base-units to shares (divide by 1e6).
    def _to_levels(raw, ascending: bool) -> list[BookLevel]:
        out: list[BookLevel] = []
        for entry in raw:
            try:
                price = float(entry.get("price"))
                size_units = float(entry.get("size") or 0)
                size_shares = size_units / 1_000_000.0   # USDC 6-decimal -> share units
                if price > 0 and size_shares > 0:
                    out.append(BookLevel(price=price, size=size_shares))
            except (ValueError, TypeError, AttributeError):
                continue
        out.sort(key=lambda x: x.price, reverse=not ascending)
        return out

    yes_asks = _to_levels(raw_asks, ascending=True)
    yes_bids = _to_levels(raw_bids, ascending=False)

    # Derive NO from YES via complement
    no_asks = [BookLevel(price=round(1.0 - b.price, 6), size=b.size) for b in yes_bids]
    no_asks.sort(key=lambda x: x.price)
    no_bids = [BookLevel(price=round(1.0 - a.price, 6), size=a.size) for a in yes_asks]
    no_bids.sort(key=lambda x: x.price, reverse=True)

    return yes_asks, no_asks, yes_bids, no_bids, ts_ns, age_ms


async def fetch_polymarket_book(session, token_id: str):
    """Returns (asks, bids, response_ts_ns, age_ms) for one outcome token."""
    url = f"{POLYMARKET_CLOB}/book"
    body, ts_ns, age_ms = await _fetch_json(session, url, params={"token_id": token_id})
    if body is None:
        return None
    asks = _parse_book(body.get("asks") or [], ascending=True)
    bids = _parse_book(body.get("bids") or [], ascending=False)
    if not asks and not bids:
        return None
    return asks, bids, ts_ns, age_ms


def _top(book_levels):
    return book_levels[0].price if book_levels else None


# ---------- Observation loop ----------

async def observe_limitless_yes_no(session, market: dict, conn: sqlite3.Connection):
    slug = market.get("slug")
    if not slug:
        return
    result = await fetch_limitless_book(session, slug)
    if result is None:
        record_error(conn, "fetch_limitless", slug, "no book returned")
        return
    yes_asks, no_asks, yes_bids, no_bids, ts_ns, age_ms = result
    if not yes_asks or not no_asks:
        record_error(conn, "thin_book", slug, f"yes_asks={len(yes_asks)} no_asks={len(no_asks)}")
        return

    now_iso = datetime.now(tz=timezone.utc).isoformat()
    for size in SIZE_LADDER_USD:
        obs = evaluate_yes_no_complementarity(
            venue="limitless",
            market_key=slug,
            yes_asks=yes_asks, no_asks=no_asks,
            size_usd=size,
            yes_top_ask=_top(yes_asks),
            no_top_ask=_top(no_asks),
            observation_ts_ns=ts_ns,
            response_age_ms=age_ms,
        )
        conn.execute(
            """INSERT INTO yes_no_observations (
                observed_at_utc, observed_ts_ns, venue, asset, duration_class,
                market_key, raw_title, response_age_ms,
                yes_response_ts_ns, no_response_ts_ns, intra_skew_ms, intra_skew_unreliable,
                size_usd,
                naive_sum_top_asks, realistic_sum_avg_asks,
                yes_avg_price, no_avg_price, yes_filled_shares, no_filled_shares,
                yes_depth_exhausted, no_depth_exhausted, depth_ok,
                fees_yes_usd, fees_no_usd, fees_source,
                gross_edge_usd, net_edge_usd, edge_per_share_usd
            ) VALUES (?, ?, ?, ?, ?,  ?, ?, ?,  ?, ?, ?, ?,  ?,  ?, ?,  ?, ?, ?, ?,  ?, ?, ?,  ?, ?, ?,  ?, ?, ?)""",
            (
                now_iso, ts_ns, "limitless", market.get("asset"), market.get("duration_class"),
                slug, market.get("raw_title"), age_ms,
                # Limitless: NO is derived mathematically from YES (CTF complement),
                # no second fetch, no possible skew. Record yes_ts; intra-skew = 0.
                ts_ns, ts_ns, 0.0, 0,
                size,
                obs.naive_sum_top_asks, obs.realistic_sum_avg_asks,
                obs.yes_walk.avg_price, obs.no_walk.avg_price,
                obs.yes_walk.filled_shares, obs.no_walk.filled_shares,
                int(obs.yes_walk.depth_exhausted), int(obs.no_walk.depth_exhausted), int(obs.depth_ok),
                obs.fees_yes_usd, obs.fees_no_usd, obs.fees_source,
                obs.gross_edge_usd, obs.net_edge_usd, obs.edge_per_share_usd,
            ),
        )


async def observe_polymarket_yes_no(session, market: dict, conn: sqlite3.Connection):
    yes_tok = market.get("yes_token_id")
    no_tok = market.get("no_token_id")
    if not (yes_tok and no_tok):
        return
    yes_res, no_res = await asyncio.gather(
        fetch_polymarket_book(session, yes_tok),
        fetch_polymarket_book(session, no_tok),
        return_exceptions=False,
    )
    if yes_res is None or no_res is None:
        return
    yes_asks, _, yes_ts, _ = yes_res
    no_asks, _, no_ts, _ = no_res
    if not yes_asks or not no_asks:
        return
    ts_ns = max(yes_ts, no_ts)
    intra_skew_ms = abs(yes_ts - no_ts) / 1_000_000.0
    intra_unreliable = intra_skew_ms > SKEW_RELIABLE_THRESHOLD_MS
    age_ms = None

    now_iso = datetime.now(tz=timezone.utc).isoformat()
    for size in SIZE_LADDER_USD:
        obs = evaluate_yes_no_complementarity(
            venue="polymarket",
            market_key=market.get("slug"),
            yes_asks=yes_asks, no_asks=no_asks, size_usd=size,
            yes_top_ask=_top(yes_asks), no_top_ask=_top(no_asks),
            observation_ts_ns=ts_ns,
            response_age_ms=age_ms,
        )
        conn.execute(
            """INSERT INTO yes_no_observations (
                observed_at_utc, observed_ts_ns, venue, asset, duration_class,
                market_key, raw_title, response_age_ms,
                yes_response_ts_ns, no_response_ts_ns, intra_skew_ms, intra_skew_unreliable,
                size_usd,
                naive_sum_top_asks, realistic_sum_avg_asks,
                yes_avg_price, no_avg_price, yes_filled_shares, no_filled_shares,
                yes_depth_exhausted, no_depth_exhausted, depth_ok,
                fees_yes_usd, fees_no_usd, fees_source,
                gross_edge_usd, net_edge_usd, edge_per_share_usd
            ) VALUES (?, ?, ?, ?, ?,  ?, ?, ?,  ?, ?, ?, ?,  ?,  ?, ?,  ?, ?, ?, ?,  ?, ?, ?,  ?, ?, ?,  ?, ?, ?)""",
            (
                now_iso, ts_ns, "polymarket", market.get("asset"), market.get("duration_class"),
                market.get("slug"), market.get("raw_title"), age_ms,
                yes_ts, no_ts, intra_skew_ms, int(intra_unreliable),
                size,
                obs.naive_sum_top_asks, obs.realistic_sum_avg_asks,
                obs.yes_walk.avg_price, obs.no_walk.avg_price,
                obs.yes_walk.filled_shares, obs.no_walk.filled_shares,
                int(obs.yes_walk.depth_exhausted), int(obs.no_walk.depth_exhausted), int(obs.depth_ok),
                obs.fees_yes_usd, obs.fees_no_usd, obs.fees_source,
                obs.gross_edge_usd, obs.net_edge_usd, obs.edge_per_share_usd,
            ),
        )


async def observe_cross_pair(session, pair: dict, conn: sqlite3.Connection):
    """Fetch BOTH venues simultaneously (asyncio.gather), then evaluate both directions
    (Lim-YES + Poly-NO; Poly-YES + Lim-NO) and record whichever has a chance of edge."""
    lim = pair["limitless"]
    poly = pair["polymarket"]
    lim_slug = lim.get("slug")
    poly_yes = poly.get("yes_token_id")
    poly_no = poly.get("no_token_id")
    if not (lim_slug and poly_yes and poly_no):
        return

    lim_task = fetch_limitless_book(session, lim_slug)
    poly_yes_task = fetch_polymarket_book(session, poly_yes)
    poly_no_task = fetch_polymarket_book(session, poly_no)
    lim_res, py_res, pn_res = await asyncio.gather(lim_task, poly_yes_task, poly_no_task,
                                                    return_exceptions=False)
    if lim_res is None or py_res is None or pn_res is None:
        return
    lim_yes_asks, lim_no_asks, _, _, lim_ts, _ = lim_res
    poly_yes_asks, _, py_ts, _ = py_res
    poly_no_asks, _, pn_ts, _ = pn_res

    now_iso = datetime.now(tz=timezone.utc).isoformat()
    pair_key = pair["pair_key"]

    # Direction 1: buy YES on Limitless, NO on Polymarket
    if lim_yes_asks and poly_no_asks:
        for size in SIZE_LADDER_USD:
            obs = evaluate_cross_venue(
                pair_key=pair_key + "|LIM_YES+POLY_NO",
                venue_a="limitless", venue_b="polymarket",
                a_yes_asks=lim_yes_asks, b_no_asks=poly_no_asks, size_usd=size,
                a_yes_top_ask=_top(lim_yes_asks), b_no_top_ask=_top(poly_no_asks),
                a_response_ts_ns=lim_ts, b_response_ts_ns=pn_ts,
            )
            _insert_cross(conn, pair, obs, "limitless", "polymarket", lim_ts, pn_ts, size, now_iso)

    # Direction 2: buy YES on Polymarket, NO on Limitless
    if poly_yes_asks and lim_no_asks:
        for size in SIZE_LADDER_USD:
            obs = evaluate_cross_venue(
                pair_key=pair_key + "|POLY_YES+LIM_NO",
                venue_a="polymarket", venue_b="limitless",
                a_yes_asks=poly_yes_asks, b_no_asks=lim_no_asks, size_usd=size,
                a_yes_top_ask=_top(poly_yes_asks), b_no_top_ask=_top(lim_no_asks),
                a_response_ts_ns=py_ts, b_response_ts_ns=lim_ts,
            )
            _insert_cross(conn, pair, obs, "polymarket", "limitless", py_ts, lim_ts, size, now_iso)


def _insert_cross(conn, pair, obs, venue_a, venue_b, a_ts, b_ts, size, now_iso):
    conn.execute(
        """INSERT INTO cross_venue_observations (
            observed_at_utc, observed_ts_ns, pair_key, asset, duration_class,
            venue_a, venue_b, a_response_ts_ns, b_response_ts_ns, skew_ms, skew_unreliable,
            size_usd, naive_a_yes_top, naive_b_no_top, naive_sum, realistic_sum,
            a_avg_price, b_avg_price, a_filled_shares, b_filled_shares,
            a_depth_exhausted, b_depth_exhausted, depth_ok,
            fees_a_usd, fees_b_usd, fees_source, oracle_haircut_usd,
            gross_edge_usd, net_edge_usd, edge_per_share_usd
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,  ?, ?, ?, ?,  ?, ?, ?, ?,  ?, ?, ?,  ?, ?, ?, ?,  ?, ?, ?)""",
        (
            now_iso, max(a_ts, b_ts), obs.pair_key, pair["asset"], pair["duration_class"],
            venue_a, venue_b, a_ts, b_ts, obs.skew_ms, int(obs.skew_unreliable),
            size, obs.naive_a_yes_top, obs.naive_b_no_top, obs.naive_sum, obs.realistic_sum,
            obs.a_yes_walk.avg_price, obs.b_no_walk.avg_price,
            obs.a_yes_walk.filled_shares, obs.b_no_walk.filled_shares,
            int(obs.a_yes_walk.depth_exhausted), int(obs.b_no_walk.depth_exhausted), int(obs.depth_ok),
            obs.fees_a_usd, obs.fees_b_usd, obs.fees_source, obs.oracle_haircut_usd,
            obs.gross_edge_usd, obs.net_edge_usd, obs.edge_per_share_usd,
        ),
    )


# ---------- Main loop ----------

class State:
    def __init__(self):
        self.pairs_payload: dict | None = None
        self.pairs_loaded_at: float = 0.0
        self.stop = asyncio.Event()


def load_pairs() -> dict | None:
    if not PAIRS_PATH.exists():
        return None
    try:
        with open(PAIRS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[logger] failed to load pairs.json: {e}")
        return None


async def main_loop():
    state = State()

    # Self-test before doing anything
    print("[logger] running self-test...")
    rc = selftest.run()
    if rc != 0:
        print("[logger] self-test FAILED; refusing to start", file=sys.stderr)
        return 1
    print("[logger] self-test passed; starting observation loop")

    # Signal handlers
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, state.stop.set)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda *_: state.stop.set())

    while not state.stop.is_set():
        # Reload pairs if needed
        if state.pairs_payload is None or (time.time() - state.pairs_loaded_at) > DISCOVERY_REFRESH_SEC:
            state.pairs_payload = load_pairs()
            state.pairs_loaded_at = time.time()
            if state.pairs_payload:
                c = state.pairs_payload.get("counts", {})
                print(f"[logger] reloaded pairs: lim_yes_no={c.get('limitless_yes_no_candidates')}, "
                      f"poly_yes_no={c.get('polymarket_yes_no_candidates')}, "
                      f"cross={c.get('cross_venue_pairs')}")

        if state.pairs_payload is None:
            print("[logger] no pairs.json yet — waiting 30s. Run discover.py.")
            try:
                await asyncio.wait_for(state.stop.wait(), timeout=30)
            except asyncio.TimeoutError:
                continue
            else:
                break

        lim_markets = state.pairs_payload.get("limitless_yes_no_candidates", [])
        poly_markets = state.pairs_payload.get("polymarket_yes_no_candidates", [])
        cross_pairs = state.pairs_payload.get("cross_venue_pairs", [])

        cycle_start = time.time()
        connector = aiohttp.TCPConnector(limit=10)
        async with aiohttp.ClientSession(connector=connector) as session:
            with db_session() as conn:
                # Concurrency: 10 in-flight requests total.
                sem = asyncio.Semaphore(10)

                async def _guard(coro):
                    async with sem:
                        return await coro

                tasks = []
                for m in lim_markets:
                    tasks.append(_guard(observe_limitless_yes_no(session, m, conn)))
                for m in poly_markets:
                    tasks.append(_guard(observe_polymarket_yes_no(session, m, conn)))
                for p in cross_pairs:
                    tasks.append(_guard(observe_cross_pair(session, p, conn)))

                if tasks:
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    errors = sum(1 for r in results if isinstance(r, Exception))
                    elapsed = time.time() - cycle_start
                    print(f"[logger] cycle done: {len(tasks)} markets observed in {elapsed:.1f}s, "
                          f"{errors} errors, db={DB_PATH.stat().st_size//1024} KB")
                else:
                    print("[logger] empty universe — nothing to observe yet")

        try:
            await asyncio.wait_for(state.stop.wait(), timeout=POLL_INTERVAL_SEC)
        except asyncio.TimeoutError:
            continue
        else:
            break

    print("[logger] shutdown clean")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_loop()))
