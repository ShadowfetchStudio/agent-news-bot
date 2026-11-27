# Operations and ingestion storage

Open **Operations** in the Mac toolbar. The view brings together journal storage,
consumer backlog and persistent source checks. **Reload operations** takes new
observations; it asks before discarding edited limits. Source history has its own
reload button. Observations are separate local snapshots, not a cross-database
transaction or a real-time uptime claim. A failed section remains visibly failed
while other sections are available.

On Linux, `agent-newsbot operations` prints the same report (exit 1 for incomplete
observations). `agent-newsbot storage-policy` reads storage and current limits.
The running app exposes authenticated `GET /v1/operations` and
`GET/POST /v1/library/storage`; neither endpoint accepts query parameters.
The feature is advertised as `operator_storage: 1` in capabilities.

## Limits and what they protect

The default journal ingestion ceiling is **2048 MiB** and the minimum volume free
space is **256 MiB**. Valid ceilings are 1–8192 MiB; free-space reserves are
64–8192 MiB. Both limits and an `expected_version` must be supplied when saving.
The first explicit policy has version 1. A stale version returns 409; reload and
review before retrying. The saved policy is part of research/application backups.
Old journals and archives without a policy use the defaults.

Before a fetch contacts providers, storage is checked. The ingestion transaction
rechecks the limits and sets SQLite's page ceiling for that connection. If the
batch cannot fit, its story, event and alert changes roll back together. Existing
research and delivery positions are preserved. No automatic pruning, consumer
reset, acknowledgement or approval happens.

The status distinguishes logical allocated database bytes, the physical main file,
reusable pages, WAL bytes and volume free space. It includes story/event counts,
unread alerts and bookmarks. An allocated journal already above the ceiling, free
space below the reserve, or a WAL already above the ceiling blocks new ingestion.
Used pages at 90% of the ceiling produce a warning. A batch can still fail below
90% if it would exceed the remaining capacity. The free-space reserve is a
preflight condition, not a reservation against concurrent disk users.

**This is an ingestion budget, not a filesystem quota.** Manual editorial work,
consumer acknowledgements, policy changes and recovery remain available; their
writes can grow the journal past the ceiling. A long-lived SQLite reader can grow
the WAL during a batch. Backups, export folders, caches and other applications are
outside this ceiling. Do not use these settings as an OS disk quota or as a promise
that a batch cannot consume any of the reserved free space.

## Recover from a paused fetch

1. Inspect Operations and free volume space if it is below the reserve.
2. Make a verified research or application backup before pruning.
3. Review consumer lag and retention holds. Unacknowledged events, bookmarks,
   draft/approval references, unread alerts and repair evidence can prevent
   pruning. Do not reset a consumer merely to make its backlog disappear.
4. Use the explicit, backup-gated [retention workflow](HISTORY_RETENTION.md), or
   raise the ingestion ceiling if the retained research is needed and disk space
   permits. Pruning makes pages reusable; it does not compact the main file.
   If the allocated file is above the ceiling, raise the ceiling to its size;
   pruning alone will not remove the above-ceiling block.
5. Reload observations and retry the fetch. A failed batch was not acknowledged.

Example Linux change file (bytes, not MiB):

```json
{"expected_version":0,"max_journal_bytes":2147483648,"min_free_bytes":268435456}
```

Apply with `agent-newsbot storage-policy --changes /absolute/path/limits.json`,
or POST the same object to `/v1/library/storage` using the current bridge token.
Do not put the token in a shared change file. Policy writes are serialized with
journal transactions. A fetch that already passed preflight is rechecked at its
ingestion transaction; a policy cannot retroactively undo a committed batch.
