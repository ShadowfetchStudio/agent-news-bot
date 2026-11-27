# Combined app release checklist

Scope: repair every finding in the November 27 four-perspective audit, implement the identified usability and operational improvements, create the app icon, update documentation, and publish tested source and downloadable packages to GitHub. A passing unit suite alone does not close this checklist.

## Defects (audit IDs)

- [x] B01: disconnected native clients cannot terminate the app; bounded responses.
- [x] B02: deterministic listener lifecycle; occupied replacement port preserves service; accurate Settings status.
- [x] B03: enrichment reaches current stories and consumers without score churn.
- [x] B04: newer corrections survive provider precedence; versioned observations.
- [x] B05: unchanged RSS feeds recover candidates after cap/lookback changes.
- [x] B06: quote checkpoints correspond to persisted observations.
- [x] B07: truthful asynchronous fetch completion, concurrency and failure semantics on both runtimes.
- [x] B08: installed Linux CLI works outside checkout and finds service state.
- [x] B09: shared URL canonicalization fixtures and existing identity migration.
- [x] B10: source failure never appears as current coverage.
- [x] B11: unknown dates and restored snapshots expire deterministically.
- [x] B12: X weighted Unicode/URL counting and parity fixtures.
- [x] B13: preserve custom agent skills; explicit managed installation.
- [x] B14: separate new/updated/carried/displayed counts.

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

## Repair batch 2 evidence

- One complete revision per changed story/transaction, with separate versioned provider observations. Swift and Python fixtures verify same-cycle enrichment delivery, later enrichment events, quiet score-only updates, correction persistence after unchanged RSS returns, multiple desk memberships and disagreement flags.
- Current story and revision-diff API routes tested over actual HTTP in both runtimes, including missing IDs, authorization and invalid limits. Mac inspector now renders provider observations and a revision timeline; visual/accessibility sign-off remains open.
- Recoverable RSS body caching tested against a real local 200/304 server on both runtimes. Widening lookback and increasing caps recover previously excluded stories. Legacy validator-only caches trigger a full fetch. Cache body/marker updates are transactional and follow journal persistence.
- Shared 19-case URL fixtures cover significant/default ports, IPv6, IDNA, path slashes, query order, duplicate keys, flags, spaces, escape case and tracking removal. Migration tests retain original story IDs and immutable events while resolving new aliases. Previously discarded URL information is explicitly documented as unrecoverable.
- Native Atom parser regressions verify alternate article links, nested summary text, source-title isolation, and rejection of HTML/error or malformed documents. Python freshness-store connections now close deterministically.
- Validation: 73 tests passed on macOS; 54 passed and 19 Mac-specific skips on native Pop!_OS. The Apple Silicon app built and passed strict ad-hoc signature verification. Existing native agent CLI concurrency warnings remain on the release checklist.

Remaining larger work includes weighted X counting, complete coverage/count reporting, setup and credential rotation, Mac refresh/restoration, searchable history/watchlists/editorial exports, consumer controls and maintenance/upgrade tooling, followed by final packaging and GitHub publication.

## Repair batch 3 evidence

- Replaced raw character counting with the same packaged twitter-text 3.1.0 parser in JavaScriptCore (Mac) and bounded thread-local QuickJS (Linux). Imported 24 official v3 cases and tested CJK, compound emoji, accents, URL preservation, oversized tags and concurrent workers. Parser failures preserve the story and disable its draft; the Mac Copy action is guarded.
- Source checks now distinguish valid empty results from request/parsing failures and report success/partial/stale/unavailable/unknown coverage. A completed job does not imply successful coverage. HTTP status/desks and JSON exports expose it; the Mac Coverage window includes per-source details and separate change/display counts.
- Fixed additional silent failures: Hacker News item fetches surface warnings, invalid index/prediction-market payload shapes fail visibly, missing weather fields cannot fabricate zero-valued forecasts, invalid quote prices are errors.
- Last completed validation: 87 tests passed on macOS; 59 passed and 28 native-Mac skips on Pop!_OS. Native app build and strict ad-hoc signing passed. The vendored npm build dependencies had no known vulnerabilities reported by npm audit at this check; the wider release dependency scan remains open.
- Remaining: actual GUI walkthrough, snapshot restoration/automatic refresh, persistent source history/backoff, setup/rotation, editorial and automation features, safe upgrades and final release verification. The coverage state describes the last attempt with explicit check times; it is not a continuous uptime assertion.

## Snapshot/refresh implementation (validation in progress)

- Added Mac startup restoration, immutable fallback-date policy, stale restored coverage, future/expired-row rejection and a 16 MiB snapshot ceiling on both platforms.
- Both runtimes retain eligible previous stories alongside new arrivals, replace corrections by stable ID, and bound displayed rows. The Mac keeps the selected story when possible and exposes displayed versus fresh status counts.
- Added configurable Mac automatic refresh (15-minute default, 5–120 minutes) and a guarded one-time engine start. Actual GUI and timer walkthrough remain required before that checklist item is complete.
- Added an explicit isolated native testing namespace: AGENT_NEWSBOT_HOME controls data, preferences and the Keychain service independently of the production app.
- Five targeted snapshot/isolation tests passed on Mac, including pre-existing Linux restoration expiry coverage. Full-suite/build results are recorded after completion below.

Snapshot batch validation: 93 tests passed on macOS; 60 passed with 33 native-Mac skips on Pop!_OS. The Apple Silicon app built and passed strict ad-hoc signing. Snapshot policy and isolated namespaces are verified; the actual GUI/timer walkthrough and existing agent subprocess warnings remain open. The previous Linux development wheel was separately installed outside the checkout and verified to contain/run the shared parser (CJK regression returns 280); final release assets will be rebuilt after all remaining changes.

## Agent handoff repairs

- Replaced both unbounded native CLI paths with an owned-process-group runner: 120-second deadline, 2 MiB output ceiling, cancellation and descendant cleanup, concurrent pipe draining and child reaping. Removed blocking subprocess-based executable discovery.
- Native process fixtures passed for successful output, nonzero exits, missing executable, output flood, timeout cleanup of a child and cancellation. Native HTTP fixtures passed for successful draft handoff, redirect rejection, oversized-response rejection and unauthorized readiness.
- Hermes API handoffs now have total resource/response bounds and refuse redirects. OpenClaw gateway-only detection no longer enables an unsupported CLI send; invalid ports are rejected without integer conversion traps. Shared mutable completion captures were replaced with synchronized state.
- Actual installed-agent compatibility and GUI handoff remain open. Controlled fixtures are not an actual agent acceptance.

Latest native validation: 101 tests passed; the Apple Silicon app built without compiler warnings and passed strict ad-hoc signature verification. The prior Linux validation remains 60 passed/33 native-Mac skips; changes since then are native agent execution and native-only tests. No production app was installed or user data overwritten. No release assets have been published yet.
