# Runtime snapshots (auto-updated by VM sync)

Last sync: 2026-05-27T03:00:03Z

| File | What |
|------|------|
| Opus4.7-Trade.json | Opus bot paper-trade journal (JSON, native format) |
| GPT5.5-bot.sqlite3.sql | GPT5.5 paper-trade DB, sqlite .dump form (diffable text) |
| arb-bot-cross-logs.jsonl | last 2000 log lines from Opus container |
| arb-bot-gpt55-arbitrage-bot-1-logs.jsonl | last 2000 log lines from GPT5.5 container |
| Opus4.7-ticks.jsonl | only the phase1.tick_summary events (compact, easy to analyze) |
| GPT5.5-ticks.jsonl | same for GPT5.5 |
| ARB-BOT-Opus4.7-metrics.txt | Prometheus scrape at sync time |
| ARB-BOT-GPT5.5-metrics.txt | same for GPT5.5 |

Source: live containers on OCI VM. Git history retains every prior snapshot.
