# Post validation and coverage

## X drafts

Both runtimes use the same packaged `twitter-text` 3.1.0 parser and its v3 configuration: 280 weighted characters, 23 for recognized URLs, NFC normalization, and discounted recognized emoji sequences. This fixes CJK/emoji drafts that the earlier character counter incorrectly approved. Hashtags count against the budget; oversized tags are omitted whole. Truncation preserves recognized URL and emoji entities. Empty or failed drafts are not copyable in the Mac app; `tweet_chars: 281` with an empty `tweet` indicates validation unavailable to API consumers. Story ingestion continues even if draft validation fails.

The upstream library is versioned, not a live promise about future X rules or newly introduced emoji/TLDs. The packaged parser's recognized data governs the result. It does not grant posting authorization; all outputs remain drafts.

- X documentation: https://docs.x.com/fundamentals/counting-characters
- Upstream code/config: https://github.com/twitter/twitter-text/tree/v3.1.0
- 24 imported default-v3 validation cases, with attribution: `tests/fixtures/twitter-weighted.json`.
- Additional tests cover 150 CJK characters, family emoji, accents, URL preservation, oversized tags, Mac/Linux parity and concurrent Linux workers.

Mac uses the system JavaScriptCore framework. Linux uses a thread-confined QuickJS context with a 64 MiB memory limit and two-second execution limit. No Node service or network call is needed at runtime. Only pinned bundled code is evaluated; story text is passed as a function argument. See `tools/twitter-text/README.md` for reproducible generation, compatibility changes and licenses.

## Source coverage

`GET /v1/desks`, `GET /v1/status` and JSON exports include `coverage`:

```json
{"state":"partial","checked":2,"successful":1,"degraded":1,
 "sources":[{"source":"Example","provider":"direct","state":"success","detail":"","checked_at":"2026-11-27T12:00:00Z"}]}
```

The shortened example omits the second source row. Counts describe unique source labels within each provider, not article counts or the number of independent publishers. World Monitor is one aggregated digest check; its underlying feed warnings remain in the detail. Its complete/partial/stale/unavailable state is preserved (complete maps to success).

- `success`: every recorded source check completed and parsed. An empty valid RSS feed is a success, with zero stories.
- `partial`: successful or partial coverage exists alongside gaps.
- `stale`: only stale/unavailable coverage is available.
- `unavailable`: all recorded checks failed or provided no usable coverage.
- `unknown`: no source checks have been recorded.

**Coverage describes the last refresh, not a continuous uptime probe and not factual verification.** Read `checked_at` and `fetched_at` before treating it as current. A completed fetch job can have unavailable coverage: the refresh finished but the sources failed. `/health` only proves the local service is responding. The native Coverage window displays the check time, source results and counts. Persistent last-success history/backoff and snapshot-age policy are tracked separately on the release checklist.

Per-desk counts have matching meanings on both runtimes:

| Field | Meaning |
|---|---|
| `new` | Newly journaled stories in this refresh |
| `updated` | Changed/enriched existing stories in this refresh |
| `fresh` | `new + updated` |
| `carried` | Previously displayed stories retained without a new revision |
| `displayed` | `fresh + carried` |

One story may belong to multiple desks, so summing desk counts can double-count stories. Linux `/v1/status.fresh` excludes carried rows and `displayed` counts all snapshot rows. The native app's current fetch behavior replaces the batch, so carried is zero until restoration/carry-over is enabled.

Parser checks also reject missing weather observations instead of fabricating zero-valued forecasts, surface Hacker News item fetch failures, and reject invalid prediction-market/index payload shapes.
