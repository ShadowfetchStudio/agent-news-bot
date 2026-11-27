# Story identity, observations and revisions

The story journal is durable. The current fresh batch is a view of changes, not the complete library.
Use `GET /v1/stories/<id>` to read current stored context and `GET /v1/stories/<id>/revisions?limit=50`
for up to 100 recorded revisions with field-level before/after differences. Both require the bridge
token. Missing stories return 404; invalid revision limits return 400. The Mac inspector displays
provider observations, a disagreement warning and the recorded timeline for the selected story.

## Complete revisions

A successful ingestion transaction emits at most one event per changed story, after all of its
provider/source observations have been merged. This prevents a consumer receiving an intermediate
story missing the World Monitor context that arrived in the same batch. Existing events and
consumer cursors are not rewritten. Event sequence numbers remain the idempotency keys.

- `new`: first recorded story.
- `updated`: a source URL was added, selected editorial text changed, or an existing provider
  changed its headline/summary, including changes that warrant review rather than selection.
- `enriched`: stable context or provenance changed without an editorial text change.

Location, threat, tickers, link verification and assessed/unassessed status are context fields.
Score/count/collection-time changes alone update the current record without generating events.
Agents needing the latest scores should read the current story. A score is a provider assessment,
not independent verification or permission to reuse media.

## Provider observations and disagreements

Every source URL retains separate `observations` keyed by provider. Observations include title,
summary, source, publication date, metadata, local last-observed time and the generation in which
its editorial text last changed. The selected headline comes from the most recently observed
substantive change; equal-generation ties prefer direct reporting. For an identical headline,
the longer available summary is retained. An unchanged old RSS item therefore does not overwrite
a correction later observed through World Monitor.

These are observation-order rules, not proof of the actual time or truth of a correction. Publication
dates are not assumed to be revision timestamps. Alternate observations remain visible for review.
Different headlines at one URL, or a heuristic polarity disagreement among related reports, set
`disagreement` and a review note. That heuristic can miss disagreements or overflag differences.
A story can belong to several `desks`; the original `desk` remains for client compatibility.

## URL identity version 2

Shared fixtures specify the same Swift/Python behavior:

- Lowercase scheme and host; normalize international domain names to ASCII IDNA form.
- Strip URL credentials and fragments, and remove known tracking query fields.
- Remove HTTP port 80 and HTTPS port 443; preserve other ports and IPv6 literals.
- Preserve non-root path trailing slashes, query order, duplicate fields, flags and plus signs.
- Preserve percent-encoded distinctions while uppercasing escape hex digits. Encode raw spaces
  and non-ASCII path/query text. An empty root path and `/` have the same identity.

The migration adds normalized member indexes and identity aliases without deleting old story IDs,
rewriting delivered events or advancing consumers. Both original IDs and recorded aliases can
retrieve current stories and revision history. Incoming article IDs become aliases when their
source joins an existing story group.

Old Python records sometimes lost non-default ports or path slashes before storage. Information
that was discarded cannot be reconstructed safely. The migration does not invent it: new incoming
URLs preserve those distinctions, while historical payloads remain as recorded. Likewise, a prior
incorrect story grouping is not silently split or rewritten by this identity migration.

## Recoverable RSS caching

Each feed's complete response body (maximum 4 MiB) is saved with its validator and observation time.
A 304 response is reparsed from that stored body under the current limit and lookback. Validators
from old installations without a stored body are not sent until a complete body has been fetched.
The body and validator are committed atomically, after successful journal ingestion. An unchanged
body retains its original observation time for undated entries.

Invalid feed documents are rejected. Native Atom parsing chooses the alternate article link,
preserves nested summary text, ignores nested source titles, and disables external entity loading.
Freshness database connections are closed after each Python operation.

## Group repair events

`group_repaired` revisions are explicit editorial regroupings. Split children link to their parent; merged source records remain addressable with `merged_into`. Consumers must follow the destination for active work and retain their original event identities for replay. See [group repair preservation and delivery semantics](GROUP_REPAIRS.md).
