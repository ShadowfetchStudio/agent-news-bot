# API capabilities and error contract

Read authenticated `GET /v1/capabilities` before using optional workflows. It accepts no query parameters. `/health` remains the only public endpoint, for liveness. A capability read has no journal/consumer side effects and exposes no token, filesystem path, source credential or private draft.

## Version and feature negotiation

The response advertises:

- `api_version: 1`: route family (`/v1`).
- `contract_version: 1`: discovery-document format.
- `error_schema_version: 1`: structured error format.
- `app_version`: packaged application version, distinct from protocol version.
- `implementation`: `swift` or `python`; all other advertised contract fields have cross-runtime parity tests.
- `features`: integer versions for async fetch, durable stories, source observations, revision history, consumer delivery/generations/leases, research library, editorial drafts, research export, group repair and optional World Monitor digest support.
- `routes`: canonical route templates and supported methods. Substitute a canonical story ID for `{id}`. Use the feature-specific guides for request/query fields.
- `limits`: 64 KiB JSON requests, 16 MiB responses, maximum fresh/event pages of 500, library/revision pages of 100, 4 MiB repair evidence and 10–300 second consumer leases.
- `authentication`, `delivery`, `limitations` and an HTTP-status-to-error-code/recovery map.

Clients should require supported version values for the features they use. Ignore additional unknown fields/features within contract version 1; do not assume an unknown major format is compatible. The reference consumer requires version 1 of API, contract, error schema and delivery/generation/lease features before creating or leasing a consumer.

Capabilities describe implemented functionality, **not live readiness**. For example, `worldmonitor_digest: 1` does not mean World Monitor is enabled, reachable or licensed for redistribution. Inspect status, coverage and explicit agent connection tests separately. The app does not embed World Monitor's complete dashboard, publish automatically, fact-check sources or clear media rights.

## Backward-compatible errors

Existing clients may continue reading the `error` string. New clients should branch on `error_detail.code`, not the English message:

```json
{
  "error": "story changed; reload before saving",
  "error_detail": {
    "schema_version": 1,
    "code": "conflict",
    "http_status": 409,
    "recovery": "reload_state",
    "automatic_retry_safe": false
  }
}
```

Additional response context, such as a current fetch job, is retained. The structured envelope applies at the final response writer, including malformed JSON bodies, authentication failures, storage failures, response-size rejection and connection-capacity rejection. Python's HTTP parser failures also use JSON. HEAD responses have headers but no body, as required by HTTP semantics. Unsupported PUT, PATCH, DELETE, OPTIONS, HEAD and TRACE requests return 405 on a known canonical route and 404 on unknown routes (authentication still comes first, except `/health`).

Key recovery cases:

| Status | Stable code | Recovery |
|---|---|---|
| 400 | `invalid_request` | Correct the request; do not repeat invalid input |
| 401 | `unauthorized` | Reload the configured private credential; do not log it |
| 404 | `not_found` | Check the resource identity/path |
| 405 | `method_not_allowed` | Check advertised routes/methods |
| 409 | `conflict` | Reload state and reconcile versions, generation or lease |
| 413 | `payload_too_large` | Reduce page/payload size and inspect state before retrying a mutation |
| 429 | `rate_limited` | Inspect state, then back off |
| 503 | `temporarily_unavailable` | Inspect state, then back off; overload may include Retry-After |

The complete status map is in the capability response. Unrecognized HTTP verbs may return 501. Status codes classify failures; they do not establish whether a side effect occurred. A response-size rejection or broken connection can follow a committed database change. `automatic_retry_safe` is therefore false in every error envelope. A client may retry only when it knows the operation is safe—for example, a GET, a generation-bound acknowledgement, or an identical repair request with its original operation ID. Consumer create/reset/lease administration must be reconciled after an ambiguous result.

Some transport failures have no response: disconnected clients, expired socket deadlines and malformed/incomplete headers may close the connection before a JSON error can be sent. Treat an absent response as an unknown outcome, not proof of rollback. No exactly-once external publishing guarantee is implied.

## Reference consumer behavior

The [reference consumer](../examples/reliable_consumer.py) negotiates capabilities, disables environment proxy inheritance for its loopback bearer credential, never follows redirects, and retries only reads and generation-bound acknowledgements with bounded backoff. It stops after ambiguous administrative failures. Before writing its private inbox, it validates page counters, strict event ordering and the acknowledgement boundary; malformed pages cannot advance a cursor.

Live Swift/Python bridge tests run the example twice: the first run commits and acknowledges a fixture story, while the second commits zero new records and leaves a single inbox entry. Separate crash-before-ack tests prove replay idempotency. These checks do not execute external publishing or replace the need for an idempotent downstream sink.
