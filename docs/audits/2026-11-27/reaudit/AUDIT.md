# Combined app follow-up audit — four user perspectives

Observed 2026-11-27. Scope: the current Agent News Bot + World Monitor digest connector, its native macOS app, Python service, research/editorial library and agent bridge. Base commit `77e237389c0247b838384fc5a531cd47c627b7e6`, plus the pending Python dependency lock/CI and World Monitor worker changes. The original audit in the parent directory predates nine repair commits; its original defects are not presented here as still unfixed.

**Verdict: useful development build, not ready for the final public release.** The strongest capability is a local research service with durable, source-preserving delivery. New adversarial fixtures found important correctness and integration failures despite the existing suite passing. This is a broad audit with explicit limits, not security certification or complete GUI/accessibility acceptance.

## What was actually verified

- Current macOS regression suite: **127 passed**, 48.83 seconds.
- Same source/tests copied to the isolated Pop!_OS checkout: **80 passed, 47 macOS-only skips**, 16.34 seconds. An earlier run used an older copied test set; that result is superseded.
- Native Apple Silicon app rebuilt successfully. Strict signature verification passed; the signature is **ad hoc**, with no TeamIdentifier. This is not notarization or distribution acceptance.
- Existing runtime audit result checked: eight locked Python runtime packages had no known advisory findings. Existing npm audit result likewise reported zero findings. This does not audit the OS, all build tooling, or prove vendored/native code vulnerability-free.
- New bounded fixtures exercised both runtimes, real loopback HTTP, malformed headers, eight idle connections, a 6 MiB response, non-web RSS links, snapshot caps, story grouping, agent readiness and native token-write failure. They used temporary state and fake credentials. No unsafe URL was opened and no external agent received a prompt.
- Live read-only source sweep: **54 distinct RSS URLs**, **48 HTTP-200 feeds with parseable entries**, five request/HTTP failures and one valid empty feed. Results are timestamped in `feed-health.json`.
- GitHub's [current default-branch README](https://github.com/ShadowfetchStudio/agent-news-bot/blob/main/README.md) and [upstream digest protobuf](https://github.com/koala73/worldmonitor/blob/main/proto/worldmonitor/news/v1/list_feed_digest.proto) were read through the connector. Public documentation still describes the older app; the current upstream digest contract remains consistent with the connector's categories, timestamps and coverage fields. Observed file blob SHAs: README `76369b961a203e43dd86361ac3ec252481676412`; protobuf `15e1b1ce5aa1bce2837feb226e5cf6324ddb6016`.

Reproduce the new observations from the repository root with a Python environment containing the runtime dependencies and pytest:

```sh
python docs/audits/2026-11-27/reaudit/probe.py
```

On macOS this compiles the checked-in native integration harness. `observations.json` records actual outcomes, not assertions that the observed defects are acceptable. `manifest.json` fingerprints the audited source files. Production code was not changed by this follow-up audit.

## 1. First-time user: “Can I install it and know whether it is working?”

### What works

Default news sources do not require provider keys. World Monitor is optional and disabled by default. macOS has explicit credential saves with visible Keychain errors; Linux has a private, non-executable configuration file and staged installation/rollback. Existing customized agent skills are preserved.

### R01 — P1 — Native bridge can advertise a credential it never saved

**Reproduced using the exact helper body, isolated from the GUI.** With a directory occupying `bridge.token`, two calls returned different 32-character tokens while persistence failed silently. `bridgeToken` is computed, so the token used to start the server and the value later displayed in Settings can differ. A short invalid token is also replaced without the explicit preservation policy now used on Linux.

Evidence: `Sources/Engine.swift:89` and `:350-358`; `observations.json.native_token_write_failure`. This did not alter a real token or Keychain entry.

Fix: load/create once through a throwing, private atomic helper; validate the existing regular file; preserve invalid existing bytes; surface a repair action and refuse bridge startup on failure. Add explicit rotation that switches the live listener and persistent credential together. Acceptance: unwritable paths, invalid tokens, symlinks and concurrent creators cannot produce an advertised-but-unusable credential.

### R02 — P2 — Agent “ready” and successful response labels are too optimistic

**Reproduced in the native harness.** An unrelated localhost service returning HTTP 200 and `{}` was labeled Hermes-ready. The same empty JSON as a chat completion was returned as successful output (`"{}"`). The probe accepts any successful `/health`; the send path falls back to raw JSON when expected completion fields are missing. CLI readiness additionally establishes executable presence, not model/authentication readiness.

Evidence: `Sources/HermesClient.swift:5-42,119-122`; `unrelated_service_hermes_probe` and `invalid_completion_hermes_send`.

Fix: separate detected, reachable, authenticated and tested states; validate a compatible service response and nonempty assistant completion; offer a harmless explicit test. Show actionable authentication/model/configuration errors without exposing credentials. Acceptance: unrelated health endpoints and empty/error-shaped HTTP-200 bodies cannot be called ready/successful.

### Setup opportunities

- First-run choices: direct sources only, existing self-hosted World Monitor, or an authorized hosted API. Explain that the app does not deploy World Monitor for the user.
- A connection test with distinct URL/TLS/401/403/429/schema messages, last successful retrieval and retry guidance.
- A visible setup checklist for bridge credentials, enabled desks, agent configuration and first successful fetch.
- Verify small-window layout, full keyboard navigation, VoiceOver labels and ordinary installer launch on a clean account. Those were not fully exercised in this pass.

## 2. Daily researcher / video creator: “Can I trust the story and turn it into useful work?”

### What works

The persistent library supports search, saved filters, bookmarks, source mutes, multiple desk membership, source observations and revision history. Draft generation creates five editable fields for YouTube, X, TikTok and narration. Optimistic concurrency protects edits, approval is tied to a source revision, and later changes invalidate approval. Research packages preserve source links and editorial state in separate folders.

### R03 — P1 — Similar wording can merge different real-world events

**Reproduced on Python and Swift.** These two distinct hypothetical reports became one story with two source links:

1. “NASA launches new lunar orbiter to map south pole craters”
2. “China launches new lunar orbiter to map south pole craters”

Python also reported `disagreement: false`. The matcher uses token overlap without checking the actor/event identity. The current contradiction heuristic does not flag this case. Source-breadth ranking then gives the combined story extra weight, and drafts can describe one actor while including the other event as supporting material.

Evidence: `linux/agent_newsbot/stories.py:121-127`; `Sources/StoryStore.swift:140-146`; `different_actor_group_*`.

Fix: use conservative grouping with entity/number/date disagreement checks, and keep uncertain matches separate or explicitly suggested. Add manual split/merge with provenance and audit history. Acceptance: different actors, places, dates and materially different numbers do not silently merge; exact-URL corrections still work and existing identities/events remain recoverable.

### R04 — P1 — RSS can introduce non-web URLs into the desktop action

**RSS ingestion reproduced on both runtimes; desktop dispatch confirmed in code, not executed.** A `file:///tmp/audit-unused.txt` item was accepted as an article without source errors. The Mac's `openStory` passes any Foundation URL to `NSWorkspace.open`. Some library links already restrict HTTP(S), but the primary action does not. Untrusted feed data can therefore select local files or registered URL handlers instead of opening a news webpage.

Evidence: `Sources/RSSClient.swift:39-42`; `linux/agent_newsbot/engine.py:107-110`; `Sources/Engine.swift:273-276`; `unsafe_rss_*`.

Fix: share an HTTP(S)-only URL validator at ingestion, stored-story migration/read boundaries, exports and every open action; reject embedded credentials and missing hosts. Acceptance: `file:`, custom schemes, malformed hosts and encoded bypass cases never become actionable news links. Test without opening any target.

### R05 — P2 — “Max stories per desk” is not enforced after carry-over

**Reproduced on both runtimes.** With a maximum of 20 and two enabled desks, 20 new science articles plus 20 retained science articles produced **40 science articles**. The implementation caps the overall batch at `limit × number of desks`; it does not allocate each desk's allowance. Busy desks can also consume capacity expected by quieter desks.

Evidence: `Sources/SnapshotPolicy.swift:30-31`; `linux/agent_newsbot/server.py:_keep_live_desks`; `per_desk_cap_*`.

Fix: define and enforce deterministic per-desk selection after merging fresh/retained stories, respecting multi-desk membership without inflating change counts. Keep omitted stories in the durable library. Acceptance: repeated refreshes and restoration respect each desk's cap and do not starve others.

### Research opportunities

- Explain **why stories were grouped**, and separate “two links” from two independent reports. Group repair is higher value than adding more feeds now.
- Provide an editorial brief: what happened, what changed, evidence, disputed claims, unknowns, and suggested follow-up. Current templates mostly rearrange retained headlines/snippets.
- Add saved-search alerts and a review queue for changed approved drafts. A saved search currently stores filters, not a notification subscription.
- Allow approving an X-only draft or a YouTube-only package. Current approval requires all five fields even when the creator needs one platform.
- Make source rights/attribution and production readiness explicit. Current VideoBot support is a **manual Inspect URLs handoff**, not video generation, a native project importer or automatic queueing.

## 3. Automation author: “Will my agents behave predictably?”

### What works

Both runtimes expose authenticated loopback APIs and a durable event journal. Consumers have independent cursors, explicit acknowledgements, replay/start positions, generation fencing and optional leases. The reference consumer commits to a private idempotent inbox before acknowledging. Fetch requests are asynchronous and return a job ID; they no longer imply immediate completion.

### R06 — P2 — Linux retains idle header-reader threads without a server-level bound

**Bounded reproduction, not an exhaustion test.** Eight loopback clients sent incomplete request headers and waited six seconds. Eight additional handler threads remained alive. `serve()` uses the standard `ThreadingHTTPServer`; the five-second timeout is set only later for selected JSON bodies, after headers and authentication. There is no connection semaphore or request-header deadline. The issue can be triggered before authentication, but the listener is local-only.

Evidence: `linux/agent_newsbot/server.py:246-251,416-421`; `slow_headers_python`.

Fix: bound concurrent handlers, header/body wall time and response writes; close incomplete requests and release capacity. Acceptance: slow headers, trickled bodies, stalled readers and disconnects leave the service responsive with bounded threads/descriptors. Do not claim that this eight-client sample demonstrated a full-service outage.

### R07 — P2 — A malformed authentication header closes the Python request without a JSON error

**Reproduced.** A non-ASCII bearer value causes Python's string `hmac.compare_digest` to raise `TypeError`; the client receives no status line. The native bridge returned 401 for the equivalent case. This is a handler/request failure, not a process crash or authentication bypass.

Evidence: `linux/agent_newsbot/server.py:231-235`; `non_ascii_auth_*`.

Fix: validate token/header encoding or compare normalized bytes, reject malformed credentials with a bounded 401 response and retain constant-time comparison for validly encoded credentials. Acceptance: arbitrary header bytes do not raise outside request error handling.

### Automation opportunities

- Add version/capability discovery and stable machine-readable error codes. `/v1` alone does not tell a consumer which optional administration/editorial features exist.
- Add recorded contract fixtures for current upstream World Monitor coverage fields and explicit incompatibility diagnostics. Current digest fields were checked against the live upstream protobuf; a licensed authenticated production provider was not exercised.
- Add an optional outbox example for downstream side effects. Inbox idempotency and acknowledgements cannot guarantee exactly-once social publication.
- Offer a prepared SDK/client for the sequence: fetch → inspect coverage → read events → save research → approve → acknowledge. Avoid retrying publication blindly.

## 4. Owner / maintainer: “Will it keep working for months?”

### What works

Linux upgrades now use separate staged environments, preflight, atomic activation, PID-matched health checks, preserved configuration and rollback. The current lock file pins runtime packages with artifact hashes. Core database/configuration privacy has improved. The new World Monitor subprocess deadline returned after **1.00 second** in a deliberately slow-response test with a one-second test deadline; its production deadline is 20 seconds.

### R08 — P2 — Direct feed/API transfers still buffer before their limit and lack a short total deadline

**Oversized transfer reproduced; prolonged streaming risk confirmed in code.** The Python direct HTTP helper downloaded all **6,291,456 bytes** of a fixture even though RSS later enforces 4,194,304 bytes. Swift's fixture likewise sent the full 6 MiB before RSS rejected it. Python uses non-streaming `requests.get`; native general networking uses `URLSession.shared.data`. The byte check protects parsing/cache persistence, not receipt or peak memory. Python's request timeout is an inactivity timeout. The new isolated World Monitor worker does not cover ordinary RSS, weather, markets or HN requests.

Evidence: `linux/agent_newsbot/engine.py:53-62,94-100`; `Sources/Models.swift:183-209`; `Sources/RSSClient.swift:33`; `direct_http_*`.

Fix: use common streaming bounds and total deadlines for every provider path, including decompressed bytes and redirects. Apply per-source circuit breaking and Retry-After handling. Acceptance: large, compressed and indefinitely trickling fixtures terminate within the declared byte/time budgets while other desks still complete. No infinite-stream or out-of-memory test was run against the user's machines.

### R09 — P2 — Native snapshot/export write errors are silently discarded

**Code-confirmed; no disk-fill experiment performed.** `exportJSON` uses `try?` for serialization and both writes and returns no failure to the refresh path. A full/unwritable export destination can leave `latest.json` stale while the app presents a completed refresh. File consumers and the next launch can then see an older snapshot than the visible app. The durable journal is separate, so this is not a claim that all research is lost.

Evidence: `Sources/Engine.swift:312-332` and its refresh call site.

Fix: return an explicit persistence result, display snapshot/export failure separately from retrieval success, keep the last complete snapshot, and offer retry. Acceptance: simulated write/rename failures preserve the old snapshot and cannot produce a falsely successful export indication.

### Live source findings and remaining operational work

| Source | Observation in this sweep | Interpretation |
|---|---|---|
| AP / AP World | ConnectionError | Unavailable from this test environment; not proof of permanent retirement |
| Yahoo Finance RSS | HTTP 404 | Review/replace configured endpoint |
| Medical News Today | HTTP 404 | Review/replace configured endpoint |
| VentureBeat AI | HTTP 429 | Rate-limited at observation time; honor backoff before deciding replacement |
| arXiv cs.LG | HTTP 200, valid RSS, zero entries | Empty is not a transport/parser failure |

Persist last-success, failure streak and next-retry times across restarts; current source checks mainly describe the latest attempt. Add one operator view covering source health, consumer lag and storage usage.

Provide a supported full backup/restore workflow and consumer-safe retention. Installer journal backups are useful, but do not constitute full application disaster recovery. Story/event storage currently grows without an operator retention tool; this audit did not run a production-sized months-long benchmark. Retention must not delete unacknowledged events or silently invalidate consumer cursors.

Finish release documentation and distribution validation. Public GitHub README still describes the older unseen-only/ready-to-post behavior. The local README still has obsolete Linux token-size/permission details and understates cross-platform carry-over/count parity. Refresh architecture/API/platform claims, then validate the final packages and downloads. An ad-hoc build is not a signed/notarized consumer release.

## Prioritized work and acceptance boundaries

1. **Protect users and evidence:** R04 non-web links, R03 unrelated-event merging, R01 native credential persistence.
2. **Bound operation and report failures honestly:** R06/R07 HTTP handling, R08 all-provider transfers, R09 export failures, R02 agent readiness, R05 per-desk caps.
3. **Close existing usability/operations gaps:** setup/connection tests, persistent source health, review queues, backup/restore/retention, capability discovery and release documentation.
4. **Then add editorial value:** explained grouping, ranked researched briefs, source-quality context, platform-specific approval and deeper explicit VideoBot handoff.

The adapter integrates the **news digest**, not World Monitor's full map/dashboard and every upstream data source. Expanding that scope before these fixes would increase the number of failure paths.

Not verified in this pass: full keyboard/VoiceOver walkthrough, clean-account installer/Gatekeeper acceptance, actual authenticated hosted/self-hosted World Monitor, end-to-end installed-agent work, native Intel Mac execution, root-system-service installation, production-scale retention, or a published final GitHub artifact. These are open release gates, not evidence of failures. No GitHub push, package publication, social posting or production service replacement was performed by this audit.
