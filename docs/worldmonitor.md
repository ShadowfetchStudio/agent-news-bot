# World Monitor integration — first version

The optional connector ingests World Monitor's English feed digest into the existing desks. It
retains source links, snippets, publication dates, category, location, provider assessments and
coverage state. New and updated story groups are written to a durable SQLite revision log that
agents can consume independently. Existing sources work when the connector is disabled or fails.
This feature does not install or start a World Monitor server, subscribe to a plan, or publish posts.

## Configuration

**macOS:** build the app with `./build.sh --arch arm64`, open the built bundle, then Settings →
World Monitor. Enable the connector, set the server origin and edition, and enter a key if the
server requires one. Closing Settings saves the key to Keychain and the other settings to
UserDefaults. Fetch Fresh performs a digest request. Source links and revision labels are visible
in the story inspector. JSON exports and both bridge APIs contain the source groups.

**Linux:** add these environment settings to the service's private environment file:

```ini
AGENT_NEWSBOT_WORLDMONITOR_ENABLED=true
AGENT_NEWSBOT_WORLDMONITOR_URL=http://127.0.0.1:3000
AGENT_NEWSBOT_WORLDMONITOR_VARIANT=tech
# Set AGENT_NEWSBOT_WORLDMONITOR_KEY privately if the endpoint requires a key.
```

The default is disabled. Variants: `full`, `tech`, `finance`, `happy`, `commodity`, `energy`.
The URL must be an origin with no credentials, path, query or fragment. HTTPS is required except
for localhost, 127.0.0.1 or ::1. For another computer, use HTTPS or an SSH tunnel to loopback.
No keys are embedded in query strings or sent through redirects. Provider errors are redacted.
Responses are bounded to 4 MiB. Each refresh makes one request with no automatic retry; a 429
is surfaced as a warning and normal sources continue. Keep an interval appropriate for your plan.

The connector calls `GET /api/news/v1/list-feed-digest?variant=tech&lang=en`, optionally with an
`X-WorldMonitor-Key` header. Hosted origin: `https://api.worldmonitor.app`; use only with the
appropriate access/usage rights. Self-hosting requires your own feed ingestion and provider keys.

## Source contract and licensing

The wire format was inspected at upstream commit `a12f5bb0a9c551410417692d8e8afa30979ae05b`:

- [Service endpoint](https://github.com/koala73/worldmonitor/blob/a12f5bb0a9c551410417692d8e8afa30979ae05b/proto/worldmonitor/news/v1/service.proto)
- [Digest response](https://github.com/koala73/worldmonitor/blob/a12f5bb0a9c551410417692d8e8afa30979ae05b/proto/worldmonitor/news/v1/list_feed_digest.proto)
- [News item fields](https://github.com/koala73/worldmonitor/blob/a12f5bb0a9c551410417692d8e8afa30979ae05b/proto/worldmonitor/news/v1/news_item.proto)

The REST JSON uses camelCase and publication timestamps in epoch milliseconds. Missing/invalid
dates, unsafe links and items outside the lookback (including dates more than five minutes in the
future) are skipped. Unknown categories fall back to `world`; technology maps to `tech`, finance
to `business`. In macOS, disabled desks remain disabled. Stale/partial/unavailable coverage is
reported as a warning, not silently presented as complete coverage.

The adapter is original MIT code; no AGPL platform code was copied. World Monitor code and its
hosted service have different licenses. [License](https://github.com/koala73/worldmonitor/blob/main/docs/license.mdx),
[hosted outputs agreement](https://github.com/koala73/worldmonitor/blob/main/docs/eula.mdx), and
upstream publisher rights still apply. API access alone is not permission to republish an article.

## Reliable agent consumption

All endpoints use the existing bridge bearer token. `GET /v1/fresh` remains a snapshot for existing
clients. Repeating that call can return the same stories. For durable processing:

1. GET `/v1/events?consumer=video-research&limit=100`.
2. Process events in sequence. The response includes `cursor`, `next_cursor` and `events`;
   each event contains `seq` and a complete `story` revision, including `sources`.
3. After successful processing, POST `/v1/events/ack?consumer=video-research&through=<next_cursor>`.
4. Repeat until `count` is zero. After a crash, unacknowledged events are replayed.

This is **at-least-once** delivery. Use `seq` as the idempotency key for work performed by that
consumer. Only acknowledge the contiguous prefix you successfully processed, not the entire
page if a middle event failed. An acknowledgement cannot exceed the highest sequence previously
offered to that consumer; older repeated acknowledgements never move the cursor backwards.
Different consumers do not consume one another's events. Serialize workers using the same name;
this first version does not lease jobs among multiple workers sharing one consumer.

Consumer names accept 1–64 ASCII letters, digits, dots, underscores or hyphens. The first call
starts at the beginning of the retained journal. Limits are 1–500. New endpoints return 400 for
invalid parameters, 401 without authentication, 405 for the wrong GET/POST method and 503 if
story storage is unavailable. Errors never acknowledge an event.

## Grouping, updates and storage

`stories.sqlite` is separate from the existing `seen.sqlite`; upgrading preserves both existing
application data and installation. Initially fetched articles seed the new journal even if the
legacy ledger had seen them. The journal retains story payloads, source metadata, immutable
revisions and consumer cursors; no automatic history pruning is performed in this first version.
Back it up with SQLite's backup API (or stop the service and copy the database plus WAL state).

Canonical URL identity is authoritative for source updates. Similar headline tokens can join a
story among the 500 most recently observed groups within 48 hours. Financial quotes, forecasts
and prediction markets are excluded from fuzzy grouping. Grouping is a heuristic and can merge
or split incorrectly: multiple links are not proof of independent reporting. Revisions are created
for added URLs or changed source titles/snippets, not score/count/timestamp-only provider changes.
World Monitor's shortened snippet never replaces a direct RSS summary for the same URL.
Provider metadata is refreshed in the current stored story; old event payloads remain immutable.

A batch may produce several ordered revisions of one story as its sources arrive; the fresh
snapshot shows the last substantive revision. `sources[].providers` distinguishes direct
collection from World Monitor. Assessments remain under `sources[].metadata`; unassessed
credibility stays explicitly marked. Nothing is automatically verified or published.

## Tests

```bash
python3 -m venv .venv
.venv/bin/pip install -r linux/requirements.txt pytest
AGENT_NEWSBOT_HOME="$(mktemp -d)" .venv/bin/pytest -v tests
./build.sh --arch arm64
```

The suite exercises the digest schema with synthetic fixture data, local HTTP provider responses,
redirect/auth/error behavior, durable updates, transaction rollback, consumer acknowledgements,
and existing feeds/API behavior. On macOS it compiles a Swift integration harness and runs the
same parsing and actual bridge HTTP contracts. The fixture is synthetic, not republished news.
Live licensed-provider availability is a separate check; no paid credential is needed for tests.

Watchlists, editorial ranking, automatic briefs and VideoBot export are later extensions, not
features of this first integration. It ingests the news digest only, not every World Monitor layer.
