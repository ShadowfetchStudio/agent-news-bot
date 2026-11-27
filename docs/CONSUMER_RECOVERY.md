# Consumer administration, replay and worker leases

Agent delivery is **at least once**, in journal sequence order. Consumers must commit their work before acknowledging. The research-library filters do not apply to event delivery. Event requests with unsupported query filters now return 400 instead of silently ignoring them.

## Observe and choose a starting point

Authenticated `GET /v1/consumers` returns `latest_seq` and a `consumers` array. Each row includes name (`consumer`), acknowledged `cursor`, highest `offered` sequence, `generation`, pending-event count (`lag`), Unix-seconds `last_ack_at` or null, and the active lease worker/expiry or null. Status does not expose lease tokens or change cursors. Null acknowledgement time means no acknowledgement was recorded since metadata tracking began or since the last reset; it does not invent historical timestamps.

Authenticated `POST /v1/consumers` accepts a bounded JSON object (Content-Length, at most 64 KiB), using one of these actions:

```json
{"consumer":"daily-brief","action":"create","start":"history"}
```

`start` options:

- `history`: start before all retained events.
- `now`: start after the current latest sequence, atomically with creation/reset.
- `date`: supply `since`, an ISO-8601 timestamp with timezone. Start just before the first event recorded at or after that time, then deliver the entire remaining sequence. This uses the event's recorded `updated_at`, not the publisher's publication date. Later entries with earlier timestamps still appear: delivery is never filtered into gaps.

Creating an existing name returns 409. A legacy consumer first created by `GET /v1/events?consumer=NAME` keeps its previous full-history behavior and generation zero. Explicit creation uses generation one. Use a separate name for every independent workflow.

## Replay or skip deliberately

Read status, stop your worker and then submit the observed generation:

```json
{"consumer":"daily-brief","action":"reset","start":"history","expected_generation":1}
```

Reset can use history, date or now. History/date replay work; now intentionally skips the current backlog. A reset increments `generation`, clears the previous offered range and resets the acknowledgement timestamp. It never deletes stories, events, drafts or another consumer's position. An active lease must be released or expire first. A stale expected generation returns 409 without mutation.

Every event response includes `generation`. Acknowledge only a range whose complete work has been committed:

```text
POST /v1/events/ack?consumer=daily-brief&through=123&generation=2
```

The acknowledgement cannot exceed the range previously offered to this consumer. Generations above zero require the matching generation; an old worker cannot acknowledge a newly replayed range. Generation-zero legacy clients remain compatible until their consumer is explicitly reconfigured or leased. A response lost in transit can be retried; acknowledgements are monotonic and idempotent within a generation.

Do not acknowledge only the interesting subset of a delivered batch. Commit every event, including a durable record of intentionally skipped work, before acknowledging its end. Use your own processing filters after delivery.

## Optional worker leases

For multiple workers sharing one consumer, acquire a lease with its observed generation:

```json
{"consumer":"daily-brief","action":"lease","worker":"worker-a","expected_generation":2,"ttl_seconds":60}
```

TTL is 10–300 seconds (default 60). Acquisition returns an opaque `lease`, its expiry and a **new generation**. It clears previously offered ranges, so the new owner must fetch before acknowledging. While active, event reads and acknowledgements require the matching `X-Consumer-Lease` header. Put the token in this header, not URLs/logs. The normal bridge bearer token is still required.

Renew by POSTing `action: "renew"`, `consumer`, `expected_generation`, `lease` and optionally `ttl_seconds`. Renewal retains the generation/token. Release uses `action: "release"` with the same identity fields; it clears the lease, advances the generation and clears the offered range. Other workers cannot acquire an active lease. Expired tokens are rejected, including during renewal; acquire a new lease instead.

Leases are optional and use the host clock. An unleased consumer may be read by multiple clients, so use leases where mutual exclusion matters. Leases prevent concurrent delivery/acknowledgement ownership; they cannot undo an external side effect performed by a worker after its lease expires. External sinks still need idempotency keys. Pause/restart workers after a major host-clock change.

Invalid commands return 400; missing consumers 404; generation/lease conflicts 409; storage failures 503. On 409, read status and reconcile—do not blindly reset or acknowledge with a newly fetched generation.

## Linux administration

```sh
agent-newsbot consumers
agent-newsbot consumers --changes consumer-command.json
```

The file uses the same command object as the API. Set `AGENT_NEWSBOT_HOME` to the service data folder when needed. The command never creates a missing news journal.

## Reference consumer

[`examples/reliable_consumer.py`](../examples/reliable_consumer.py) runs one batch by default, with `--follow` for a foreground polling loop. It uses requests, the localhost bridge, a token file, an exclusive lease, bounded transient-network retries and a private SQLite inbox. It does not publish, call agents or download media.

```sh
python examples/reliable_consumer.py \
  --token-file /path/to/bridge.token \
  --consumer research-inbox \
  --inbox /path/to/private/research-inbox.sqlite
```

The example's complete side effect is an atomic inbox transaction keyed by `(consumer, seq)`. A crash after commit and before acknowledgement safely replays the same keys. An inbox write error prevents acknowledgement. A reused sequence with different content stops processing rather than overwriting evidence. Use a separate inbox when connecting to a different or replaced server journal.

`--start history` is the default for a new name; `--start now` only affects creation. Existing names are never automatically reset. Administrative conflicts stop the example. If a lease-acquisition response is lost, the example stops instead of automatically retrying administration. Inspect status and wait for the short lease to expire rather than forcing a reset. This is safe but not transparent recovery of a lost lease token.

For real publishing or remote work, replace the SQLite sink only with an operation that accepts an idempotency key or a durable transactional outbox. A successful HTTP send followed by a crash can otherwise duplicate an external action. Acknowledgement is not proof of editorial or licensing approval.

The example now checks [capabilities and error-contract versions](API_CONTRACT.md) before creating/leasing a consumer, disables proxy inheritance, and validates ordered page/acknowledgement boundaries before committing. Only reads and generation-bound acknowledgements are automatically retried; administrative calls stop on ambiguous failures.

The [research recovery tool](RESEARCH_RECOVERY.md) preserves backed-up acknowledged cursors but clears worker leases and offered positions in a restored copy. Restoring an older journal can replay externally completed work and lacks events created after that backup. A restored local generation cannot fence every later generation from the former service; reconnect with new bridge credentials only after reconciling downstream inboxes/outboxes. Research recovery never automatically switches the running service home.
