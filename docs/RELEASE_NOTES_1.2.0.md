# Agent News Bot 1.2.0

A durable local research desk for macOS and Linux, with an optional World Monitor
news-digest connector. Direct sources work independently. This release does not
embed World Monitor's map dashboard, generate finished videos or publish posts.

## Downloads

[GitHub release and checksums](https://github.com/ShadowfetchStudio/agent-news-bot/releases/tag/v1.2.0).

- `AgentNewsBot-1.2.0-macos-arm64-ad-hoc.zip`: native Apple Silicon app, macOS 14+.
  **Ad-hoc signed; not notarized.** This distribution format was selected by the
  owner. Read [Mac installation](MAC_INSTALL.md). Rosetta is not needed.
- `AgentNewsBot-1.2.0-linux-source-installer.tar.gz`: tracked source, private-config
  installer, systemd service, tests, documentation, icons and dependency locks.
  Python 3.10+, Linux/systemd and network access for dependency installation are
  required. Start with `linux/install.sh --user` for a user service. A C toolchain
  may be needed if pip builds the locked QuickJS dependency from source.
- `AgentNewsBot-icon.png`: original 1024-pixel project artwork, MIT licensed.
- `RELEASE-INFO.json` and `SHA256SUMS`: exact source commit, platform/signing scope
  and SHA-256 hashes of the released files. Checksums are not independent signing.

The Linux installer is a source package, not an AppImage or a desktop application.
Its installed Python package includes the 256-pixel icon. Intel Mac users can build
from source; this release's downloadable Mac executable is ARM64 only.

## What changed

- Durable story identities, current-story lookup, corrections, versioned provider
  observations, multiple desks and revision history. Conservative grouping keeps
  distinct events apart; explicit split/merge preserves history and has retry-safe
  receipts. Source breadth does not imply independent confirmation.
- Searchable research library, bookmarks, saved filters, source mutes, saved-search
  alerts and a correction-review queue. Source-linked briefs organize retained
  evidence, changes, disputed reports, unknowns and follow-up questions.
- Separate revision-bound YouTube, X, TikTok and narration drafts/approval.
  Corrections require another review. Research exports include provenance and
  source URLs for a manual VideoBot handoff; media rights remain to be checked.
- Durable per-consumer events, generation checks, replay/onboarding choices and
  optional worker leases. The reference consumer commits its local inbox before
  acknowledgement and includes an explicit outbox pattern for external effects.
- First-run source setup, optional connection diagnostics, observable credentials,
  explicit skill installation, automatic Mac refresh and restored snapshots.
- Bounded HTTP transfers, total provider deadlines, persistent source backoff,
  truthful asynchronous fetch jobs and separate source coverage versus story counts.
- Operations view/CLI/API for consumer backlog, source health and ingestion budgets.
  Verified research/application archives, new-folder restore and backup-gated
  history pruning preserve unacknowledged and protected evidence.
- Staged Linux upgrades with private configuration, health checks and rollback.
  Shared API capabilities/errors, hash-locked dependencies and advisory checks.

All B01–B14 and R01–R09 findings are mapped to current regression evidence in
[release validation](RELEASE_VALIDATION.md). The historical audits remain available
with their original pre-fix findings; they are not descriptions of this release.

## Before upgrading

Use [application backup](APPLICATION_RECOVERY.md) for research, selected settings
and exports. Stop writers as documented. Preserve your old version and its
configuration until the new version is verified. Mac installation does not remove
research from Application Support. Linux upgrades preserve selected configuration
and have explicit rollback; rollback does not restore old research automatically.

After archival pruning, older apps must not open the journal. Retention-aware
clients need the explicit retained-history boundary; never reset a lagging consumer
merely to remove a backlog. See [retention](HISTORY_RETENTION.md) and
[consumer recovery](CONSUMER_RECOVERY.md).

Hosted World Monitor data permissions and publisher reuse rights are separate
from the connector's code license. Review [data rights](DATA_RIGHTS.md) before
retaining or redistributing hosted output. The app does not automatically expire
protected research, archives or exports to satisfy a hosted plan's retention terms.

## Validation and limits

The release-preparation CI passed **439 Mac tests** and **270 Linux tests with
169 Mac-specific skips**, plus the dependency audit and native build/signature
checks. Actual Mac walkthroughs cover setup/diagnostics, corrected history,
platform decisions, briefs, alerts, storage recovery, credential failure/reload and
keyboard navigation. A five-minute timer completed two live refreshes; one
rate-limited source was correctly shown as partial coverage. Earlier bounded
load trials cover 3,000 stories, 6,000-event retention and 6,400 alert matches.
See the current [checklist](RELEASE_CHECKLIST.md) for package/download evidence.

Explicit limits:

- No Apple notarization, Developer ID identity or clean-account Gatekeeper approval
  is claimed. Ad-hoc integrity verification is a different check.
- Accessibility labels, focused keyboard paths and small-window layouts were
  checked. VoiceOver could not be verified running through the available UI tools;
  a complete spoken-screen-reader walkthrough remains unverified.
- Hermes/OpenClaw protocol and failure handling were exercised with controlled
  services. Their executables were absent on the checked hosts; no live-model
  end-to-end handoff is claimed. No authenticated commercial World Monitor account
  was tested. Connection tests do not establish rights to retain/reuse its output.
- Native Pop!_OS user-service installation/upgrade/rollback is tested. Root system
  service installation, native Intel Mac execution, every Linux distribution and
  months of unattended production load are not claimed.
- Source availability changes; successful transport/parsing is not fact-checking.
  Grouping, disagreement and ranking assist review, not historical truth.
- The journal limit governs ingestion, not total filesystem consumption. Manual
  edits, WAL, caches and archives have the scope documented in [Operations](OPERATIONS.md).
- Delivery is at least once. External publishing needs idempotency and independent
  authorization; this app does not provide exactly-once social publication.

Publication verification: all five public release assets were downloaded again and
matched the tested local files byte for byte. The release tag and embedded metadata
identify `86785a1628537ea7224878368b6c7c987529a92c`. See
[package evidence](audits/2026-11-27/release-packages.json).
