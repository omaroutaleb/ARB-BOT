# ARB-BOT Audit Brief

> **For:** Any LLM coding agent (Claude Code, Codex, etc.) starting a fresh session.
> **How to use:** Paste this file as your first message in a new session. It contains
> everything you need — mission, repo layout, infra access, data conventions, audit
> checklist, output format. Do not ask the user for context this document doesn't already cover.

---

## 0. TL;DR — what this is

Two prediction-market arbitrage bots run side-by-side on an OCI ARM64 VM:
- **`ARB-BOT-Opus4.7/`** — built from Claude Opus 4.7 deep research
- **`ARB-BOT-GPT5.5/`** — built from GPT-5.5 deep research

Both target the same market (Polymarket × Limitless crypto markets, $500 bankroll). Both run in `DRY_RUN=true` paper-trade mode, scanning live order books and recording would-be fills to disk. The VM auto-syncs to this GitHub repo hourly (code one way, runtime data the other) so you can audit from the GitHub clone alone.

**Your mission:** find bugs that cause **false trades** (executed when shouldn't be, or wrong PnL) or **inefficiency** (missed opportunities, silent failures). You are auditing exactly ONE of the two bots; the user will tell you which. The other model is auditing the other bot in parallel — your reports get diffed, disagreements get human review.

---

## 1. Repo layout

```
ARB-BOT/
├── README.md                      # repo guide + patch summary for GPT5.5
├── AUDIT_PROMPT.md                # this file
├── .gitignore                     # blocks secrets / venv / state from being committed
│
├── ARB-BOT-Opus4.7/               # Bot #1 — Opus 4.7 design
│   ├── README.md
│   ├── STRATEGY_SYNTHESIS.md      # ★ the contract — any code that violates this is a bug
│   ├── Dockerfile, docker-compose.yml, pyproject.toml, .env.example
│   ├── research/                  # the two source research reports
│   ├── src/
│   │   ├── main.py                # entry point, phase gate, signal handlers
│   │   ├── config.py              # pydantic-settings
│   │   ├── platforms/
│   │   │   ├── polymarket/        # REST + WS + EIP-712 + geoblock + heartbeat
│   │   │   └── limitless/         # REST + Socket.IO + EIP-712
│   │   ├── strategies/
│   │   │   ├── yes_no_complementarity.py  # ★ Phase 1, currently the only active strategy
│   │   │   ├── maker_rebate.py
│   │   │   └── cross_venue.py
│   │   ├── matching/              # canonical rule signature + pair finder
│   │   ├── risk/                  # limits, orphan policy, kill switch
│   │   ├── state/                 # Trade.json store (JSON, atomic write-via-temp-rename)
│   │   ├── fees/                  # runtime fee math — never hard-coded
│   │   ├── oracles/               # UMA/Chainlink/Pyth compatibility matrix
│   │   └── observability/         # structlog + prometheus
│   ├── tests/                     # pytest, ≥80% coverage on risk + fees
│   └── scripts/
│       ├── validate_endpoints.py
│       └── dry_run.py
│
├── ARB-BOT-GPT5.5/                # Bot #2 — GPT-5.5 design (similar structure)
│   ├── STRATEGY_SYNTHESIS.md
│   ├── src/
│   │   ├── strategies/yes_no_complementarity.py  # ★ active Phase 1
│   │   ├── state/db.py            # uses SQLite instead of JSON
│   │   └── ...
│   └── ...
│
└── runtime/                       # ★ auto-populated by VM sync (hourly), gitignored from per-bot trees
    ├── README.md                   # what each file is
    ├── Opus4.7-Trade.json         # ★ Opus paper-trade journal (the bot's native format)
    ├── GPT5.5-bot.sqlite3.sql     # ★ GPT5.5 trades — sqlite dump as text for diffability
    ├── arb-bot-cross-logs.jsonl   # last 2000 log lines from Opus container
    ├── arb-bot-gpt55-arbitrage-bot-1-logs.jsonl  # same for GPT5.5
    ├── Opus4.7-ticks.jsonl        # ★ extracted phase1.tick_summary events only (compact)
    ├── GPT5.5-ticks.jsonl         # same for GPT5.5
    ├── ARB-BOT-Opus4.7-metrics.txt  # Prometheus scrape at sync time
    └── ARB-BOT-GPT5.5-metrics.txt
```

★ = critical file. Read this before forming any opinion.

---

## 2. Live infrastructure (OCI VM)

You can audit from the GitHub clone alone (the `runtime/` directory is updated hourly by the VM). **Only SSH if the user explicitly says you may** — the VM is owned by the user, hosting unrelated work too.

If SSH is permitted:

| | |
|---|---|
| Host | `ubuntu@84.8.222.224` |
| OS | Ubuntu 22.04 ARM64 (Oracle Ampere A1) |
| SSH key (user-side, Windows) | `C:\Users\oouta\Downloads\ssh-key-2026-04-25.key` |
| Bot directories | `~/ARB-BOT-Opus4.7/` and `~/ARB-BOT-GPT5.5/` |
| Git checkout | `~/ARB-BOT-repo/` (cloned via `git@github-arbbot:omaroutaleb/ARB-BOT.git` deploy-key alias) |
| Sync script | `~/sync.sh` runs hourly via cron, logs to `~/sync.log` |

Read-only commands (always safe):
```bash
ssh -i ~/Downloads/ssh-key-2026-04-25.key ubuntu@84.8.222.224 'sudo docker ps'
ssh -i ~/Downloads/ssh-key-2026-04-25.key ubuntu@84.8.222.224 'sudo docker logs arb-bot-cross --tail 100'
ssh -i ~/Downloads/ssh-key-2026-04-25.key ubuntu@84.8.222.224 'sudo docker logs arb-bot-gpt55-arbitrage-bot-1 --tail 100'
ssh -i ~/Downloads/ssh-key-2026-04-25.key ubuntu@84.8.222.224 'cat ~/ARB-BOT-Opus4.7/state/Trade.json'
ssh -i ~/Downloads/ssh-key-2026-04-25.key ubuntu@84.8.222.224 'sudo sqlite3 ~/ARB-BOT-GPT5.5/data/bot.sqlite3 "select * from trades limit 20"'
ssh -i ~/Downloads/ssh-key-2026-04-25.key ubuntu@84.8.222.224 'tail -50 ~/sync.log'
```

Force a fresh sync (also safe — runs the same script cron does):
```bash
ssh -i ~/Downloads/ssh-key-2026-04-25.key ubuntu@84.8.222.224 '~/sync.sh'
```

**Do NOT modify `.env`, restart containers, kill processes, or touch state files on the VM** unless the user has explicitly asked for that action.

---

## 3. Data flow (so you understand what you're seeing)

```
                    ┌─────────────────────────────────────────────────┐
                    │           GitHub (omaroutaleb/ARB-BOT)          │
                    │                                                  │
                    │  ARB-BOT-Opus4.7/       runtime/Opus4.7-Trade   │
                    │  ARB-BOT-GPT5.5/        runtime/GPT5.5-bot.sql  │
                    │  AUDIT_PROMPT.md        runtime/<bot>-logs      │
                    │                         runtime/<bot>-ticks     │
                    │                         runtime/<bot>-metrics   │
                    └────────────┬─────────────▲──────────────────────┘
                                 │             │
                       git pull  │             │  git push (hourly cron)
                       (when     │             │
                       sync.sh   │             │
                       runs)     ▼             │
                    ┌─────────────────────────────────────────────────┐
                    │    OCI VM   (~/ARB-BOT-repo/  ←  canonical)     │
                    │                                                  │
                    │    Each hour at :00, ~/sync.sh:                 │
                    │     1. git fetch + reset to origin/main         │
                    │     2. rsync code into running bot dirs         │
                    │     3. docker compose up -d --build (if needed) │
                    │     4. dump Trade.json / sqlite into runtime/   │
                    │     5. dump docker logs into runtime/           │
                    │     6. commit + push                            │
                    └─────────────────────────────────────────────────┘
                                 │             ▲
                       runs the  │             │  writes to mounted
                       bots via  │             │  volumes (instant)
                       docker    ▼             │
                    ┌─────────────────────────────────────────────────┐
                    │  Containers: arb-bot-cross, arb-bot-gpt55-...   │
                    │  Bind mounts: state/ ↔ /app/state               │
                    │               data/  ↔ /app/data                │
                    └─────────────────────────────────────────────────┘
```

**Implications:**
- **Code changes you make on GitHub land on the VM within the hour** (or sooner if the user manually runs `~/sync.sh`).
- **Runtime data in `runtime/` is up to an hour stale.** If a fresher snapshot is critical, ask the user to run `~/sync.sh`.
- **State files (`Trade.json`, `bot.sqlite3`) are written in real-time by the bots** (via docker bind-mount), so the file on the VM is always current. The GitHub copy lags up to one sync cycle.
- The bots run in `DRY_RUN=true`. Any "fills" in `runtime/Opus4.7-Trade.json` or in the GPT5.5 `trades` table are simulated, not real exchange orders.

---

## 4. Ground truth — read these before any opinion

| File | What it tells you |
|---|---|
| `<bot>/STRATEGY_SYNTHESIS.md` | The CONTRACT. Reconciles both research reports; every code decision should trace back here. Any code that contradicts it is a bug. |
| `<bot>/research/Opus4.7-Deepresearch.md` | Opus's strategy synthesis (raw) |
| `<bot>/research/GPT5.5-Deepresearch.md` | GPT5.5's strategy synthesis (raw) |
| `<bot>/src/strategies/yes_no_complementarity.py` | The ONLY active strategy in Phase 1 (current config) |
| `runtime/<bot>-ticks.jsonl` | Per-scan summaries. Each line is a tick: `universe_size`, `evaluated`, `crossed_threshold`, `best_sum_asks`. This is your behavioral evidence. |
| `runtime/<bot>-logs.jsonl` | Full structured logs. Grep for `paper_trade_closed`, `opportunity`, `risk_blocked`, `market_missing_fields`, anything ending in `_failed`. |
| `runtime/Opus4.7-Trade.json` | Opus paper-trade journal. `arbs: {}` means no trades fired yet; rows appear when threshold crosses. |
| `runtime/GPT5.5-bot.sqlite3.sql` | GPT5.5 paper-trade DB. Look at `trades`, `positions`, `reconciliations` tables. |

---

## 5. Hard rules — non-negotiable

1. **Cite `path/to/file.py:LINE` for every finding.** A finding without a citation is rejected.
2. **Three confidence levels only:**
   - `CONFIRMED` — you read the code, and you found matching evidence in `runtime/` (a log line, a trade row, a metric value).
   - `SUSPECTED` — you read the code path and it's wrong, but no observed bad behavior in `runtime/` to confirm.
   - `NEEDS_VERIFICATION` — the code path depends on runtime metadata (market detail response shape, fee schedule) you can't observe from the repo alone.
3. **Do not invent endpoints, response shapes, or library behaviors** you haven't actually seen in this code, in `runtime/` data, or in the official Polymarket / Limitless docs cited in `research/`.
4. **Compare logs/state to code.** If the logs say `universe_size=29` and the code can only produce `≤20`, that's a finding. The runtime data is ground truth.
5. **No cosmetic improvements.** If something is ugly but correct, ignore it.
6. **No new dependencies in fixes.** Minimal diffs only.
7. **Do not propose changes that violate `STRATEGY_SYNTHESIS.md`** unless you can explain why the contract itself is wrong (cite section).

---

## 6. Audit checklist — work through every item

For each item, write a `CONFIRMED` / `SUSPECTED` / `NOT FOUND` / `N/A` entry. Skipping items is not allowed. `N/A` requires a one-line justification (e.g., "this bot doesn't use websockets in Phase 1").

### 6.1 Phase-1 strategy correctness
1. Does the universe-build step include any market the strategy can't actually execute on (AMM markets, manual-resolution markets, markets missing `tokens.yes/no` or `positionIds`)?
2. Does the bot skip markets with **zero-priced asks** (no liquidity)? (Zero ask = book is empty on that side.)
3. Does the bot require BOTH YES and NO asks to be non-None and non-zero, or can a `None`/`0` slip through?
4. Is `sum_asks` computed from **market-buy prices** (executable) or from `midpoint` / `lastTradePrice` (not fillable)?
5. Is `PHASE1_EDGE_THRESHOLD` interpreted as `<` or `<=`? Off-by-epsilon at the boundary?
6. Are markets pre-filtered by **asset/ticker** before per-market detail fetches, or does the bot waste API budget fetching detail for irrelevant markets?
7. If a market's metadata has `tokens.yes/no` but the code assumes `positionIds: [yes, no]` (or vice versa), the wrong side gets bought — find this.
8. Is `venue.exchange` pulled per-market at runtime, or does the code hard-code an address?

### 6.2 Fee math
9. Does `calculate_fee` / `polymarket_taker_fee` / `limitless_taker_fee` pull `feeRateBps` / `feeSchedule` from market metadata, or is the rate hard-coded?
10. Is `maker fee == 0`, `taker fee != 0`? Reversed anywhere?
11. Are fees applied to **both legs** when computing net edge?
12. Is the fee rate stored as fraction (`0.018`), basis-points (`18`), or percentage (`1.8`)? Used consistently across all call sites?
13. Are fees computed against `shares * price` (notional) or against `shares` alone?
14. If `feeRateBps` is missing from market metadata, does the bot skip the market (correct) or substitute zero / a hard-coded fallback (silent false trade)?

### 6.3 Dry-run / paper-trade correctness
15. In dry-run, what price is used as the "fill price" — the ask (correct, honest), the midpoint (wrong, over-optimistic), or the bid (wrong, under-optimistic)?
16. Does dry-run write the same fields a live fill would write (`avg_price`, `filled_size`, `fees_paid`, `closed_at_utc`, etc.)?
17. Does the dry-run path **also exercise** the risk gate, fee calculator, and EIP-712 signing? Or does it short-circuit before them, masking real bugs?
18. After a paper trade is recorded, does the bot **close the arb** (`closed_at_utc` set) and update bankroll/PnL? Or does it leave open arbs accumulating forever?
19. Does dry-run accidentally hit a live endpoint that requires auth (e.g., `/portfolio/profile`)? If yes, does it fail closed or fail open?

### 6.4 Risk and reconciliation
20. Are `MAX_POSITION_USD`, `MAX_CONCURRENT_ARBS`, `MAX_PLATFORM_EXPOSURE_USD` actually checked BEFORE order submission (not after)?
21. Is the phase gate (Phase 2/3 require N closed arbs with ≥X% win rate) read from **persisted state**? Or from in-memory counters that reset on restart?
22. Does the daily-loss-stop reset at UTC midnight, or accumulate forever?
23. Does the kill-switch path handle exceptions during cancel-all, or can a failure on one venue leave the other venue's orders alive?
24. On startup, does the bot reconcile persisted state against live exchange state (open orders / positions)? Critical for crash recovery.

### 6.5 Data freshness
25. Does the strategy trade on data from REST polling, WebSocket, or both? REST data is stale.
26. If WebSocket, does the bot **re-subscribe on reconnect** (full union, not incremental)?
27. Is there a **5-second heartbeat** task for Polymarket? Without it Polymarket auto-cancels all orders after ~15s.
28. Does the bot **check WS liveness** before trading on cached state?

### 6.6 State / persistence
29. Are state writes **atomic** (temp-file + rename) or in-place? In-place is a corruption risk.
30. Is concurrent access serialized (asyncio.Lock or similar)?
31. After each paper trade, is bankroll / cumulative PnL / peak equity updated?
32. If `Trade.json` / `bot.sqlite3` is corrupted on startup, does the bot detect and refuse to run, or silently start fresh?

### 6.7 Logging / observability
33. Does **every tick** emit a summary log at INFO level? A silent bot is undetectable.
34. Are secrets (private keys, API keys, HMAC signatures, signed payloads) redacted in logs? Grep `runtime/<bot>-logs.jsonl` for things that look like secrets.
35. Are Prometheus counters/gauges incremented in dry-run, or only on live submission? (Decide if that's intentional given the metric's name.)
36. Search for `except.*: pass`, `except.*: continue`, bare `except:` — silent exception swallowing.

### 6.8 Config / env handling
37. Are `bool` env vars (`DRY_RUN`, `VALIDATE_ENDPOINTS_ON_START`) parsed correctly? Pydantic accepts "true"/"false"; raw `bool(str)` does not.
38. Are `int` env vars that map to `Literal[...]` types coerced from strings? Pydantic Literal does NOT coerce by default.
39. Are any mainnet addresses, RPC URLs, or chain IDs hard-coded where they should be env-configurable?
40. Does `docker compose restart` re-read `.env`? (It doesn't. `up -d` does. Find any docs/scripts that imply otherwise.)

### 6.9 Cross-check logs ↔ state ↔ code
41. Pick the most recent `phase1.tick_summary` (Opus) / `phase1_tick_summary` (GPT5.5) line from `runtime/<bot>-ticks.jsonl`. Do `universe_size`, `evaluated`, `crossed_threshold` add up given the code path? Where did `universe_size - evaluated` markets go? Is that gap logged?
42. Pick a trade row from `runtime/Opus4.7-Trade.json` or `runtime/GPT5.5-bot.sqlite3.sql` (or note "no trades yet"). Does `realized_pnl_usd == (1.0 - sum_asks) * shares - fees` to the cent?
43. Duplicate `arb_id` entries? Race condition or retry without idempotency.
44. `filled_size > intended_size` anywhere? Order accounting bug.
45. `closed_at_utc < submitted_at_utc` anywhere? Clock skew / ordering bug.

### 6.10 Concurrency / lifecycle
46. Can two strategy ticks overlap (one tick still in `_execute` when the next fires)? Can they both target the same opportunity?
47. On SIGTERM, does the bot wait for in-flight orders to drain before exit, or are orders left orphaned?

### 6.11 Coexistence (two bots, one VM)
48. Do both bots target overlapping Limitless markets? Are they double-eating opportunities or rate-limit-throttling each other?
49. Are container names / ports / state paths distinct between the two bots' compose files?

---

## 7. Required output

A single Markdown document with two sections:

### Section A — Findings table

```
| # | Severity   | Confidence         | File:line               | Summary (1 sentence) | Why it causes false trade or inefficiency | Evidence (log line / data row / metric) |
|---|------------|--------------------|-------------------------|---------------------|------------------------------------------|----------------------------------------|
| 1 | Critical   | CONFIRMED          | src/foo.py:42           | …                   | …                                        | runtime/<bot>-ticks.jsonl line 17      |
```

Severity scale:
- **Critical** — bot will execute a trade that loses money in expectation (wrong fee, wrong side, wrong price)
- **High** — bot can silently miss valid opportunities or persist incorrect state
- **Medium** — bot is observable but wastes API budget / produces noise
- **Low** — robustness improvement, no behavior bug

### Section B — Categories cleared

```
| Section | Items checked | Result |
|---------|---------------|--------|
| 6.1 Phase-1 correctness | 1, 2, 3, 4, 5, 6, 7, 8 | All NOT FOUND except #2 (see finding 1 in section A) |
| 6.2 Fee math | 9–14 | All NOT FOUND |
…
```

This section is for the next reviewer to skip what you've covered.

### Single-line summary at the top of the document:

```
HIGHEST SEVERITY: Critical (3 findings) | High (5) | Medium (2) | Low (1)
```

Or: `HIGHEST SEVERITY: none found`.

---

## 8. The cross-model adversarial workflow

The user runs TWO sessions in parallel:

- **Claude Code** audits `ARB-BOT-GPT5.5/`
- **Codex** audits `ARB-BOT-Opus4.7/`

(Or vice versa. The point is each model audits the OTHER model's bot.)

Their two reports are then diffed:

| What | Means |
|---|---|
| Both reports cite the same bug | High confidence — likely real, fix it |
| Only one report cites it | Either a real bug the other missed, OR a hallucination — needs human spot-check on cited line |
| Both clear the same category | Lower confidence than it feels — re-check by running the test suite or adding one |

**This is why citations matter.** If you say "the fee calculator is wrong" without a `file:line`, no one can diff your report against the other model's report. Citations are the diff key.

---

## 9. Constraints on fixes you propose

If you propose a fix, it must:

1. **Touch only files cited in the finding.** No opportunistic refactors.
2. **Be a minimal diff.** Adding 5 lines is better than rewriting a function.
3. **Not add new dependencies.** Both `pyproject.toml` files are pinned.
4. **Include a before/after behavior contrast in the diff comment.**
5. **Preserve existing tests.** If a test would break, the test is asserting the buggy behavior — flag this separately.

If you can't write a minimal fix, leave the finding as-is. The user will decide.

---

## 10. What "done" looks like

Your audit is complete when:

- Findings table has at least one row per checklist item that produced a finding
- Categories-cleared table covers every checklist section
- Every finding cites `file:line`
- Every `CONFIRMED` finding cites evidence in `runtime/`
- A one-line summary at the top declares the highest severity found
- No preamble, no codebase summary, no explanation of prediction markets. Just the report.

---

## 11. Boundary conditions (things NOT to do)

- **Do not modify production state on the VM.** No restarts, no `.env` edits, no killing processes unless the user explicitly asks.
- **Do not push directly to GitHub `main`.** Open a PR if you have a fix; the user reviews before merge.
- **Do not include the SSH key contents in your report.** The path is fine to reference. The bytes are not.
- **Do not deduplicate against the other model's report.** You don't have their report. The diff happens after both reports are submitted.
- **Do not propose architectural rewrites.** "Switch from JSON to SQLite" is out of scope.
- **Do not add tests in your fix unless the existing test suite already covers the area.** Test additions go in a separate task.
- **Do not run `git push`, `docker compose down`, or anything destructive** without the user's express confirmation.

---

## 12. Quick-reference commands

```bash
# Audit from the GitHub clone (no SSH needed):
git clone https://github.com/omaroutaleb/ARB-BOT.git && cd ARB-BOT

# Read the contract:
cat ARB-BOT-Opus4.7/STRATEGY_SYNTHESIS.md   # or ARB-BOT-GPT5.5/

# Read current paper-trade state:
cat runtime/Opus4.7-Trade.json
cat runtime/GPT5.5-bot.sqlite3.sql

# Read recent ticks (one line per scan):
tail -20 runtime/Opus4.7-ticks.jsonl
tail -20 runtime/GPT5.5-ticks.jsonl

# Read full log tails:
tail -100 runtime/arb-bot-cross-logs.jsonl
tail -100 runtime/arb-bot-gpt55-arbitrage-bot-1-logs.jsonl

# When was the last sync?
git log -1 --format='%ci %s' -- runtime/

# What's about to be reviewed (if you're working from a branch):
git diff main -- ARB-BOT-Opus4.7/src/
```

If you have SSH permission and need fresher state than `runtime/` shows:
```bash
ssh -i ~/Downloads/ssh-key-2026-04-25.key ubuntu@84.8.222.224 '~/sync.sh'
git pull origin main
```

---

## 13. One final note

The user has explicitly built this audit loop to **reduce LLM hallucination**, not eliminate it. Your contribution to that goal is:

- Citing line numbers means hallucinations become falsifiable
- Marking confidence levels means uncertainty becomes legible
- Grounding findings in `runtime/` data means "the code path is wrong" becomes "the code path produced this wrong observable behavior on this line of this log"

Treat every claim you make as something that will be diffed against another model's claims and then checked by a human. Be precise. When unsure, say so.
