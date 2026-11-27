# Research backup and recovery

The recovery tool saves the research journal, optional freshness cache and optional source-health history. It verifies SHA-256 hashes, byte counts, SQLite integrity, supported table layouts, JSON records, references and consumer checkpoints before restoring into a **new directory**. It never overwrites an existing directory, even an empty one. The running library is not replaced or restarted by these commands.

Included in `stories.sqlite`: stories, original provider observations, correction/event history, group-repair receipts, identity aliases, editorial drafts/notes/approval state, bookmarks, saved searches, saved-search alert subscriptions/inbox/read positions and version clock, source mutes and consumer checkpoints. `seen.sqlite` and `source-health.sqlite` are included when present. Research text and worker lease tokens in the backup are private data; keep the backup private.

This is a **research backup**, not a full computer or application configuration backup. It excludes bridge tokens, Keychain secrets, Mac preferences, Linux config, exported files/snapshots, agent inboxes/outboxes and binaries. Use the [application archive workflow](APPLICATION_RECOVERY.md) to include configuration and selected export folders; bridge/Keychain credentials and independent agent state still require separate reconnection or backup. Media rights and editorial approval do not change through recovery.

## Create and verify

Installed Linux CLI (substitute the actual service data home; a system installation may use `/var/lib/agent-news-bot`):

```sh
agent-newsbot backup --home "$HOME/.local/share/agent-news-bot" --destination "$HOME/NewsBot-backup-2026-11-27"
agent-newsbot verify-backup --backup "$HOME/NewsBot-backup-2026-11-27"
agent-newsbot restore-research --backup "$HOME/NewsBot-backup-2026-11-27" --destination "$HOME/NewsBot-recovered-2026-11-27"
```

The Apple Silicon/universal app packages the same standalone tool. On a Mac with **Python 3.10 or later** available, for example:

```sh
python3 "/Applications/Agent News Bot.app/Contents/Resources/recovery.py" backup \
  --home "$HOME/Library/Application Support/AgentNewsBot" \
  --destination "$HOME/NewsBot-backup-2026-11-27"
python3 "/Applications/Agent News Bot.app/Contents/Resources/recovery.py" verify-backup \
  --backup "$HOME/NewsBot-backup-2026-11-27"
python3 "/Applications/Agent News Bot.app/Contents/Resources/recovery.py" restore-research \
  --backup "$HOME/NewsBot-backup-2026-11-27" \
  --destination "$HOME/NewsBot-recovered-2026-11-27"
```

Python is required only for this recovery helper, not for ordinary native Mac app use. The tool needs only Python's standard library and works without the app's runtime packages or a valid provider configuration. From a source checkout, the script is `linux/agent_newsbot/recovery.py`. The three commands also appear in `agent-newsbot --help`.

Each command emits a JSON result and returns zero on success, one on an operational/validation failure, or two for incorrect CLI syntax. The default total database budget is 512 MiB and the operation deadline is 60 seconds. `--max-bytes` accepts 1 MiB–8 GiB; `--timeout` accepts 1–600 seconds. Copying requires a 64 MiB free-space reserve. Files are created as 0600 inside 0700 directories. The destination's parent must already exist.

SQLite's backup API includes committed WAL transactions; simply copying `.sqlite` without its WAL would not. Each database is a consistent individual snapshot. The three databases do **not** share one transaction boundary. For a quiet recovery point, finish outstanding fetch/edits, quit the Mac app or stop the Linux service, and pause external CLI writers before backing up. Online journal backup is supported, but changes made afterward are not in it. Freshness/cache/history metadata can represent slightly different moments.

## Restore and inspect before activation

1. Verify the backup. SHA-256 detects accidental alteration relative to the manifest; it is **not** a digital signature or proof of who created it. Use your own trusted backups.
2. Restore to a new directory. The tool verifies the source, copies with size/hash checks, validates the copied databases, clears source probe leases, clears consumer worker leases, increments the backed-up consumer generations and resets offered positions to acknowledged positions. It preserves acknowledged cursors and research/approval records.
3. Inspect `RECOVERY.json`. It includes the original manifest and hashes/counts of the restored databases **after** lease changes. Compare important stories, drafts, repair receipts and bookmarks using an isolated app/CLI run. The original backup stays unchanged.
4. Reconcile downstream consumers before reconnecting. Restoring older cursors can replay events whose external side effects already happened. Events after the backup are absent. Reissuing a local generation does not fence every future generation that existed after that backup. New bridge credentials and explicit reauthorization are required; preserve/reconcile the consumer's durable inbox/outbox separately. Recovery does not guarantee exactly-once publication.
5. Only after inspection, stop the active app/service and make a separate backup of its current home. Select the recovered home through an explicit `AGENT_NEWSBOT_HOME` configuration change or a carefully reviewed folder move. This tool does not perform that switch. Do not run two installations with the same restored consumers against a publishing workflow.

Restoring into a new home intentionally does not bring the former bridge credential or setup preferences with it. The next normal startup creates its own credential; use Settings/configuration to reconnect sources and agents. A native Mac run using `AGENT_NEWSBOT_HOME` uses isolated test preferences/Keychain accounts. Retained stories appear in the Library; the latest-batch snapshot is not recovered, so the main feed is populated by a new fetch.

## Failure handling and scope

An interrupted/failed backup retains `INCOMPLETE.json`; a failed restore retains `RECOVERY_INCOMPLETE.json`. No success manifest/receipt is promised for such a folder. Verification rejects incomplete folders and unexpected files (including SQLite sidecars). Current app/database entry points refuse these incomplete homes. Restore again into another new directory; never remove the marker to force an incomplete library into use.

Unknown schemas, non-regular/linked files, malformed JSON, orphaned story references, invalid checkpoints, changed checksums and exhausted byte/time/disk limits fail visibly. Existing backups and libraries remain untouched. The tool does not automatically delete partial directories. Its schema version is intentionally strict; future schema changes need a corresponding recovery-tool update.

Recovery itself performs no pruning. The separate [history-retention workflow](HISTORY_RETENTION.md) requires a saved plan and verified archive, protects consumer/editorial evidence and persists an explicit delivery boundary. Recovery preserves this optional boundary and remains compatible with older version-1 archives without it. The [application archive workflow](APPLICATION_RECOVERY.md) adds configuration and selected export trees without automatic activation. Total storage limits and broader load validation remain separate release work.
