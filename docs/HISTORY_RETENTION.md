# Archive old history without losing an agent's backlog

Retention is an explicit local maintenance workflow for both runtimes. It deletes eligible **event revisions only**, after checking a saved preview and an independent verified research backup. It does not delete current stories, source memberships, notes/drafts/approvals, preferences, aliases or group-repair receipts. Nothing runs automatically and no production data is pruned during installation or upgrade.

## What is protected

An event is eligible only when all of these hold in the write transaction:

- Its recorded `updated_at` is before the explicit timezone-qualified cutoff; unknown/invalid dates stay.
- Every registered consumer has acknowledged through its sequence. Merely offering a page is not acknowledgement. With no registered consumers, nothing is eligible.
- It is older than the story's latest two events.
- The story is not bookmarked.
- Its revision is not referenced by saved draft `source_revision`, aggregate `approved_revision` or any platform's `approved_revision`.
- It is not a `group_repaired` event.

A lagging consumer can intentionally prevent all pruning. Stop/reconcile obsolete workers and explicitly reset their cursors only if you intend to skip that backlog; maintenance never does this for you. Active leases are preserved. A worker can continue processing events after its acknowledged cursor while older acknowledged events are archived.

## Preview, back up, apply

Use your actual app/service home, not these example paths blindly. The app must have opened the journal with this retention-aware release first.

```sh
agent-newsbot storage-status --home "$HOME/.local/share/agent-news-bot"
agent-newsbot retention-plan --home "$HOME/.local/share/agent-news-bot" \
  --before 2026-10-01T00:00:00Z --limit 1000 --output "$HOME/newsbot-history-plan.json"
agent-newsbot backup --home "$HOME/.local/share/agent-news-bot" \
  --destination "$HOME/newsbot-before-history-prune"
agent-newsbot prune-history --home "$HOME/.local/share/agent-news-bot" \
  --plan "$HOME/newsbot-history-plan.json" --backup "$HOME/newsbot-before-history-prune"
```

Review the plan before applying: cutoff, acknowledgement boundary, count (`selection.entries`), exact sequence/story IDs, payload hashes and bytes. The plan file is private (0600), exclusively created, and never overwrites an existing file. Backup destinations must also be new. Empty plans are valid previews but applying one fails without a mutation.

On Mac, the app bundles the same standard-library-only helper. Replace `agent-newsbot` above with:

```sh
python3 "/Applications/Agent News Bot.app/Contents/Resources/maintenance.py"
```

For the `backup` step, use `recovery.py` instead. Python 3.10+ is required for these optional helpers; ordinary native app use does not need Python. Commands work without app dependencies or a valid provider configuration. No bridge/provider credential is required because access is to your explicitly selected local files.

`storage-status` reports journal/story counts, logical database bytes, reusable page bytes, WAL bytes, filesystem free bytes, consumer lag and retention receipts. These describe a current observation, not a capacity reservation. Receipts and plans are private research metadata.

## Atomicity and retry

The tool first verifies the backup's hashes, schema, integrity and table counts. Inside a SQLite write transaction, it re-evaluates the exact plan, checks every selected event against the backup's exact payload, and rehashes the backup journal before deletion. A changed consumer checkpoint, newly protected revision, changed payload or changed eligible set rejects the stale plan. Generate and review another preview; no automatic replanning or cursor skipping occurs.

Deletion and the retained-history boundary/receipt commit together. Failure rolls them back. The receipt includes operation ID, plan SHA-256, removed count, archive journal SHA-256 and the highest removed sequence. The last 20 receipts are kept. Retrying the identical plan returns the original receipt with `replayed: true` while that receipt remains retained; it does not delete twice. A different plan with the same ID is rejected. A retry still requires a valid backup. Beyond 20 later operations, inspect state and create a fresh plan; receipt history is bounded.

Backups are independent private archives, not automatically moved or deleted. Keep the referenced backup if you need archived revisions. Hash checks detect changes relative to the manifest; they do not establish authorship or protect against a malicious owner concurrently replacing all files. Use your own trusted archives.

## Agents and revision history after pruning

Capabilities advertise `retained_history: 1`. Authenticated `/v1/consumers` adds `retention` with `pruned_through`, cumulative `pruned_events` and `last_pruned_at`. Event pages include `pruned_through`. All surviving events **after this global sequence boundary** form a complete delivery suffix. Some protected older events remain available through story history but are outside that suffix.

Existing consumers already at or beyond the boundary keep their positions and generations. A new automatic history consumer, or a reset/ack below the boundary, gets 409 rather than silently losing earlier work. Explicit choices are:

```json
{"consumer":"new-research-worker","action":"create","start":"retained"}
```

`retained` starts at the boundary, deliberately accepting the complete remaining suffix; `now` deliberately skips the current backlog. After any pruning, `history` and `date` creation/reset are rejected. Date replay is conservatively disabled because a timestamp alone cannot guarantee complete archived coverage. Before pruning, existing history/date semantics are unchanged. Reset still requires the current generation and no active lease. The reference worker accepts `--start retained` for a new name and never automatically resets an existing name.

Per-story revision responses add `history_gap_before`. If the preceding revision is not available in the returned history context, `diff` is empty rather than comparing non-adjacent revisions. The native History tab explains the gap. The current story and latest two revisions remain available for briefs. Restoring a post-prune research backup preserves the boundary; restoring the pre-prune archive into a new folder recovers its earlier event history and checkpoint state, subject to the usual downstream reconciliation rules.

Do not point an older app version at a pruned journal: older software does not enforce the retained-history boundary. Retain a pre-prune backup for rollback. Current recovery also accepts older version-1 archives without the optional retention table; the current app initializes its empty boundary when opened.

## Bounds and space accounting

Default operation deadline: 60 seconds (1–600 allowed). Database verification budget: 512 MiB (`--max-bytes`, 1 MiB–8 GiB allowed). A batch selects at most 1,000 events by default (`--limit` 1–10,000), at most 32 MiB of selected payload; oversized candidates require a smaller batch. A 64 MiB free-space reserve is required before pruning. The saved plan is capped at 2 MiB on apply. SQLite lock waits are short and operations fail visibly on contention or deadlines.

Pruning makes database pages reusable; it does not promise immediate filesystem shrinkage or secure erasure. SQLite's [VACUUM documentation](https://www.sqlite.org/lang_vacuum.html) explains compaction and its extra-space/locking requirements. This workflow does not run VACUUM, set an overall journal growth cap, prune unrelated caches or delete archives. Those storage policies require their own measurements and controls. A backup plus a live pruned database can initially consume more total disk space.
