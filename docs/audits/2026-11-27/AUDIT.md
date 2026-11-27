# Agent News Bot + World Monitor: four-perspective audit

> This is the historical audit of `165eff6`. Repairs are tracked in [the release checklist](../../RELEASE_CHECKLIST.md); findings below describe the pre-fix behavior.

**Audit date:** November 27, 2026, America/New_York.  
**Audited code:** `165eff6a364457425d751c4ed6422f0a9917a37e`, local branch `feature/worldmonitor-integration`.  
**Verdict:** Useful development prototype, but do not release it as a dependable unattended news service yet. This audit found **14 defects: 8 P1, 5 P2 and 1 P3**, plus separate operational risks and feature opportunities. P1 means fix before relying on this workflow in normal use; none is labeled an internet-exploitable critical vulnerability.

The existing suite still passes: **41/41 on macOS; 36 passed and 5 macOS-specific skips on native Pop!_OS**. Those tests cover useful contracts, but did not cover the adverse user journeys below. Passing them does not establish release readiness. Some findings are legacy defects and some were introduced by the new integration. No production code was changed during this audit.

**Pre-repair follow-up verification:** HEAD remains `165eff6`; no tracked implementation changes are present. The macOS suite passed again (41 tests, 8.39 seconds). Re-running the isolated story/cache/install scenarios reproduced the missing delivered enrichment, suppressed correction, contradictory grouping, canonical-URL disagreement, undated carry-over, dropped RSS item, CLI module-lookup failure, incorrect market checkpoint and inconsistent counts. The repeat bridge restart test failed 13 of 30 times, compared with 15 of 30 in the original run; this confirms a scheduling-dependent race rather than a fixed failure percentage. The network rerun also reproduced SIGPIPE termination after two disconnect attempts and the false HTTP 200 at 60 seconds while the 65-second fetch was still running. The isolated native Linux suite passed again (36 passed, 5 platform-specific skips, 5.65 seconds). Follow-up observations are retained separately under `evidence/recheck-*.json`.

**Scope and evidence**

The combined implementation is Agent News Bot with an optional World Monitor digest connector; it does not embed the complete World Monitor map/dashboard application. Reviewed the Swift desktop, Settings/Keychain handling, Python daemon/CLI, source collectors, source normalization, World Monitor adapter, SQLite journal, both HTTP bridges, exports, agent handoff, installer, CI and documentation. Ran isolated native Swift bridge tests, Python scenarios, native Linux tests, HTTP disconnection/timeout tests and a bounded public RSS health sweep. All experiments used temporary research databases and synthetic stories; no live user database or service configuration was modified.

The native bridge crash occurred in an isolated test process using the app's actual bridge code, not by intentionally crashing the installed GUI app. A manual visual/accessibility walkthrough, signed/notarized release installation, authenticated hosted World Monitor session, real agent-runtime handoff, full dependency vulnerability scan, and long-duration load test were **not** performed. Source health observations are point-in-time results from this machine/network, not proof that an upstream service is permanently unavailable.

## 1. First-time user: “Can I install it and know it is connected?”

**B02 — P1 — Closing Settings can take the Mac API offline. Runtime reproduction plus UI wiring inspection.**

`AgentBridge.start()` calls `stop()`, which cancels a dispatch source whose file descriptor closes asynchronously, then immediately binds the same address. The old listener may still own the port. Repeated same-port restarts failed in **15 of 30 attempts** in the initial recorded run; the exact count is scheduling-dependent. Settings restarts the bridge on every close, including when the port did not change, and ignores the returned success flag. The earlier green `bridgeReady` indicator can therefore remain green while the listener is gone.

Evidence: `Sources/AgentBridge.swift:25-64`, `Sources/SettingsView.swift:83-88`; `evidence/findings.json`, key `bridge_restart` (reruns can differ). Fix: make listener teardown/rebind ordered and transactional, avoid restarting for unrelated settings, preserve the old listener if a new port cannot bind, and update visible readiness from the actual result. Acceptance: hundreds of same-port restarts and a deliberately occupied replacement port retain correct availability and UI status.

**B08 — P1 — The installer-generated Linux CLI cannot locate the installed module from an ordinary directory. Runtime reproduction on macOS and native Linux.**

The installer copies the package to its prefix and installs only third-party requirements into the venv. Its generated shell wrapper sets `AGENT_NEWSBOT_PREFIX`, but neither installs the package into that venv nor adds the prefix to Python's import path. `python -m agent_newsbot status` from `/tmp`, with the prefix set and no inherited PYTHONPATH, fails with `No module named agent_newsbot`. The shipped Python wrapper does add sys.path, but the installer replaces it with a different shell wrapper. systemd works differently because its unit explicitly sets PYTHONPATH.

Evidence: `linux/install.sh:24-41`, `linux/agent-newsbot:12-14`, `linux/agent-news-bot.service.example:12-14`; `evidence/linux-audit.txt`. Fix: install the package into the venv or use the shipped wrapper consistently. Also explicitly align CLI state/token discovery with the service state directory. Acceptance: install into a disposable prefix and run the CLI from an unrelated directory under the intended operator account.

**Setup risks and opportunities, separate from confirmed defects:**

- Add a first-run choice between independent direct feeds, a self-hosted World Monitor endpoint and licensed hosted access. Show whether a server is configured, reachable, authenticated and actually returning content.
- Add **Test connection**, last successful fetch, credential status and actionable 401/403/429 explanations. The present defaults do not provision a World Monitor server.
- The Linux guide mentions a private environment file, but the supplied unit has no `EnvironmentFile=` and the guide omits the systemd drop-in wiring. Supply a supported config file and exact enable/restart steps.
- Keychain calls ignore OSStatus. Reads returning nil are shown as an empty field, and closing Settings deletes/re-adds entries. This is an untested credential-loss risk if access or saving fails; preserve existing values on errors and show explicit save failures.
- Automatic refresh and restoring the visible last batch are missing on macOS, although Linux has periodic refresh and snapshot loading. Make those product differences explicit or close them.

## 2. Daily news reader / creator: “Is it fresh, complete and usable?”

**B05 — P1 — Increasing the story limit cannot recover articles discarded behind an RSS cache marker. Runtime reproduction; affects both implementations.**

The collector records an ETag for the whole feed but returns only the first `limit` articles. It does not retain the feed body or unselected items. With a two-story feed, a first fetch limited to one story commits the ETag. Raising the limit to two and refreshing receives HTTP 304 and returns nothing; the other story cannot be recovered until upstream content changes or the cache is invalidated. Widening the lookback window has the same class of problem. A presentation limit should not silently checkpoint unprocessed source items.

Evidence: `linux/agent_newsbot/engine.py:73-131`, equivalent logic in `Sources/RSSClient.swift`; `evidence/findings.json`, key `rss_cap_checkpoint`. Fix: cache the full parsed feed, persist all eligible candidates before applying a display cap, or invalidate caches when relevant filters change. Acceptance: changing a cap/lookback against an unchanged feed exposes the newly eligible stories.

**B06 — P1 — Market checkpoints can belong to the wrong selected symbols. Runtime reproduction in Python; same ordering defect in Swift.**

Articles are sorted by move size, but quote checkpoint tuples remain in original symbol order. Both lists are then independently truncated. With symbols AAA (+1%) and BBB (+50%), and a one-item limit, the collector returns **BBB but checkpoints AAA**. This can suppress an unserved quote and repeatedly reconsider another. This defect was introduced when quote checkpoints were deferred until after journal persistence.

Evidence: `linux/agent_newsbot/engine.py:293-322`, `Sources/MarketsClient.swift:42-75`; `evidence/extra-findings.json`, key `market_checkpoint_mismatch`. Fix: derive checkpoints from exactly the selected articles, keyed by symbol, or persist every quote observation before applying presentation limits. Acceptance: reorder input symbols, vary the limit and prove each checkpoint corresponds to a committed observation.

**B10 — P2 — Complete source failure can be described as “Desks are current.” Code-confirmed.**

The Mac engine counts errors, but when the merged batch is empty it always chooses the “No fresh stories … Desks are current” message. It does so even when every feed failed. An empty successful refresh, stale cached data, partial coverage and total failure need distinct states.

Evidence: `Sources/Engine.swift:184-187`. Fix: make health/coverage a structured result and render it independently of the number of new articles. Acceptance: an all-failed fixture visibly reports failure, never current coverage.

**B11 — P2 — Undated articles can remain “fresh” indefinitely on Linux. Runtime reproduction.**

The carry-over path assigns `now` whenever `published` is missing/unparseable. Each refresh renews that test. An item whose `first_seen` was set to the year 2000 still survived the freshness check. Snapshot loading also does not prune age immediately, so a restart can initially serve an expired batch.

Evidence: `linux/agent_newsbot/server.py:42-47,82-102`; `evidence/findings.json`, key `undated_carryover`. Fix: use immutable first-observed time as the fallback, preserve unknown publication status and prune restored snapshots. Acceptance: undated and restored items expire predictably under the configured lookback.

**B12 — P2 — The X character counter accepts posts that exceed the standard limit. Runtime reproduction and official platform documentation.**

Both composers use ordinary string length plus a fixed 23-character URL cost. X uses weighted Unicode. A synthetic title of 150 CJK characters plus its link is reported as **174**, but its weighted length is **324**, above 280. Swift grapheme counting also differs from X for emoji. [X's official character-counting rules](https://docs.x.com/fundamentals/counting-characters) document the weighting and normalization requirements.

Evidence: `Sources/Hashtagger.swift:45-60`, `linux/agent_newsbot/hashtags.py` function `tweet`; `evidence/x-character-count.json`. Fix: use the official twitter-text rules and parity fixtures for CJK, emoji sequences, combining characters and multiple URLs. This audit did not publish a test post.

**B14 — P3 — Desk counts contradict the Linux snapshot after carry-over. Runtime reproduction.**

When a desk has no newly changed articles, `_keep_live_desks` retains its previous stories but leaves `desks[].fresh` at zero. The reproduced snapshot has one article and a corresponding desk count of zero. The field currently mixes “new this cycle” and “displayed in the batch.”

Evidence: `linux/agent_newsbot/server.py:82-110`; `evidence/extra-findings.json`, key `desk_count_parity`. Fix: expose separate new/updated/carried/displayed counts and use them consistently across runtimes.

**Editorial opportunities:**

- Opposing headlines were grouped together: “will proceed” and “will not proceed.” Keeping both reports together can be useful, and the UI already warns that grouping is heuristic. The missing capability is a disagreement/correction flag and a timeline so the first headline is not mistaken for a settled conclusion. This is a capability gap, not a claim that every contradictory pair should be split into different events.
- Allow a story to belong to multiple desks. The current single `desk` field and representative-selection rule can move a shared AI/tech story out of one user's chosen desk.
- Add saved searches/watchlists, entity filters, bookmarks, source muting, source-quality explanations, and a permanent story-detail/history view.
- Add YouTube/X/TikTok draft templates and editorial approval state. Drafting remains separate from posting; do not infer verification or media reuse rights from a source score.

## 3. Agent / automation user: “Can I trust the API and avoid losing work?”

**B01 — P1 — A disconnecting client can terminate the native bridge process. Runtime-confirmed.**

The native server writes directly to the socket without suppressing SIGPIPE. A consumer requested an event response, then disconnected before reading it. The isolated process running the app's real bridge code exited with **signal 13 / SIGPIPE** (`returncode=-13`). Only two bounded attempts were needed in the recorded run. The event used a synthetic large payload; RSS items currently have no comparable body-size cap to prevent large event responses. This is a local-client reliability issue, not a demonstrated unauthenticated remote exploit.

Evidence: `Sources/AgentBridge.swift:240-265`; `evidence/network-findings.json`, key `disconnect`. Fix: handle broken pipes explicitly (e.g. socket-level suppression plus checked writes), bound response sizes and test client cancellation. Acceptance: disconnects during small/large responses never terminate the host process.

**B03 — P1 — World Monitor context is stored but can be absent from everything delivered to agents. Runtime-confirmed in Swift and Python.**

When direct RSS and World Monitor supply the same URL, the first direct observation emits an event. World Monitor later adds location/importance/provenance, but the content comparison considers only URL, title and summary. No enriched event is emitted, and the earlier fresh result is not rebuilt from the final stored record. The database contained `locationName: Florida` and importance 80 while the fresh snapshot and event both contained empty metadata. There is no separate current-story lookup backed by that persistent database.

Evidence: `linux/agent_newsbot/stories.py:20-22,76-108`, `Sources/StoryStore.swift:95-133`; `evidence/findings.json` and `evidence/extra-findings.json`. Fix: finalize each grouped story after all provider observations, expose current state, and distinguish enrichment events from editorial updates so context is delivered without score-churn spam. Acceptance: an agent receives added provenance/location for an existing URL without needing an unrelated headline change.

**B04 — P1 — A newer World Monitor correction can be suppressed after any direct observation. Runtime-confirmed in both runtimes.**

Once `providers` contains `direct`, the merge rule always replaces the World Monitor title and summary with the stored direct values. It does this even in a later WM-only cycle when the direct feed is unavailable. Changing a fixture from “launches” to “cancels … after engine failure” emitted **zero updates** and left the old headline as the latest deliverable. Avoiding a shortened snippet overwriting a full summary is reasonable; permanently treating every direct headline as newer is not.

Evidence: `linux/agent_newsbot/stories.py:84-97`, `Sources/StoryStore.swift:106-120`; same evidence files, correction keys. Fix: retain versioned provider observations with their dates, compare titles independently of snippet length, and emit a correction/disagreement when warranted. Acceptance: a newer substantive change is not silently suppressed just because a direct provider was seen earlier.

**B07 — P1 — A slow Mac fetch returns false success. Runtime-confirmed.**

A fetch handler deliberately took 65 seconds. At **60.0 seconds**, the bridge returned HTTP 200 with `{"ok":true,"total_articles":0}` because it ignores the semaphore timeout. Work was still running. Agents can mistake this for a completed empty refresh and advance a workflow incorrectly.

Evidence: `Sources/AgentBridge.swift:199-210`; `evidence/network-findings.json`, key `fetch_timeout`. Fix: use a 202/job-status contract, or a real timeout/error response with an explicit running state; remove the shared mutable completion variable. Acceptance: timeout and already-in-progress cases cannot be mistaken for completed success.

**B09 — P2 — URL identity differs between Mac and Linux and Linux drops significant ports. Runtime-confirmed.**

Python canonicalizes both `https://example.com:8443/story` and `:9443/story` to `https://example.com/story`; Swift retains each port. For a path with a trailing slash before a query, Python strips the slash while Swift keeps it. Distinct endpoints can merge on Linux, and the same source can receive different IDs across the two runtimes.

Evidence: `linux/agent_newsbot/store.py:16-29`, `Sources/Models.swift:235-254`; `evidence/findings.json`, key `canonical_parity`. Fix: define one URL identity specification and shared fixture suite, preserving non-default ports and query meaning. Plan migration/aliases for existing IDs rather than silently reassigning all stored identities.

**Automation opportunities:**

- Add `GET /v1/stories/<id>` for current durable state, revision diffs, source/provider filters and a consumer-status endpoint with lag/last acknowledgement.
- Provide a small reference consumer with idempotent processing, retry handling and explicit acknowledgement. Current at-least-once semantics are sensible; they are not exactly-once publication.
- Add consumer onboarding choices (full history, from a date, from now), replay/reset controls and optional worker leases. Do not introduce filtering that lets acknowledgements skip unseen events.
- Add a schema version/capability endpoint, shared fixtures for edge cases, and consistent failure responses. Ordinary contract fixtures alone missed the failures in this audit.

## 4. Owner / maintainer: “Can I operate and upgrade this without surprises?”

**B13 — P2 — Starting the Mac app overwrites existing agent skill files. Code-confirmed; not executed against the user's files.**

`start()` calls `installHermesSkill()`, which writes generated SKILL.md content to both agent directories on every start with no existence/version/ownership check or backup. A user-customized file at either path is replaced. The generated descriptor also remains version 1.1.0 while the app/package is 1.2.0.

Evidence: `Sources/Engine.swift:104,284-319`. Fix: make installation explicit or update only app-owned unmodified files; preserve custom files and previous versions. Acceptance: a fixture custom skill survives application startup and a managed upgrade.

**Current source health:** the bounded sweep checked **54 unique configured RSS URLs**. **48** returned HTTP 200 with parseable entries. Five failed: AP and AP World connection errors, Yahoo Finance and Medical News Today HTTP 404, VentureBeat AI HTTP 429. One arXiv cs.LG feed returned HTTP 200 with zero entries. Empty output can be legitimate; 429 can be transient. Results are in `evidence/feed-health.json`. This is not a check of every non-RSS API or an authenticated World Monitor feed.

**Operational risks / backlog, not additional confirmed defects:**

- Implement last-success/coverage monitoring, per-feed backoff and replacement-source review. Zero stories must not be used as the sole health signal.
- `stories.sqlite` and all event revisions have no retention policy. This is documented, but storage, backup/restore and consumer-safe pruning need an operator interface before long-running use. No production-size benchmark was run.
- Installer upgrades delete/copy package files and rewrite the unit in place; `enable --now` is not a verified upgrade/restart workflow for an already active service. Add staged releases, config preservation, health validation and rollback. A real system upgrade was not performed in this audit.
- Direct Python runs inherit the caller's umask; the test journal was mode 0644. The supplied systemd unit has a tighter umask and service directories, so this does not prove all deployed data is exposed. Define private file permissions for manual runs as well as service installs.
- Requirements are lower bounds, despite an installer comment describing pinned dependencies. Add a reproducible lock/constraints process and dependency scanning. No CVE finding is asserted here.
- Python's World Monitor HTTP read timeout is an inactivity timeout, not a total response deadline. A trickling response can occupy a refresh far longer than 15 seconds. Add an overall deadline and provider circuit breaker; this was code-reviewed, not stress-tested with an indefinitely streaming endpoint.
- Add credential rotation and observable Keychain failures, and document hosted-output/upstream permissions separately from code licensing. This audit does not clear data for redistribution.
- Align public claims with verified behavior before release: README currently describes tweets as ready to post and the two runtimes as sharing one freshness model/API. B09, B11, B12 and B14 demonstrate important exceptions. The architecture diagram also omits the new World Monitor adapter and durable story/event journal. Update the diagram, API completion semantics, actual platform differences and release validation evidence together with the fixes.

## Recommended order

1. **Stabilize operation:** B01/B02/B07/B08 — disconnect safety, deterministic binding, truthful fetch completion and the Linux CLI.
2. **Protect research correctness:** B03/B04/B05/B06/B09 — delivered enrichment, correction precedence, feed/quote checkpoints and cross-runtime identity.
3. **Make status trustworthy:** B10/B11/B14, source health, current-state lookup and stale-data labels.
4. **Finish user-facing reliability:** B12/B13, setup wizard, credential save feedback, macOS refresh/restoration and a supported Linux configuration path.
5. **Then expand capability:** watchlists, revision/conflict views, editorial ranking/briefs, platform-specific drafts and VideoBot export.

The highest-value product remains a dependable local research service for agents. A larger dashboard or more automatic posting would amplify the current reliability problems; those expansions should follow the repair batch.

## Evidence and reproduction notes

`evidence/` retains JSON observations, the baseline/native-Linux logs, the diagnostic Swift harness and Python scripts. Diagnostics were run against this exact local checkout. They use `/tmp/anb-audit-20261127` and the existing temporary integration venv; they are audit artifacts, not portable installed commands. The diagnostic code was not added to the default passing test suite. Convert each confirmed defect into a regression test as its fix is implemented.

The network harness intentionally demonstrates a timeout and a crash in a child process. Do not point it at a production app. No source-code fixes, installation, GitHub push or public posting occurred during this audit.
