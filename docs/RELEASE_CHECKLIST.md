# Combined app release checklist

Scope: repair every finding in the November 27 four-perspective audit, implement the identified usability and operational improvements, create the app icon, update documentation, and publish tested source and downloadable packages to GitHub. A passing unit suite alone does not close this checklist.

## Defects (audit IDs)

- [x] B01: disconnected native clients cannot terminate the app; bounded responses.
- [x] B02: deterministic listener lifecycle; occupied replacement port preserves service; accurate Settings status.
- [ ] B03: enrichment reaches current stories and consumers without score churn.
- [ ] B04: newer corrections survive provider precedence; versioned observations.
- [ ] B05: unchanged RSS feeds recover candidates after cap/lookback changes.
- [x] B06: quote checkpoints correspond to persisted observations.
- [x] B07: truthful asynchronous fetch completion, concurrency and failure semantics on both runtimes.
- [ ] B08: installed Linux CLI works outside checkout and finds service state.
- [ ] B09: shared URL canonicalization fixtures and existing identity migration.
- [ ] B10: source failure never appears as current coverage.
- [x] B11: unknown dates and restored snapshots expire deterministically.
- [ ] B12: X weighted Unicode/URL counting and parity fixtures.
- [x] B13: preserve custom agent skills; explicit managed installation.
- [ ] B14: separate new/updated/carried/displayed counts.

## Setup and everyday use

- [ ] First-run mode selection, connection test and actionable provider errors.
- [ ] Safe observable credential storage, failure preservation and rotation.
- [ ] Supported private Linux config and service wiring.
- [ ] macOS automatic refresh and restored batch.
- [ ] Disagreement/correction timeline, multiple desks and durable story detail.
- [ ] Saved searches/watchlists, entity filters, bookmarks and source muting.
- [ ] Source-quality explanations without inferred factual verification.
- [ ] YouTube/X/TikTok draft templates, editorial approval state, ranked briefs and VideoBot export.

## Automation and maintenance

- [ ] Durable story lookup, revision differences, source/provider filters.
- [ ] Consumer status/lag/ack, onboarding from history/date/now, replay/reset and optional worker leases.
- [ ] Reference idempotent retrying consumer and versioned capabilities/errors.
- [ ] Feed coverage/last-success/backoff, verified replacement sources.
- [ ] Backup, restore, consumer-safe pruning and bounded storage/load validation.
- [ ] Staged Linux upgrades, config preservation, health check and rollback.
- [ ] Private file permissions, dependency constraints and vulnerability scan.
- [ ] Total provider deadline and circuit breaker.
- [ ] Hosted data permissions documented separately from code licensing.

## Release evidence

- [ ] New icon source and packaged macOS/Linux icons inspected at small sizes.
- [ ] Native macOS build, native Linux package/installer and behavioral regression tests.
- [ ] GUI/accessibility walkthrough, real agent handoff and authenticated/self-hosted provider validation (report any actual limitation).
- [ ] README, architecture, skills, configuration, API, upgrade and release notes match delivered behavior.
- [ ] Clean source commit pushed, CI inspected, release assets uploaded and checksums/downloads verified.
- [ ] Final requirement-by-requirement review against this checklist and original audit.

Do not mark items complete until their behavior and required verification are recorded. Existing audit evidence describes the pre-fix revision, not the eventual release.

## Repair batch 1 evidence

- Native listener: 300 repeated starts and 100 stop/start cycles passed; occupied replacement preserves the original listener. Native 1 MB response cancellation survived 20 clients.
- Fetch jobs: Python and Swift HTTP tests verify immediate 202 acceptance, concurrent 409, matching job ID and eventual completed/failed status.
- Market checkpoints: both input orders and caps 0/1/2 are covered in Python; selected-symbol matching is covered in Swift.
- Linux freshness: ancient unknown-date stories expire, immutable first-seen dates remain unchanged, restored snapshots prune immediately, and carry-over counts are explicit. Mac count parity remains to implement (B14).
- Skills: explicit native installer preserves pre-existing bytes on repeated installation; startup no longer installs skills.
- Keychain: injected native OSStatus failures verify no deletion/addition after a failed update and an observable failed read. Settings now saves credentials explicitly. Actual Keychain/UI walkthrough and rotation remain open.
- CLI: generated installer wrapper tested from outside the checkout, with spaces in paths and no inherited PYTHONPATH; service-state selection verified. Full staged system installer/rollback remains open.
- Icon: original native-drawn master and 64 px preview inspected. Build output matches checked-in PNG SHA256 `a2b083c49349e0fd3c4322dbace3ec886154701765f7c7fbc0bfba7d1ec3bd63`. Final Linux and universal macOS packages remain open.
- Additional fix: native wire decoding now preserves extra fields, including market symbols.
- Build surfaced existing concurrency warnings in Hermes/OpenClaw process handling; eliminate these and bound process execution before final agent-runtime validation.

These checked defects are repair milestones, not a release approval. Remaining checkboxes and final end-to-end tests are still required.

Validation snapshot for this batch: 58 tests passed on macOS; 47 passed with 11 native-Mac skips on Pop!_OS. The final fetch-job terminal-state locking adjustment passed all four cross-runtime fetch-job tests. The Apple Silicon app builds and passes strict ad-hoc signature verification. Existing Hermes/OpenClaw concurrency warnings remain explicitly open. A Linux wheel was built and installed in an isolated venv, and its console entry point ran from `/tmp` with no PYTHONPATH. This development wheel is not the final release asset.
