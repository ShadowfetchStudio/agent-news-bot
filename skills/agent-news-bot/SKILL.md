---
name: agent-news-bot
description: Pull only fresh, unseen news from the local Agent News Bot Mac app (RSS, AI, HN, Polymarket, markets, weather) over localhost HTTP.
version: 1.1.0
---

# Agent News Bot

Agent News Bot is a native macOS newsroom. It keeps a local SQLite freshness ledger (with 30-day retention, `ETag`/`If-Modified-Since` RSS caching, and cross-desk fuzzy title deduplication) and only serves stories that have not been shown before and are inside the lookback window.

On launch, the app installs a skill descriptor to `~/.hermes/skills/agent-news-bot/SKILL.md` and `~/.agents/skills/agent-news-bot/SKILL.md` referencing the `0600` token file (never embedding the raw token in plaintext):

```text
~/Library/Application Support/AgentNewsBot/bridge.token
```

## Default endpoints

- `GET http://127.0.0.1:18765/health` (no auth)
- `GET http://127.0.0.1:18765/v1/fresh?desk=<desk>&limit=<n>`
- `GET http://127.0.0.1:18765/v1/articles/<id>`
- `GET http://127.0.0.1:18765/v1/desks`
- `GET http://127.0.0.1:18765/v1/status`
- `POST http://127.0.0.1:18765/v1/fetch`

Header: `Authorization: Bearer $(cat ~/Library/Application\ Support/AgentNewsBot/bridge.token)` (or `X-Agent-Token`).

Do not invent URLs. Use `tweet` and `hashtags` when drafting social posts. Never print the bearer token in public channels.
