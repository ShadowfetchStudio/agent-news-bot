# World Monitor integration

The optional connector ingests World Monitor's English feed digest into the existing desks. It
retains source links, snippets, publication dates, category, location, provider assessments and
coverage state. New and updated story groups are written to a durable SQLite revision log that
agents can consume independently. Existing sources work when the connector is disabled or fails.
This feature does not install or start a World Monitor server, subscribe to a plan, or publish posts.

## Configuration

**macOS first launch:** choose direct sources alone, direct sources plus your own World Monitor
server, or direct sources plus an authorized hosted API. Direct sources remain active in all
three modes. Choose at least one desk and whether to refresh automatically. No news fetch or
agent discovery starts until setup is saved. The loopback bridge can start before setup, but
fetch requests cannot run until setup is completed. A connection test is optional and reads one
digest without saving stories. Saving an enabled connector's edited key uses Keychain; a failed
read/write keeps setup pending and preserves the existing key. Direct-only setup leaves the key
unchanged. Existing installations retain their preferences and do not repeat setup. Unfinished
setup stays unfinished after restart.

**macOS Settings:** build the app with `./build.sh --arch arm64`, open the built bundle, then Settings →
World Monitor. Enable the connector, set the server origin and edition, and enter a key if the
server requires one. Use **Credentials → Save credential changes** to save the key to Keychain.
Closing Settings saves ordinary preferences, but does not change credentials. **Test World Monitor
connection** uses the values currently entered, including an unsaved key. It does not save that
key: save credential changes before fetching with it. The result includes coverage and a session
check timestamp; editing the origin, edition or key clears it. A compatible empty, partial or stale
digest proves connectivity, not current complete reporting, factual verification or reuse rights. Fetch Fresh performs a digest request. Source links and revision labels are visible
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
Both runtimes enforce a 20-second total resource deadline for this connector. Python uses an
isolated request worker that is killed and reaped on expiry; a read-inactivity timeout alone would
not bound a server that trickles data. Direct sources also have streaming/total-time bounds. Persistent circuit breakers and last-success history are described in [Source health](SOURCE_HEALTH.md).

### Connection diagnostics

Linux: run `agent-newsbot test-worldmonitor` using the same private configuration as the
service. It tests even when scheduled World Monitor ingestion is disabled, prints redacted JSON
(`code`, `message`, `succeeded`) and exits 0 for a compatible digest or 1 on failure. It does not
create research records, snapshots or consumer events. Tests on either platform make one request,
which may count against the provider's quota; they do not start or install a provider.

Both runtimes distinguish configuration errors, TLS validation, timeout, transport failure,
oversized responses, incompatible JSON/schema, authentication (401), authorization (403), missing
endpoint (404), rate limits (429), redirects and provider errors. They never echo server error
bodies or credentials. Fix the server certificate instead of disabling TLS verification. A redirect
is refused; configure the final authorized origin explicitly. Python does not inherit environment
proxies for requests containing the World Monitor key. Connection-test results are separate from the persistent [source health and automatic backoff](SOURCE_HEALTH.md) applied to ordinary fetches.

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
See [data rights and retention boundaries](DATA_RIGHTS.md): the persistent journal and backup-gated archival tools do not implement hosted-output expiry or deletion across exports/backups.

## Reliable agent consumption

All endpoints use the existing bridge bearer token. `GET /v1/fresh` remains a snapshot for existing
clients. Repeating that call can return the same stories. For durable processing:

1. Read `/v1/capabilities` and inspect the retained-history boundary before onboarding.
2. Create a named consumer with an explicit start (`history`, `date`, `retained` or `now`) and retain its returned generation. History/date choices can be rejected after archival pruning; `retained` explicitly accepts the available scope.
3. GET `/v1/events?consumer=video-research&limit=100` and process the ordered events idempotently.
4. Acknowledge only the contiguous successfully committed range with the current generation (and lease token if acquired). Fetching does not acknowledge anything. An acknowledgement cannot exceed the previously offered range.
5. After interruption, read status and replay unacknowledged work. Generation checks fence stale administration, and optional leases coordinate competing workers.

This is **at-least-once** delivery, not exactly-once publishing. Separate consumer names have independent progress. Use the [complete consumer contract and reference client](CONSUMER_RECOVERY.md) for exact request bodies, replay/reset behavior, lease renewal and downstream side-effect handling. Legacy generation-zero clients retain their compatibility path until explicitly configured; new integrations should use generation-bound administration.

## Grouping, updates and storage

`stories.sqlite` is separate from the existing `seen.sqlite`; upgrading preserves both existing
application data and installation. Initially fetched articles seed the new journal even if the
legacy ledger had seen them. The journal retains story payloads, source metadata, immutable
revisions and consumer cursors. Use the [research backup/verification/restore commands](RESEARCH_RECOVERY.md) for a verified independent archive. [Explicit archival maintenance](HISTORY_RETENTION.md) can remove eligible old events only after backup verification and consumer/protected-evidence checks; no automatic history pruning or total storage cap is implied.

Canonical URL identity is authoritative for source updates. Similar headline tokens can join a
story among the 500 most recently observed groups within 48 hours. Financial quotes, forecasts
and prediction markets are excluded from fuzzy grouping. Grouping is heuristic, not proof of
independent reporting. See [Story identity, observations and revisions](STORY_MODEL.md).

A batch emits one complete revision per changed story after all source observations are processed.
`sources[].observations` retains each provider's report; `selected_provider` identifies the selected
headline. A newer observed correction can supersede older direct text without an unchanged RSS item
reverting it. Equal-generation ties prefer direct reporting and identical headlines retain the
longer summary. Differing reports remain available for editorial review.

Stable context changes emit `enriched` events. Score/count/time-only changes update current stored
state without an event; use `GET /v1/stories/<id>` to retrieve them. Use
`GET /v1/stories/<id>/revisions` for history and before/after differences. Old event payloads remain
immutable. Nothing is automatically verified or published.

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
