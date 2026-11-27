# Source health and circuit breakers

Source health survives restarts in a private `source-health.sqlite` beside the research journal.
It is separate from stories, editorial state, delivery acknowledgements and freshness caches.
No provider credential, raw URL or response error body is stored in this database. Source IDs are
SHA-256 hashes of an internal source/configuration key; friendly names identify each record.

## What the records mean

- `last_attempt`: when the latest owned source check began (Unix seconds, UTC).
- `last_success`: the last fully successful retrieval/parse check, including a valid empty feed.
- `last_usable`: the last successful or partial response. This does not promise any new stories.
- `last_state`: success, partial, stale, unavailable or unknown from the actual source checks.
- `failure_streak`: consecutive request/stale/unavailable failures. A healthy connection with a
  usable partial response resets the retry streak without claiming fully successful coverage.
- `next_retry`: earliest automatic/manual Fetch Fresh retry (Unix seconds); zero means no delay.
- `last_error`: a bounded internal code, never the provider's raw error body.
- `circuit`: ready, probing, backoff or open, calculated when the state is read. This is not uptime.

The macOS Coverage window includes reloadable persistent history. Authenticated `GET /v1/sources`
returns the bounded history list on both platforms; it accepts no query parameters. Linux also has
`agent-newsbot source-health`, which works without a running server. `GET /v1/capabilities`
advertises `source_health: 1`. Reading status does not perform provider requests.

RSS history is scoped to a feed **and desk**, so a feed included in two desks can populate both
without one concurrent fetch suppressing the other's membership. Identical labels on different
feeds/desks have distinct history IDs. Multi-request APIs have one record per provider configuration:
Hacker News, Polymarket, Yahoo market symbols, Open-Meteo city, and World Monitor origin/edition.
Source-check coverage uses these IDs when available instead of collapsing distinct feeds by name.

## Retry behavior

A failed request, stale response or unavailable/unknown check waits 60 seconds initially, doubling
with consecutive failures up to six hours.
At three consecutive failures, the circuit is called open until that retry time. Once the delay
expires, the next fetch can claim a probe. A full success closes the circuit and clears the streak.
Partial data is still returned to the library. Partial coverage with transport failures gets a retry
delay, but a compatible partial digest with a healthy connection keeps normal polling. It updates
last-usable time without claiming fully successful coverage. Other sources continue independently.

HTTP `Retry-After` seconds or an HTTP date can extend the delay, bounded to 24 hours from receipt.
Malformed or past values do not shorten the exponential delay. Within a multi-request provider
check, three transport failures or a future `Retry-After` stop further HTTP attempts in that check.
There is no automatic tight retry loop. The existing per-transfer streaming byte caps, total
resource deadlines, redirect restrictions and worker reaping remain in force.

A transactional five-minute lease prevents overlapping probes for the same source key. An expired
lease is recorded as interrupted; a new probe gets a new token. An old worker cannot overwrite a
newer probe's result. A cancelled native task releases its lease without claiming success. A killed
process becomes retryable after lease expiry. Long provider checks can exceed the lease; fencing
still prevents their late result from overwriting a newer check. No exactly-once request claim is made.

The explicit World Monitor **connection test** is an operator diagnostic outside scheduled source
health. It makes one request using the entered settings and does not clear a circuit or save stories.
A provider may count it against its quota. Changing credentials alone does not erase retry history.

## Storage limits and failures

At most 1,024 source/configuration identities can be recorded. Reaching capacity or being unable
to read/create the database prevents a new request and yields visible coverage errors. Records
are not silently deleted to make room. Operator retention tooling is tracked separately.

A failed health update after retrieval retains the retrieved stories and exposes a coverage warning.
It does not falsely claim persisted health. Schema/corruption/private-file checks fail closed, and
transaction failure preserves the previous state. These guarantees do not replace full application
backup/restore or establish power-loss durability.

## Default-feed maintenance — November 27, 2026

The bounded repeat check is retained in [feed-replacements.json](audits/2026-11-27/feed-replacements.json).
It records observation time, response format/count and hashes for successful feeds.

- The configured Yahoo Finance RSS and Medical News Today RSS URLs again returned HTTP 404.
  They are replaced by **Federal Reserve releases** and **National Cancer Institute releases**,
  respectively. Both returned HTTP 200 and parseable RSS (20 and 10 entries in this observation).
  These are institutional announcements, not substitutes for independent reporting. Existing
  business and health news sources remain available alongside them.
- The two configured AP feeds again failed to connect from this environment. They have been
  removed from defaults pending a verified replacement endpoint. This is not a claim that AP has
  permanently retired all feeds. Existing AP research and source history are preserved.
- VentureBeat's earlier 429 is handled by backoff, not treated as proof of retirement. A valid empty
  arXiv response remains a successful check, not an outage.

Primary publisher documentation: [Federal Reserve RSS directory](https://www.federalreserve.gov/feeds/feeds.htm)
and [NCI RSS directory](https://www.cancer.gov/syndication/rss). Their directories identify the
selected feeds. Feed availability does not grant rights to reuse every article, image or third-party
asset. Preserve publisher attribution and evaluate the source material before publishing.
