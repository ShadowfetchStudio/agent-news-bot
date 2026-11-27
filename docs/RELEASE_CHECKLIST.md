# Combined app release checklist

Scope: repair every finding in the November 27 four-perspective audit, implement the identified usability and operational improvements, create the app icon, update documentation, and publish tested source and downloadable packages to GitHub. A passing unit suite alone does not close this checklist.

## Published release — November 27, 2026

[Version 1.2.0](https://github.com/ShadowfetchStudio/agent-news-bot/releases/tag/v1.2.0) is public. Tag, tested packages and embedded build metadata identify source commit `86785a1628537ea7224878368b6c7c987529a92c`, merged through PR #1. Subsequent documentation-only commits record the publication evidence; they do not change the released executable.

All five downloaded release assets match the tested local copies byte for byte. [Package evidence](audits/2026-11-27/release-packages.json) includes hashes, sizes, archive inspection, extracted-Mac launch/API checks and native Pop!_OS installation/upgrade/rollback results. [Release-preparation CI](https://github.com/ShadowfetchStudio/agent-news-bot/actions/runs/46051062319) passed 439 Mac tests, 270 Linux tests (169 Mac-only skips), dependency audit, native build and signature checks.

The Mac download is explicitly **ad-hoc signed, not notarized**, as authorized by the owner. Full VoiceOver speech, actual optional agent/model accounts, authenticated commercial World Monitor, clean-account Gatekeeper acceptance, root Linux and native Intel execution remain unverified. These are stated release limits, not passed checks; see [release notes](RELEASE_NOTES_1.2.0.md). The checklist closes the implemented scope and recorded validation disposition. Historical milestone sections below retain their original pre-release status statements.

## Follow-up four-perspective audit

The [current-source follow-up audit](audits/2026-11-27/reaudit/AUDIT.md) found nine additional defects after the original repair batches. These are separate from B01–B14; passing those regressions does not close the findings below.

- [x] R01: fail visibly and preserve data when native bridge credentials cannot be persisted; stable cached credential and explicit rotation.
- [x] R02: distinguish agent detection from compatible readiness and reject invalid/empty completion payloads.
- [x] R03: prevent unrelated events with similar wording from silently merging; preserve recoverable group identities/history.
- [x] R04: reject non-HTTP(S) news links at ingestion and every desktop/export boundary.
- [x] R05: enforce each desk's display cap after fresh/carry-over merging and restoration.
- [x] R06: bound Linux HTTP connections, header/body deadlines and stalled writes.
- [x] R07: return a controlled unauthorized response for malformed/non-ASCII credentials.
- [x] R08: enforce byte and total-time limits while receiving every provider response, not after buffering.
- [x] R09: surface native snapshot/export persistence failures without losing the preceding valid snapshot.

Follow-up evidence: current Mac suite 127 passed; synchronized native Pop!_OS suite 80 passed/47 Mac-only skips; Apple Silicon build and strict ad-hoc signature verification passed. New isolated fixtures reproduce R01–R08 (R04's desktop dispatch is code-confirmed, not executed; R08 total-time risk is code-confirmed). R09 is code-confirmed. The live 54-feed sweep returned 48 feeds with parseable entries, five request/HTTP failures and one valid empty feed. This remains a development checkout, not the final published release.

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

- [x] First-run mode selection, connection test and actionable provider errors.
- [x] Safe observable credential storage, failure preservation and rotation.
- [x] Supported private Linux config and service wiring.
- [x] macOS automatic refresh and restored batch.
- [x] Disagreement/correction timeline, multiple desks and durable story detail.
- [x] Manual story-group split/merge with preserved identities, event history and explicit editorial review of legacy groups.
- [x] Saved searches/watchlists, entity keyword filters, bookmarks and source muting.
- [x] Persistent saved-search alerts and correction-review queue, with explicit seen versus approval state.
- [x] Source-quality explanations without inferred factual verification.
- [x] YouTube/X/TikTok draft templates, editorial approval state, ranked briefs and manual VideoBot Inspect URLs export.

## Automation and maintenance

- [x] Durable story lookup, revision differences, source/provider filters.
- [x] Consumer status/lag/ack, onboarding from history/date/now, replay/reset and optional worker leases (API and Linux CLI).
- [x] Reference idempotent retrying consumer and versioned capabilities/errors.
- [x] Feed coverage/last-success/backoff, verified replacement sources.
- [x] Backup, restore, consumer-safe pruning and bounded storage/load validation.
  - Research/application recovery, backup-gated pruning, configurable ingestion budgets and operator observations are implemented and tested. This is an ingestion ceiling, not an overall filesystem quota; protected research, manual writes, caches, WAL and external archives have the documented scope in `OPERATIONS.md`. Bounded synthetic load evidence is recorded below.
- [x] Staged Linux upgrades, config preservation, health check and rollback (native user-service verification; root execution limit below).
- [x] Private file permissions, dependency constraints and vulnerability scan.
- [x] Total provider deadline and circuit breaker.
- [x] Hosted data permissions documented separately from code licensing (documentation only; no account or publication clearance).

## Release evidence

- [x] New icon source and packaged macOS/Linux icons inspected at small sizes.
- [x] Native macOS build, native Linux package/installer and behavioral regression tests.
- [x] Native GUI/keyboard walkthrough completed; VoiceOver speech, actual optional agent and authenticated commercial provider acceptance explicitly recorded as unverified in release notes.
- [x] README, architecture, skills, configuration, API, upgrade and release notes match delivered behavior.
- [x] Clean source commit pushed, CI inspected, release assets uploaded and checksums/downloads verified.
- [x] Final requirement-by-requirement review against this checklist and original audit.

Do not mark items complete until their behavior and required verification are recorded. Existing audit evidence describes the pre-fix revision, not the eventual release.

## Follow-up safety repair evidence

- R01: the Mac caches one persisted bridge token and fails visibly on invalid/unreadable storage. An atomic private helper preserves existing bytes, bounds lock acquisition and supports explicit reload/rotation. Native tests cover 24 concurrent creators, stale rotation, invalid/directory/symlink destinations, private mode, and a real listener rejecting the former token and accepting the new saved token.
- R04: common HTTP(S) article validation now covers RSS/HN/digest ingestion, journal inputs, active snapshot restoration, native opening/source links, agent handoff, drafting/approval and exports. Cross-runtime fixtures include credentials, control/encoded characters, non-web schemes, invalid ports/hosts, IPv6 and IDNA. Existing raw historical events remain untrusted forensic evidence; they are not rewritten. Library warnings identify unsafe retained source links and exports reject them.
- R03 automatic matching is repaired: distinct actors/countries, numbers, dates, locations and prepositions no longer merge through loose word overlap; equivalent wording still groups, and same-URL corrections retain identity and immutable previous events. The later manual-repair batch below adds explicit split/merge with audit history.
- The eight Python runtime dependencies now have version/hash locks, PyPI metadata provenance and a CI advisory audit. Build/test toolchains are explicitly outside that lock. The checked runtime and npm audit results reported no known findings.
- Python World Monitor now has an isolated worker, 4 MiB stream cap and a 20-second parent deadline. Real oversized-valid-JSON and trickling-server tests verify rejection and timeout child reaping. Ordinary direct providers and persistent circuit breakers remain open.
- Final batch validation: **154 passed on macOS; 93 passed / 61 native-Mac skips on Pop!_OS**. A final encoded-authority rejection refinement passed both Mac URL fixtures and the native Linux policy fixture. The Apple Silicon app rebuilt without warnings and passed strict ad-hoc signature verification. These checks use isolated state; no production service replacement or public release was performed.

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

## Research library and editorial batch

- Added persistent search/filter/pagination, bookmarks, saved watchlists, exact-source mutes and transparent review-priority ordering to both runtimes. Muting only hides groups whose sources are all muted and never changes consumer events. Entity filtering is explicitly keyword matching, not NER.
- Added five-field editable platform templates, source-bound approval and optimistic concurrency using both story revision and editorial version. Later corrections produce needs-review; invalid or stale edits roll back. Added authenticated bounded-body API routes and Linux CLI workflows.
- Added atomic private per-export research folders with saved drafts, full provenance and source URLs. VideoBot handoff is its documented Inspect URLs workflow; no unsupported project importer, media-rights assumption or automatic queueing is claimed.
- Native UI checked using isolated fixture data/preferences: retained-story search, bookmark save, template generation, revision approval, correction to needs-review, summary/source links, weighted X counts, unsaved Done/Reload confirmation, keep-editing preservation and saved watchlist visibility. Screenshots at 1000×650 (library) and 850×660 (editor) showed readable controls without overlap. This is a scoped walkthrough, not complete VoiceOver/keyboard acceptance or a real agent/provider validation.
- The UI check identified two extra issues now addressed: editor lacked original story evidence; returning from an editor could leave old row status. Added Story tab and reload-on-dismiss. A failed bridge bind now shows Offline in the sidebar instead of a misleading default URL.
- Full automated validation: **110 passed on macOS**, **65 passed / 45 native-Mac skips on Pop!_OS**. Includes actual HTTP auth/validation/conflict tests in both runtimes, private non-overwriting file exports, and CLI from outside checkout with corrupted-journal error handling. Native build passed without warnings. The final UI-only reload-on-dismiss/offline-label adjustment is followed by a separate build/signature check.
- Still open within the larger editorial checkbox: ranked briefing output beyond story priority. Source timeline is implemented and tested at API level; its native expanded-history interaction remains part of final GUI acceptance. Wider setup, consumer controls, maintenance, real integrations and final release remain open.

## Consumer recovery batch

- Added per-consumer backlog, highest offered position, last acknowledgement time and generation; explicit history/date/now onboarding and reset; optional 10–300 second exclusive worker leases with renewal/release/expiry.
- Reset, acquisition and release fence older workers by advancing generation and clearing old offered ranges. Active leases require the matching token for reads/acks. Status never exposes tokens. Legacy generation-zero clients retain their old contract until explicitly reconfigured.
- Unknown event filters now fail instead of implying delivery of a filtered subset. Date onboarding selects a start sequence and delivers the full suffix; no hidden gaps can be acknowledged.
- Added a foreground reference consumer with bounded retries and private transactional SQLite inbox. Tests simulate a crash after commit/before ack, prove replay idempotency, reject changed event identities and prevent ack after failed writes. It does not perform external publishing; downstream side effects require their own idempotency/outbox contract.
- Real HTTP tests on Swift/Python cover create/reset, stale generation, missing generation, prior offered-range invalidation, competing leases, renew/release, expiry, date start and status privacy. Wider versioned capability/error discovery and full native automation UI remain open.

Consumer batch validation: full Mac integration suite **112 passed**, plus **3 passed** for the newly added reference-consumer tests; native Pop!_OS full suite **69 passed / 46 native-Mac skips**. The Apple Silicon app built without warnings and passed strict ad-hoc signature verification. A separate CLI regression checks administration from outside the checkout. These remain development builds; release packaging, authenticated provider/real agent acceptance and publication are still pending.

## Staged installation and private configuration batch

- Replaced in-place Linux installation with independent permanent release environments, dependency/import preflight, atomic current/previous links, exclusive installer locking, managed runtime drop-in, PID-matched localhost health acceptance and failure rollback. Explicit `--rollback` reactivates the prior staged version through the same checks. Existing configuration/main-unit/custom CLI data are preserved; recovery records are unique per transaction.
- Added a private non-executable configuration loader, systemd EnvironmentFile wiring and installed CLI agreement. Source-tree setup instructions now install the entire package instead of omitting parser dependencies.
- Existing invalid Linux bridge tokens fail visibly instead of silently rotating. New tokens are exclusively created at 0600. CLI uses umask 0077; Mac/Python databases are prepared at 0600 before SQLite opens them, reject symlinks and retain existing bytes. New application directories use 0700. The dependency vulnerability scan remains separately open.
- The first native installer test caught EnvironmentFile quoting that systemd interpreted literally. The generated path was corrected and validated with a filename containing spaces; the failed attempt returned failure and removed only its own test service.
- Native Pop!_OS systemd **user-service** smoke test passed initial install, staged upgrade, configuration and consumer preservation, explicit rollback in both directions, injected broken-release failure, automatic rollback and restored-service health. Its unique temporary service was removed. Recovery/report artifacts were retained in the isolated test directory. Root-system-service installation has not been run on the user's machine; system unit generation/identity is covered by code review and automated preservation/rollback tests, not claimed as native root acceptance.
- Full Mac regression suite: **124 passed**; app build without warnings and strict ad-hoc signature verification passed. This remains a development build and is not a notarized/published release.
- Install-time journal backups are now available. They do not close the separate full backup/restore, pruning and storage/load requirements. Old release/recovery directories are retained deliberately until explicit retention tooling is delivered.

Native Linux regression result for this batch: **77 passed / 47 native-Mac skips**. A final focused installer/configuration run additionally verifies package-staging umask isolation. Successful native service validation covered isolated test data only; no production news service was replaced.

Final token regression: concurrent creators publish one complete private token without overwriting another creator; invalid existing bytes remain unchanged and fail visibly. Targeted Mac/Linux tests passed.

Final installer acceptance also checks the process command line uses the selected current interpreter and rejects Python path/home overrides that could load another runtime. The native smoke sequence passed with this check. A focused regression verifies that a late CLI-write failure restores the previously disabled service state as well as the old release. Native root-system execution and vulnerability/constraint work remain explicitly unclaimed.


## Follow-up reliability repair batch

- R05: independent per-desk selection after fresh/retained merging, with deterministic date/ID order. Display-only `display_desks` keeps full source membership intact while preventing shared stories from overflowing a full desk. Sidebar, current API filters, categories and restored counts use the same selection. History/events are unaffected.
- R06/R07: Linux production listener limits active workers to 16, rejects overflow with 503/Retry-After, enforces absolute header and request deadlines, and bounds stalled reads/writes. Non-ASCII authentication headers return 401. Real-socket tests cover idle headers, trickled headers/bodies, stalled readers, capacity recovery and shutdown. A socket deadline does not roll back a mutation already executing.
- R09: native private snapshot writer validates the 16 MiB limit, writes/fsyncs complete staging bytes, publishes a unique archive and atomically replaces latest. Failed commits preserve the preceding snapshot and clean the newly created archive/staging file. A persistent warning and Retry save control accompany the API's `snapshot_error`; failed bridge refresh jobs stay failed even if a later save retry succeeds. Archive pruning remains best-effort; power-loss durability is not claimed.
- Native filesystem fixtures exercise simulated ENOSPC, oversized payloads, directory/symlink destinations, private permissions, unique successive exports and retry preservation.
- Actual isolated Mac app walkthrough: disabled provider desks and agent CLI use; forced EACCES on only the temporary export directory; HTTP fetch job failed, API exposed snapshot_error and previous bytes were preserved. Inspected the visible warning and Retry save button at 1100 x 752. Restored directory access, clicked Retry save, observed the success message and warning removal, verified the saved 0600 JSON and cleared API error while preserving the original failed job. Stopped the test app and removed only its isolated preferences afterward. Evidence retained in `/var/folders/4m/7t5q5j0x6fz8ybxfyr6197nr0000gn/T/anb-snapshot-ui-jclap3fc/`.
- Validation: full macOS suite **174 passed**; isolated native Pop!_OS suite **107 passed / 67 native-Mac skips**. Apple Silicon build completed without warnings and passed strict ad-hoc signature verification. This targeted UI check does not close the full keyboard/VoiceOver/installer acceptance items.

R02, the manual repair portion of R03, and direct-provider bounds in R08 remain open, along with the wider setup, maintenance, real integrations and final distribution checklist. No final release assets have been published.


## Agent readiness and provider transport repair batch

- R02: Hermes discovery now checks a bounded, authenticated `/v1/models` response and the configured model. An unrelated health response, missing model or CLI executable alone cannot claim a verified reply. Settings offers discovery without inference and an explicit fixed-prompt reply test; successful replies are timestamped for the current configuration/session. Model/URL/mode/credential edits invalidate verification. CLI handoffs reject empty output, API handoffs require nonempty assistant text with a completed stop result, and error/cancelled/length responses are not successful handoffs. Settings explains saved credentials and possible model charges. OpenClaw gateway reachability remains distinct from CLI handoff support.
- Added a configurable Hermes model and normalized origin versus `/v1` base URLs. Removed the stale README claims that a local Hermes installation had been detected, that gateway-only OpenClaw handoff worked, and that startup installed skills.
- R08: all native direct-provider calls use decoded streaming limits (4 MiB) and URLSession resource deadlines. Linux direct providers use owned per-request workers with decoded streaming limits and a parent deadline covering DNS, redirects, headers and body; timed-out workers are killed/reaped. Normal direct deadlines are 14 seconds, with existing 8/12-second source-specific settings. Five redirects maximum; no HTTPS downgrade. Agent requests refuse redirects, use separate 256 KiB discovery/2 MiB reply bounds and 3/120-second deadlines. World Monitor keeps its existing 4 MiB/20-second boundary.
- Real HTTP fixtures cover unrelated/empty/invalid model lists; custom model and `/v1` base; incomplete/cancelled/error/empty/non-assistant completions; explicit acknowledgement matching; actionable 401/403/404/429 without echoing provider error bodies; 6 MiB streaming and compressed payloads; slow trickled bodies; redirect loops; retained Retry-After; and timeout worker reaping. Existing real 200/304 feed-cache tests continue to pass.
- Full validation: **204 tests passed on macOS; 113 passed / 91 native-Mac skips on Pop!_OS**. Final Apple Silicon app builds without warnings and passes strict ad-hoc signature verification. A build that overlapped a UI source edit correctly failed and was replaced by the final stable-source build.
- Isolated actual Settings walkthrough at 520 x 592: compatible model list displayed Reply not tested; Test Hermes reply against a local fixture displayed a verified timestamp; editing the model immediately invalidated that state; a second fixture reply of `{}` displayed an explicit failure. Fixture request log confirmed exactly two fixed prompts and no story material. Test app/server stopped and isolated preferences removed. Evidence: `/var/folders/4m/7t5q5j0x6fz8ybxfyr6197nr0000gn/T/anb-agent-ui-rg3kva10/`.
- Actual Hermes/OpenClaw executables were not found on the checked Mac or Pop!_OS PATHs; the usual Mac Hermes location was absent. Controlled protocol/UI checks are not a live model/provider acceptance. That limitation remains in the final integration gate.

The manual repair portion of R03 remains open. Source backoff/circuit breakers, setup, backup/restore/retention, deeper editorial work, complete release documentation and final distributable publication remain part of the goal.

## Manual grouping repair batch

- Added matching Swift/Python transactional split/merge, canonical current membership, stable old IDs/aliases, immutable previous events, preserved provider observations and per-story drafts/notes, and explicit needs-review invalidation. Retired merge sources remain readable but cannot be edited/approved/exported as active work. Manual groups are excluded from automatic title matching; same-URL corrections continue.
- Receipt IDs support exact-request idempotency. Both story and editorial versions are compared in one transaction, and conflicting retries, stale destinations, inconsistent membership and invalid inputs are rejected. A trigger-induced failure after the first event/membership writes proves full rollback, including the receipt table.
- Added native split checkboxes, merge destination preview, reason/confirmation and durable receipt display; library inclusion of merged history; related IDs/reason in the Story tab. Added authenticated API routes and a Linux CLI applying repairs through the running localhost service. CLI receipt reads work offline.
- Live/restore reconciliation resolves split descendants and merged destinations with cycle protection, then reapplies expiry/desk caps. Mac bridge repairs synchronously refresh the native batch. Linux repairs serialize with fetch startup and reject an in-progress fetch. Persistence failures after a committed repair are visible and retryable with the same operation ID without duplicate events.
- Full automated verification: **219 passed on macOS; 121 passed / 98 native-Mac skips on Pop!_OS**. The app built without warnings. Additional focused checks replay receipts between runtimes. Native root/system service installation, real agents/providers and final release packaging remain separate gates.
- Isolated Mac UI walkthrough passed a split, a merge back, library row refresh (two active rows then one), retained-history inclusion, readable source/related IDs and repair reasons, and disabled export/save for a retired group. A subsequent authenticated API split immediately returned two live stories and persisted the same batch; original research notes remained intact. Evidence: `/var/folders/4m/7t5q5j0x6fz8ybxfyr6197nr0000gn/T/anb-group-ui-_n7s4elb/verification.json` and its isolated journal. The test app was stopped and its test-only preferences removed.
- The walkthrough identified two UI refinements: clear library selections no longer present after filtering/repair, and open on All stories when a restored selected desk is disabled. Retired draft fields and editorial-state controls are disabled; the reason field has an explicit accessibility label. Final build passed without warnings and strict ad-hoc signature verification passed. Scoped UI recheck showed the restored story in the main list and both story actions disabled after filtering away the selected row; evidence is in `/var/folders/4m/7t5q5j0x6fz8ybxfyr6197nr0000gn/T/anb-group-ui-coluguh_/verification.json`. Test app/preferences were cleaned up.

This closes R03 and the manual grouping checkbox, not the broader release objective. First-run experience, ranked briefs, capabilities/errors, source resilience/replacements, backup/restore/retention/load, full keyboard/VoiceOver and optional real integrations, final icons/packages/docs/release upload remain tracked above.

## API discovery and reliable-client batch

- Added authenticated capability discovery with API/contract/error-schema and feature versions, canonical routes/methods, limits, authentication/delivery requirements, and explicit limits on publishing/dashboard/fact-check/rights capabilities. This is implemented-support discovery, not a claim of live source/model readiness.
- Both final response writers preserve legacy `error` strings and add matching versioned codes, HTTP status and recovery guidance. Malformed JSON, wrong methods, auth failures, conflicts, storage failure, response-size rejection and connection overload share the envelope. Python parser errors are JSON too; HEAD sends no response body. Dead socket/header failures can still close without a response and remain unknown outcomes.
- Reference consumer negotiates before creating/leasing, disables environment proxies, retries only reads and generation-bound acknowledgements, and rejects malformed/incomplete/out-of-order pages before committing or acknowledging. Ambiguous administration stops for reconciliation. Real Swift/Python HTTP tests run it twice and verify a single private inbox record with no duplicate delivery effects.
- Updated shipped/generated agent descriptors to include capability/error/generation handling, and removed stale claims that empty output proves current coverage. Existing installed custom descriptors remain preserved.
- Full Mac suite: **230 passed**. Native Pop!_OS suite: **131 passed / 99 Mac-only skips**. An initial Linux copy lacked `build.sh` needed by the new packaged-version consistency fixture; copying the missing test input resolved the failure and the full suite passed.
- Apple Silicon build completed without compiler warnings and passed strict ad-hoc signature validation. An isolated run of the actual built app returned authenticated capabilities, structured 405 errors and bodyless HEAD responses. Evidence: `/var/folders/4m/7t5q5j0x6fz8ybxfyr6197nr0000gn/T/anb-group-ui-3uw2igdd/contract-verification.json`. The fixture app was stopped and its private test preferences removed.
- A Linux development wheel was built and installed into `/tmp/anb-contract-installed`; from outside the checkout, its packaged capability module and server imported with the expected app version. This is package inclusion evidence, not the final public release artifact.

First-run setup, source resilience, maintenance/retention/load, deeper editorial work, remaining accessibility/real integrations and final release deliverables remain open in the checklist.


## First-run setup and connection diagnostics

- New Mac installations choose direct sources alone or direct sources plus their own/authorized hosted World Monitor API. They select desks and automatic refresh. News fetch and agent discovery wait for saved setup. Existing preferences migrate without reset; explicitly unfinished setup survives a restart. Keychain failures preserve credentials and block enabled-connector completion; direct-only setup leaves the key unchanged.
- Settings and first-run setup offer an explicit digest connection test. It uses entered values without saving credentials or ingesting research, reports coverage separately from connectivity and clears its session result on edits. Linux exposes the same test through `agent-newsbot test-worldmonitor` with JSON and success/failure exit codes. No CLI key argument is needed.
- Both adapters distinguish invalid configuration, HTTP authentication/permissions/missing endpoints/rate limits/provider errors/redirects, TLS validation, deadlines, oversized responses and incompatible payloads. Raw provider errors and credentials are not displayed. Python World Monitor no longer inherits environment proxies. Existing byte/deadline/no-redirect bounds remain intact.
- Automated validation before the final layout-only adjustment: **251 passed on macOS; 133 passed / 118 native-Mac skips on Pop!_OS**. Migration fixtures verify clean, unfinished, completed and existing configurations. Real local HTTP tests compare both runtimes for partial/empty/stale digests and error responses, verify key redaction and no redirect follow, and reject invalid configuration before network access. The Linux CLI test verifies no research/snapshot creation.
- Actual built-app walkthrough: inspected the first-run mode choices, local 401 guidance, compatible partial coverage, timestamp, result invalidation after an origin edit, successful setup completion and Settings status at 520 x 592. Two explicit provider test requests produced no story schema or exported snapshot. Automatic refresh remained off in the final isolated fixture; its saved setup and origin were verified. Evidence: `/var/folders/4m/7t5q5j0x6fz8ybxfyr6197nr0000gn/T/anb-setup-ui-rs0a21e6/verification.json`. Test app stopped and isolated preferences removed. A subsequent layout adjustment removes an empty form row; final validation is recorded below.

Persistent source health/backoff, backup/restore/retention/load, richer editorial briefs/platform approvals, full accessibility/real integrations and final release packages remain open.

Final setup-batch verification: the rebuilt Apple Silicon app passed strict signature verification without compiler warnings. The final provider form was visually inspected with the empty row removed; 401 guidance and result clearing after editing still worked. Evidence: `/var/folders/4m/7t5q5j0x6fz8ybxfyr6197nr0000gn/T/anb-setup-ui-3zii2o8r/verification.json`. The isolated app/preferences were cleaned up. Final focused Mac connection/migration/transport checks: **25 passed**; native Linux CLI/diagnostics/deadline checks after the worker refinement: **3 passed**. These checks do not claim real hosted-provider access or full VoiceOver acceptance.


## Persistent source health and resilient collection

- Added matching private SQLite source-health stores on Mac/Linux with source/configuration hashes, last attempt/full success/usable response, failure streak and earliest retry. State survives restart. Transactional five-minute probe leases and fencing prevent a stale worker from overwriting a newer probe; expired probes are recorded as interrupted. Identity count is capped at 1,024 and errors are bounded internal codes.
- Ordinary RSS, Hacker News, Polymarket, Yahoo markets, Open-Meteo and World Monitor collection participate. RSS keys include the desk so a shared feed still populates both desks. Coverage distinguishes different health IDs instead of conflating identical display names. New source-health storage failures prevent requests; a post-retrieval health write failure retains stories and adds a visible warning.
- Consecutive request/stale/unavailable failures use 60-second exponential backoff up to six hours. HTTP Retry-After seconds/dates can extend it (24-hour bound). Three transport failures or future Retry-After stop additional HTTP operations inside a provider check. Healthy partial responses continue ordinary polling and preserve partial coverage rather than claiming full success. Explicit World Monitor setup tests remain one-request diagnostics outside scheduled health.
- Authenticated GET /v1/sources and Linux source-health CLI expose bounded history, and capability discovery advertises the feature. The Mac Coverage panel shows reloadable historical status, retained success times, failure codes and retry times. API validation covers authentication, methods, unexpected parameters, malformed storage and matching offline CLI output.
- Real network regression cases verify repeated rate-limited fetches make only one request across restarted runtimes, multi-request providers stop after the configured failure threshold, and distinct desk memberships remain populated. Deterministic cross-runtime cases cover exponential delays, partial responses, date/seconds parsing, fencing, cancellation, 16 concurrent claimers, corrupt/symlink/directory/invalid-schema storage, capacity and database interoperability.
- Bounded live recheck reconfirmed two 404 feeds and two unreachable AP endpoints. Replaced Yahoo Finance RSS and Medical News Today RSS with publisher-documented Federal Reserve and National Cancer Institute release feeds (HTTP 200, 20/10 parsed entries), clearly labeled as institutional releases. Removed the repeatedly unreachable AP defaults without deleting old research or claiming permanent retirement. Observation times, byte counts and successful response hashes are in audits/2026-11-27/feed-replacements.json; rationale and primary publisher links are in SOURCE_HEALTH.md.
- Full suites before the final partial-coverage policy refinement: **293 passed on macOS; 155 passed / 138 Mac-only skips on native Pop!_OS**. The final policy/source-health suite passed **44 Mac tests; 23 Linux tests / 21 Mac-only skips**. Apple Silicon build completed without warnings and passed strict ad-hoc signature verification.
- Actual isolated Mac walkthrough displayed an open circuit with failure streak 3, last successful check retained and a future retry; a healthy source; and a partial source showing Never for full success and a usable-response time. Authenticated built-app /v1/sources agreed. Reload showed a newly added fourth source without fetching. Evidence: `/var/folders/4m/7t5q5j0x6fz8ybxfyr6197nr0000gn/T/anb-health-ui-1v0xp1s7/verification.json`. The app was stopped and isolated preferences removed. The initial accessibility aggregate lagged after insertion despite correct visual rows; stable row IDs and explicit accessibility grouping were added for the final recheck.

Source-history retention is still part of the broader maintenance work. This closes the source health/backoff/replacement and provider-bound/circuit checkboxes, not the full release, recovery/load, editorial, accessibility, real-provider or final artifact gates.

Final source-health UI verification: stable identity and accessibility groups correctly exposed the newly inserted fourth row after Reload, without stale aggregate text. The final Apple Silicon build passed without warnings and strict signature verification passed. Evidence: `/var/folders/4m/7t5q5j0x6fz8ybxfyr6197nr0000gn/T/anb-health-ui-gwi1mk51/verification.json`. Test app stopped and isolated preferences removed. This is scoped accessibility-tree verification, not a complete VoiceOver walkthrough.

## Research recovery batch

- Added standard-library-only `backup`, `verify-backup`, and `restore-research` commands, accessible through the installed Linux CLI or the helper packaged in the native Mac app. Explicit homes/destinations work even with invalid app configuration or unavailable runtime packages. Recovery requires Python 3.10+; ordinary native Mac use does not.
- SQLite online backup includes committed WAL records and excludes uncommitted work. The private, versioned manifest records per-file SHA-256/bytes/table counts and an explicit per-database consistency boundary. Verification checks integrity, schema/JSON/references/checkpoints and rejects unexpected sidecars, links, incomplete folders and tampering. Source files are not replaced.
- Restore requires a new folder, verifies copied bytes again, clears probe/consumer leases, advances backed-up generations and resets offered positions to acknowledged cursors. The recovery receipt fingerprints the resulting files. Credentials, configuration, exported snapshots/files and downstream inboxes/outboxes are explicitly excluded. Reconnecting older consumer checkpoints requires reconciliation; recovery does not promise exactly-once external effects or fence every later generation.
- Interrupted work retains an incomplete marker. Native/Python database access refuses incomplete homes; native startup/fetch reports the problem. No automatic deletion of partial folders or replacement of the live app/service occurs. Byte/time bounds and a free-space reserve fail visibly.
- Final local validation: **317 passed on macOS; native Pop!_OS 176 passed / 141 Mac-only skips**. The 22 recovery cases cover both database runtimes, WAL consistency, credentials excluded, preserved drafts/preferences/revisions, leases/cursors, existing folders, tampering, schema/JSON/orphans, deadlines, byte limits, changed files during restore, incomplete-home protection and standalone/CLI operation without site packages. The earlier source-health commit also passed all three GitHub CI jobs (Mac: 295; Linux: 156 passed / 139 skips).
- Apple Silicon app rebuilt without warnings and passed strict ad-hoc signature verification. Its packaged helper hash matches source and successfully verified the independent load fixture. This is not notarization or a final release artifact.
- Bounded synthetic recovery trial: 3,000 stories and 3,000 events, 52,789,248 database bytes; Mac backup 0.102 s, verify 0.046 s, restore 0.153 s. An acknowledged cursor of 1 retained lag 2,999 after restore. Measurement: `/var/folders/4m/7t5q5j0x6fz8ybxfyr6197nr0000gn/T/anb-recovery-load-02ah7ooi/measurement.json`. These warm local timings do not establish months-long collection throughput, retention safety or slow-disk performance.

This closes the research-data recovery portion only. Consumer-safe pruning, broader configuration/export recovery and sustained storage/load behavior remain open, along with editorial enhancements and final distribution/integration gates.

Recovery packaging check: a development wheel built through an isolated build backend, installed into a new venv with no runtime dependencies, and its CLI verified the 3,000-story archive from `/tmp` with no PYTHONPATH and an intentionally missing provider config. The native packaged helper SHA-256 matches source (`770f97312e28d9b045342578bd3cae95145282be6c18c172d207824c5384a1cf`). No production installation was replaced.

## Independent platform approval

- Added explicit YouTube/X/TikTok/narration decisions, bounded partial draft updates and capability discovery. Whole-package approval retains its all-platform meaning. Existing records inherit their prior aggregate decision; saves preserve ordinary notes/bookmarks and unchanged platform approvals. Source revisions invalidate approved decisions, and manual split/merge explicitly clears every affected platform approval including child copies.
- Native tabs expose saved platform state, Save and approve, and Draft/Needs review/Rejected actions. Ordinary Save changes sends actual text changes and only explicitly changed package decisions; editing X cannot silently reapprove itself or revoke unchanged YouTube text. Whole-package edits and platform decisions are mutually exclusive in one request. Regenerating templates invalidates all approvals.
- Library Needs review filtering includes partially approved stories after corrections. Research JSON includes `platform_approvals`; exported Markdown lists each decision. Legacy top-level `approved` remains false for X-only approval. API/CLI changes retain both optimistic concurrency versions and rollback invalid multi-platform decisions atomically.
- Full suites before the final legacy all-platform reapproval refinement: **329 Mac tests passed; native Pop!_OS 182 passed / 147 Mac-only skips**. Final focused tests additionally verify reapproval updates the legacy package source revision and both runtimes' exported files distinguish X-only approval. Final test totals are recorded below.
- Actual isolated Mac UI at 850 x 660: approved X with the other drafts empty; independently filled/approved YouTube; changed/saved X and observed X Draft while YouTube remained Approved. An injected source correction produced revision 2 / Needs review on reload, then explicit YouTube reapproval saved revision 2. Authenticated API/export reads matched the UI. Evidence: `/var/folders/4m/7t5q5j0x6fz8ybxfyr6197nr0000gn/T/anb-platform-ui-5lmrnr3_/verification.json`. The app was stopped and its isolated preferences removed. No production research or social account was changed.
- Final Apple Silicon rebuild passed without warnings and strict ad-hoc signature verification passed. README, research/export/API documentation and the shipped agent skill describe the platform contract. This is scoped UI/accessibility-tree verification, not full VoiceOver acceptance.

Ranked editorial briefs, broader follow-up workflows, retention and remaining release/integration/distribution gates stay open. The larger editorial checkbox is not closed by platform approval alone.

Final platform-approval focused verification: **16 passed on Mac; 8 passed / 8 Mac-only skips on native Pop!_OS**. The final full commit is covered by GitHub CI in the PR; local full-suite counts above identify their exact scope rather than implying a rerun after the final two cases.


## Source-linked editorial brief batch

- Added matching deterministic Swift/Python briefs: retained report, prior-event headline/summary comparisons, added/removed sources, attributed provider observations, difference flags, unknowns, follow-up questions, saved notes and transparent review-priority components. These are retained reports, not verified facts or finished scripts; no model/provider call is made.
- Reads pin story, editorial state and prior events to one SQLite snapshot. Research exports use that same snapshot and add structured `brief` plus `brief.md` alongside the three existing files. A concurrent external correction/notes write cannot contaminate an in-flight export. Alias IDs resolve without changing editorial state or consumer progress.
- Authenticated `GET /v1/library/brief?id=ID`, capability `editorial_brief: 1`, Linux `brief ID` and native Brief tab. Evidence is capped at 20 source links/eight observations, text at 2,000 Unicode code points, with explicit excerpt markers. Markdown escapes source text; native source actions retain the shared URL validator.
- Full local Mac regression suite: **341 passed**. Native Pop!_OS: **188 passed / 153 Mac-only skips**. Three additional alias/snapshot cases were then added: final focused Mac **13 passed**; native Linux **7 passed / 6 Mac-only skips**. The source-equivalent final full CI counts are expected to include those extra cases, but are not claimed until observed.
- Native Apple Silicon build completed without warnings and strict ad-hoc signature verification passed. The first accessibility refinement exposed a SwiftUI accessibility recursion crash when expanding a labeled disclosure. Replaced that group with an explicit labeled expand/collapse button; repeated expansion/collapse, both sources, accessibility reads and source layout passed on the rebuilt app. This is focused accessibility acceptance, not a full VoiceOver walkthrough.
- Actual Mac Export action created four private files; `brief.md` equals `research.json.brief.markdown`, and the live authenticated brief matches the export. Fixture evidence: `/var/folders/4m/7t5q5j0x6fz8ybxfyr6197nr0000gn/T/anb-brief-ui-l28s4qmf/verification.json`; brief SHA256 `e38416641e343458843b39704cc9d7842c5cc9ddbd644ca08264c97642369b13`. Isolated app/preferences cleaned up; production state was not replaced.

The editorial export checkbox is closed at its stated manual-handoff scope. Persistent follow-up/alert workflows, consumer-aware retention, broader recovery, complete accessibility/real-agent/provider acceptance and final distribution remain separate open work.


Final brief-batch follow-up: the ordinary Python editorial GET now also reads story and draft state in a single transaction. A controlled concurrent writer regression verifies that old story data cannot be paired with a newer approval; both that case and the export-snapshot case passed on Mac and native Linux. Native Swift reads already use the common transactional store. The universal macOS app also compiled successfully; its architecture/signature checks are separate from native Intel execution and notarization.


CI portability follow-up: the first brief commit's Mac tests passed, but the older hosted Swift compiler could not type-check the nested source-button expression. Extracted a small source view with a Boolean expansion state and explicit accessibility text, reducing the compiler workload. Final CI acceptance remains required for the follow-up commit.


## Consumer-safe archival history maintenance

- Added explicit stdlib-only `storage-status`, `retention-plan` and `prune-history` commands, packaged for Linux and the native Mac app. A private saved plan fixes a cutoff, batch size, acknowledged boundary and exact event hashes. Apply verifies an independent recovery archive, rechecks eligibility under the write transaction and compares exact archived payloads before atomic deletion/boundary/receipt commit. No production library was pruned.
- Protects all unacknowledged events, each story's latest two events, bookmarked history, revisions referenced by saved drafts/aggregate/platform approvals and group-repair events. No consumers means no eligible history. Current stories, memberships, editorial records, aliases and repair receipts are preserved. A lagging consumer can intentionally block cleanup; no automatic reset or cursor skip occurs.
- Existing consumers remain on their acknowledged positions. New or reset consumers below the highest archived sequence fail with 409; explicit `retained`/`now` choices acknowledge a reduced history scope. Date replay is conservatively rejected after pruning. API capability/status and the reference client document/enforce the boundary. Revision API/UI gap markers suppress misleading comparisons across missing revisions. Older apps must not open pruned journals; retain a pre-prune archive for downgrade/recovery.
- The optional retention table is accepted by recovery while old version-1 archives remain supported. Post-prune restore retains the delivery boundary. Receipts are bounded to the last 20 operations; same-plan retries return the retained receipt rather than deleting again. Read/write plans have event/payload/byte/time limits and a free-space reserve. The workflow makes pages reusable but does not compact files, cap total journal growth or remove archives automatically.
- Full local regression: **366 passed on Mac; 211 passed / 155 Mac-only skips on native Pop!_OS**. Two later reference-client cases reject malformed retention boundaries before inbox commit/ack; final focused Mac suite **34 passed**; native Linux **33 passed / 1 Mac-only skip**. Tests cover stalled/new/reset consumers, active leases, bookmarks, drafts/approvals/repair evidence, exact backups, altered plans, stale eligibility, post-delete rollback, low disk, invalid deadlines, linked sidecars, incomplete recovery, private standalone CLI, legacy archives, post-prune recovery and both runtimes' HTTP/timeline behavior.
- Native Apple Silicon build completed without warnings and passed strict ad-hoc signature verification. All three packaged helper hashes match source. The packaged standalone maintenance command read the independent load fixture without app dependencies. Native History gap text compiles and its data contract is tested; its final visual/VoiceOver acceptance remains part of the complete GUI pass.
- Bounded synthetic trial on each machine: 200 stories, 30 revisions each, 6,000 events / 30,920,704 database bytes. Six backup-gated batches removed exactly 5,310 eligible events, preserving 690 protected/latest events and every current story. 26,292,224 bytes became reusable. Another 1,000 new events reused pages without increasing main-database bytes. Mac ingestion/backup/prune: 4.77/0.06/0.35 seconds; Pop!_OS: 9.05/0.12/0.79 seconds. These are warm local synthetic observations, not months-long production, slow-disk or concurrency throughput claims. Reproducible script: `audits/2026-11-27/evidence/retention_load.py`; measurements: `retention-load-mac.json` and `retention-load-linux.json` alongside that evidence directory.

Broader storage/recovery, complete UI and real-agent/provider acceptance, final packaging/upload and final requirement audit remain open. This batch does not mark the overall release complete.

Native retention-display follow-up: expanded the retained revisions in the actual Apple Silicon app at 1100 x 752. Revision 7 showed the archive-gap warning; revision 8 showed the correct revision-7-to-8 summary comparison. Removed an empty Changed label for a gap and changed that section's heading to Recorded summary. Rebuilt, rechecked both expanded sections visually and through accessibility, and confirmed the authenticated history API agreed. Evidence: `/var/folders/4m/7t5q5j0x6fz8ybxfyr6197nr0000gn/T/anb-retention-ui-a4r5ab2b/verification.json`. Strict ad-hoc signature verification passed; the isolated app and preferences were stopped/removed. Full VoiceOver acceptance remains separate.


## Saved-search alerts and correction-review batch

- Both runtimes now record future matching revisions transactionally with story ingestion/group repairs. Up to 32 named subscriptions use ordinary Library filters without pagination; one subscription/story inbox row coalesces later matches. Pause/resume/filter changes are future-only. A persistent global subscription clock rejects stale editors, including delete/recreate races. Mark seen requires the exact matching sequence and never approves drafts or advances consumer cursors.
- Added authenticated GET/POST `/v1/library/alerts`, API capability/limits, Linux CLI, native toolbar unread count and refreshable inbox. Library Watch for changes enables current named filters; Review queue resets filters to active needs-review stories, including muted sources. Seen notifications remain independent of revision-bound editorial decisions.
- Research backup/restore validates subscription clocks, filters and references; old archives without alerts remain compatible. Unread matches protect their exact event from archival pruning. Inbox rows grow with matched stories, not repeated revisions; automatic deletion, OS/push/email notifications and total journal caps are not implied.
- Full local suites before the last two recovery validation cases: **397 passed on macOS; native Pop!_OS 232 passed / 165 Mac-only skips**. Final focused alerts/recovery/reference suites: **66 passed on Mac; native Pop!_OS 54 passed / 12 Mac-only skips**. Coverage includes HTTP authentication/validation/conflicts, CLI, transactional rollback, matching beyond Library pages, stale seen/version races, pause/resume, split/merge replay, pruning holds, recovery validation and old archives. Final full-commit CI is recorded separately below.
- Final Apple Silicon build has no compiler warnings and passes strict ad-hoc signature verification. Actual isolated native workflow created a watch, received corrections, paused through revision 5, resumed for revision 6, marked seen, displayed the seen item and opened current revision 6 with needs-review state intact. The UI and authenticated API agreed. Replaced flattening List rows with accessible ScrollView groups; Pause/Resume, Remove, Open current story and Mark seen appear as distinct accessibility controls. Inspected the 800 x 600 inbox and toolbar count. Full keyboard/VoiceOver acceptance remains separate. Fixture app stopped and isolated preferences removed; no production service or research changed. Evidence: `audits/2026-11-27/alerts-native-ui.json`.
- Bounded synthetic trial on Mac and native Pop!_OS: 32 subscriptions, 200 stories, five revisions each (1,000 events), exactly 6,400 coalesced inbox rows. Warm per-200-story revision batches took 0.22–0.46 s on Mac and 0.37–0.77 s on Linux; reading 100 rows took about 4 ms and 6 ms respectively. This is not sustained production/concurrent-load acceptance. Reproducible fixture and measurements: `audits/2026-11-27/evidence/alerts_load.py`, `alerts-load-mac.json` and `alerts-load-linux.json`.

Broader configuration/export recovery, operator storage controls, complete accessibility and real integrations, final packaging/publication and the final requirement audit remain open. This batch does not declare the overall release complete.


## Hosted-output documentation review

Reviewed upstream World Monitor licensing/EULA documentation on 2026-11-27 and added `DATA_RIGHTS.md`. It distinguishes independently implemented connector code, hosted-plan/output permissions and publisher rights. It explicitly records that durable current/history data, protected events, archives and exports do not implement hosted-output expiry. No hosted-account rights or production compliance are asserted. Updated the connector guide's obsolete pre-generation/no-leases example to point to the delivered consumer contract. This is a documentation closure, not an authenticated hosted-service acceptance.


## Application configuration and export recovery

- Added standalone/installed `backup-application`, `verify-application` and `restore-application` commands. Explicit stopped-writer acknowledgement, a selected Mac preferences/Linux config file and named export trees avoid silent capture assumptions. The archive includes the existing validated research snapshot, exact configuration bytes, nested exports and empty folders, with private permissions and a complete SHA-256/size inventory. Linux configuration can contain secrets; no archive is uploaded or treated as a release asset.
- Strict paths, linked/hard-linked/special-file rejection, inventory/metadata change checks, entry/depth/byte/deadline bounds and a free-space reserve prevent false success on the exercised failures. Restore validates the archive twice, checks copied hashes and restores research with the existing consumer/probe reconciliation rules. It only writes a new destination. Configuration is data, never executed or automatically applied. Failed finalization attempts to reinstate incomplete markers; any failed destination must not be activated, even if storage cannot accept a marker.
- Bridge credentials and Mac Keychain secrets are deliberately not replayed. Documentation provides platform-specific activation steps, old-path/configuration review, new-token reconnection and downstream inbox reconciliation. Custom OS/service installation and unselected external data remain machine-backup responsibilities. This closes the app configuration/selected-export recovery gap; it does not claim a bootable machine backup or exactly-once external publishing.
- Full suites before the two final directory-sync cases: **428 passed on Mac; 262 passed / 166 Mac-specific skips on native Pop!_OS**. Final focused application/research recovery checks: **53 Mac passed; 50 Linux passed / 3 Mac-only skips**. Cases cover exact content/empty-folder preservation, runtime Linux config loading, native macOS defaults export/archive/restore/import into isolated domains, secret-free command output, credentials excluded, partial failures, changing inputs/archives, malformed/traversal paths, links, deadlines, disk/size bounds, dependency-free CLI and old research recovery. Final full-commit CI is tracked in the PR.
- Rebuilt Apple Silicon app without compiler warnings; strict ad-hoc signature verification passed. Its packaged standalone helper verified the independent synthetic archive with site packages disabled, and all four helper hashes match source. `application-recovery-packaging.json` records hashes and scope. Actual production preferences, research, services and applications were not replaced.
- Reproducible warm synthetic trial on Mac and native Pop!_OS: 200 stories, 600 events, 128 exported files plus configuration, 10,494,011 combined payload bytes. All restored export/config bytes matched; the old bridge credential was absent and the empty directory remained. Mac backup/verify/restore: 0.035/0.013/0.053 seconds; Linux: 0.261/0.038/0.351 seconds. This is bounded local evidence, not production-machine disaster-recovery or slow-storage acceptance. See `audits/2026-11-27/evidence/application_recovery_load.py` and the corresponding Mac/Linux measurement JSON.
- Updated public source table, architecture, carry-over/count wording, API route summary, CLI capabilities and stale dependency/refresh documentation to match implemented behavior.

Overall storage controls/operator view, final broad UI/integration acceptance, packaging/publication and the requirement-by-requirement release audit remain open. No final release is claimed by this batch.


## Operator controls and ingestion budgets

- Native Operations view and authenticated `GET /v1/operations` combine storage, delivery lag/leases and source observations; failed sections stay visible. Linux adds `operations` and `storage-policy`; both runtimes expose version-checked GET/POST `/v1/library/storage`. Default 2 GiB ingestion ceiling/256 MiB free-space reserve, with explicit range limits and stale-edit conflicts. No automatic pruning, acknowledgements or resets. Policy persists in existing preferences and participates in validated recovery.
- Fetch preflight precedes provider calls; ingestion rechecks and applies a SQLite page ceiling. Oversized batches roll back story/event/alert changes together. Reads, editorial work, acknowledgements and explicit recovery remain available. `OPERATIONS.md` documents manual-write, WAL, concurrent-free-space and archive exclusions; no hard filesystem quota is claimed.
- Full local regressions: **439 Mac passed; native Pop!_OS 270 passed / 169 Mac-only skips**. New tests cover atomic exhaustion/retry, lowered limits, old research preservation, policy validation/conflicts, low disk before provider collection, partial operator-section failure, authenticated HTTP parity, CLI and valid/invalid backup policies.
- Native Apple Silicon build passed strict ad-hoc signature verification. Actual isolated UI verified over-budget pause, larger-budget recovery, unchanged consumer cursor, invalid input and unsaved-edit protection in a 620-pixel sheet. Fixed stale accessibility feedback and verified both validation and success labels. API-triggered blocked fetch reports failed and starts zero provider checks. Evidence: `audits/2026-11-27/operator-native-ui.json`. No production app/home or service was changed; fixture app stopped and isolated preferences removed. Full VoiceOver and final integration/release acceptance remain separate.


## Final native workflow acceptance

- Restored one current synthetic story, removed the expired snapshot item, retained AI/Science membership and showed the correction/disagreement warning. Expanded both revisions and direct/World Monitor observations; earlier and current headline/summary remain distinct. Tab traversed filter/list/sidebar and Down selected the other desk without losing the shared story.
- Actual native automatic refresh, enabled through Settings at a five-minute interval, completed at 18:25:23Z and 18:30:29Z on November 27. Each observed live run reported 15 successful source checks and one degraded/rate-limited check rather than complete coverage. Snapshot writes succeeded. These are point-in-time source checks, not an upstream availability guarantee.
- An intentionally malformed private fixture token remained unchanged; startup showed an actionable error and the bridge stayed offline while research remained readable. Repairing the fixture file and clicking Reload credential restored the listener and authenticated current-story lookup. Explicit unchanged credential Save displayed No credential changes. Concurrency, stale rotation, actual listener old/new token rejection and Keychain failure preservation remain covered by the automated native suite.
- Evidence: `audits/2026-11-27/release-native-ui.json`. Fixture app stopped; isolated preferences removed. The source build used for these checks is 75f03a7 plus no runtime changes (the later Settings edit only renames Coverage to Operations).
- Keyboard and accessibility-tree walkthroughs cover the exercised screens; VoiceOver launch timed out and did not produce a running reader in the available UI inventory even after its preference was temporarily enabled. Its original off state and System Settings pane were restored. Actual speech output/full screen-reader acceptance is not claimed. Optional live Hermes/OpenClaw handoff also remains unverified because the runtimes were absent on the checked hosts. These limitations must be in release notes, not silently treated as passing integration tests.
- The owner explicitly selected publication of a clearly labeled ad-hoc Mac build. Developer ID/notarization and clean-account Gatekeeper acceptance are not claimed; the download and installation guide identify the format. The downloadable app targets Apple Silicon; native Intel execution is not claimed.
