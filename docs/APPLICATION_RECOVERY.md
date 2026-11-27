# Application configuration, research and export recovery

Use this workflow to archive the app's research databases together with its configuration and selected export directories. It supplements the narrower [research-only backup](RESEARCH_RECOVERY.md). It runs locally, makes no network requests and never replaces or starts an installed app/service.

Included:

- Research journal, drafts, approvals, bookmarks, watches/alerts, consumers and optional freshness/source-health databases, using the existing validated SQLite backup format.
- One explicitly selected configuration file: exported macOS preferences or the Linux service's private environment file. Its bytes are preserved exactly; Linux configuration may contain provider credentials.
- Named snapshot/research-export directory trees, including nested files and empty directories. Select every external export directory you need. `--no-exports` explicitly chooses none; there is no silent default discovery.

Bridge credentials are deliberately not restored. Files named `bridge.token` or beginning `.bridge-token-` are excluded from selected export trees and listed in the manifest. Mac Keychain secrets, OS accounts, systemd units/drop-ins, binaries and external agent inboxes/outboxes are outside this archive. Reinstall the app/service from a known release, re-enter/reconnect credentials, and back up independent agent state and customized system service files through the machine's own backup process. An app-data archive is not a bootable computer backup.

## Prepare a quiet recovery point

Finish pending edits/fetches, quit the Mac app or stop the Linux service, and pause external CLI writers and consumers. `--writers-stopped` records your explicit choice; the helper cannot prove that every external writer is stopped. It checks file inventories and metadata around copying and rejects detected changes, but this is not an atomic filesystem snapshot across databases, config and exports. Each research database has its own SQLite snapshot boundary.

Keep archives private. New directories are 0700 and files 0600, but **the archive is not encrypted**. Do not upload it as a release asset or commit it to GitHub. It contains research, notes and possibly Linux provider secrets. The command prints inventory/hashes, not configuration contents. SHA-256 verifies bytes relative to the manifest; it does not authenticate the creator of an archive.

## Linux

Run as the service's data owner after stopping the selected instance. Substitute its actual home, private configuration file and shared/export locations; inspect your install configuration rather than assuming the example paths.

```sh
agent-newsbot backup-application \
  --platform linux --writers-stopped \
  --home "$HOME/.local/share/agent-news-bot" \
  --config "$HOME/.config/agent-news-bot/config.env" \
  --exports "snapshots=$HOME/.local/share/agent-news-bot/shared" \
  --exports "research=$HOME/NewsBot-research" \
  --destination "$HOME/NewsBot-application-backup"
agent-newsbot verify-application --backup "$HOME/NewsBot-application-backup"
agent-newsbot restore-application \
  --backup "$HOME/NewsBot-application-backup" \
  --destination "$HOME/NewsBot-application-recovered"
```

Omit an unused `--exports` entry; a selected missing folder fails visibly. If no export folders are needed, use `--no-exports` instead. Export labels must be unique, 1–64 letters/digits/dashes/underscores, starting with a letter or digit. Up to 32 roots are supported. Paths with spaces must be quoted as above.

## macOS

With the app quit, export its preferences through the supported `defaults` command into a new private staging location. This captures ordinary preferences, not Keychain items. Production uses domain `ai.shadowfetch.agentnewsbot`; isolated development runs use a different path-derived domain.

```sh
umask 077
mkdir "$HOME/NewsBot-preferences-stage"
defaults export ai.shadowfetch.agentnewsbot "$HOME/NewsBot-preferences-stage/settings.plist"
python3 "/Applications/Agent News Bot.app/Contents/Resources/application_recovery.py" backup-application \
  --platform macos --writers-stopped \
  --home "$HOME/Library/Application Support/AgentNewsBot" \
  --config "$HOME/NewsBot-preferences-stage/settings.plist" \
  --exports "snapshots=$HOME/Library/Application Support/AgentNewsBot/Exports" \
  --destination "$HOME/NewsBot-application-backup"
python3 "/Applications/Agent News Bot.app/Contents/Resources/application_recovery.py" verify-application \
  --backup "$HOME/NewsBot-application-backup"
python3 "/Applications/Agent News Bot.app/Contents/Resources/application_recovery.py" restore-application \
  --backup "$HOME/NewsBot-application-backup" \
  --destination "$HOME/NewsBot-application-recovered"
```

Select additional Research export folders with additional named `--exports` arguments. Python 3.10+ is needed for the standalone helper; it needs no installed app dependencies. Ordinary native app use does not require Python. From source, use `linux/agent_newsbot/application_recovery.py`. The installed Linux CLI dispatches these commands before reading runtime configuration, so broken provider configuration does not prevent recovery.

## Verify and activate deliberately

A successful restore creates:

```text
NewsBot-application-recovered/
  home/                         # restored databases and research RECOVERY.json
  configuration/config.env      # Linux, or settings.plist on macOS
  exports/<selected label>/     # preserved selected directory trees
  APPLICATION_RECOVERY.json     # file hashes, research receipt, activation=false
```

1. Read `APPLICATION_RECOVERY.json`, compare important stories/drafts/exports, and verify the original archive. The research restore clears old worker/probe leases, advances backed-up consumer generations and resets offered positions to acknowledged positions. It does not erase editorial state or automatically approve anything.
2. Review configuration before use. Old home/shared paths, host addresses, models and ports may need adjustment. Configuration is copied as data, never executed or automatically applied. A successful byte check does not establish that every old setting works on a new machine.
3. Keep the current installation stopped and retain its own separate backup. For Linux, select the recovered `home/` with `AGENT_NEWSBOT_HOME`, copy/review the recovered environment file at the intended private config location, and set `AGENT_NEWSBOT_SHARED` to the intended restored snapshots folder. Match ownership to the service account. Recreate/review custom service drop-ins and use the staged installer/health workflow in [Linux upgrades](LINUX_UPGRADES.md).
4. On Mac, keep the current home as a rollback copy, place recovered home contents at the intended app support location and put the selected snapshots tree at that home's `Exports/`. Import the recovered preferences into the intended domain with `defaults import DOMAIN /absolute/path/to/settings.plist` while the app is quit. For production, DOMAIN is `ai.shadowfetch.agentnewsbot`; a test-home run needs its isolated domain. Re-enter Keychain credentials through Settings. Do not point production at a random test domain or expect a test-path launch to share production Keychain items.
5. Start only the selected installation. Confirm stored research, restored snapshots, provider settings and bridge health before resuming fetches. Source connectivity/reply verification is a new check, not inherited from backup.
6. Obtain the new bridge token and reconnect consumers explicitly. Reconcile downstream inboxes/outboxes before resuming external effects: an older backup can replay work already published elsewhere, and events recorded after it are absent. Do not run old and recovered consumer instances against the same publishing workflow.

The helper deliberately performs none of those activation steps. It never stops a production service, modifies preferences, overwrites an existing home, or reconnects an agent. Restoring both research and exports does not make separately captured snapshots newer than their recorded time.

## Bounds and failure behavior

Defaults: 512 MiB combined payload budget, 60 seconds, and a 64 MiB free-space reserve. `--max-bytes` accepts 1 MiB–8 GiB; `--timeout` accepts 1–600 seconds. Configuration is limited to 64 KiB, manifests to 8 MiB, export roots to 30 nested directory levels, and the total archive inventory to fewer than 10,000 entries (including reserved metadata). Choose an external destination whose parent exists; it must not be inside selected sources. Existing destinations, even empty ones, are never reused.

Linked/hard-linked/special files, invalid archive paths, altered hashes, unexpected entries, detected source changes, and exhausted size/time/space limits fail. Partial output retains `INCOMPLETE.json`; failed restored research also retains its protective marker where storage permits. A finalization error attempts to reinstate the markers. After any failed command, do not activate the destination even if a failed storage device prevented a marker write. Never activate such a folder or delete its marker to force it open. Retry into another new destination after resolving the issue. No automatic deletion of partial archives occurs.

Only the declared configuration and selected export roots are covered. Other files remain untouched and are not silently included. Neither application recovery nor archival pruning implements upstream hosted-content expiry across every copy; see [data rights](DATA_RIGHTS.md).
