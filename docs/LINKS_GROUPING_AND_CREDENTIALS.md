# Article links, grouping and bridge credentials

## Actionable article links

Both runtimes require HTTP or HTTPS article URLs with a host, a valid port and no embedded username/password. Control characters, backslashes (including percent-encoded forms), malformed escaping, non-web schemes and invalid host labels are rejected. Identity canonicalization is deliberately separate: normalizing an old URL is not permission to open it.

RSS and Hacker News skip invalid links and report degraded coverage. World Monitor validates the original link before normalization can remove user information. Journal ingestion rejects an invalid input batch rather than retaining partially accepted rows. Native story opening, source links and agent handoffs apply the same policy. Draft generation refuses invalid primary URLs; editorial generation, approval and research exports validate all source links.

Historical evidence is preserved. Existing immutable journal events and recorded identities are not rewritten: consumers must continue treating all historical payloads as untrusted data. Unsafe primary URLs are excluded from restored live snapshots. The library exposes `actionable_links_safe` and a quality warning for invalid historical sources; those sources cannot be opened through the app or used to approve/export a research package. The protected raw story/revision/event APIs can still return original historical strings for investigation. They are not a URL-execution interface.

URL validation does not verify article truth, publisher identity, media licensing, the safety of a destination website, or the absence of web redirects after a user opens a valid webpage.

## Conservative automatic story grouping

Existing canonical source URLs retain their story identity, so source corrections continue to update a recorded story and its revision history.

Different URLs are automatically grouped only when their ordered substantive headline wording agrees. Matching normalizes Unicode composition, capitalization and punctuation, and ignores articles (`a`, `an`, `the`). Short actor names, numbers, decimal values, dates, prepositions and word order remain significant. Explicit negation (`not`, `never`, `no`) can share a group so contrasting reports can be shown, but the separate disagreement heuristic marks the conflict. This is a deterministic lexical policy, not semantic understanding or verification of independent reporting.

The previous 72% token-overlap rule is no longer sufficient to merge identities. Different actors (NASA/China), countries (US/UK), dates, locations, magnitudes and travel directions remain separate. Uncertain paraphrases may also remain separate; this is preferable to silently attributing one event's evidence to another.

New groups carry `grouping_policy: equivalent-wording-v1`. Older groups encountered during ingestion carry `legacy-title-overlap` unless already labeled. Old groups and immutable events are not automatically split or reassigned. Explicit [manual split/merge](GROUP_REPAIRS.md) preserves old identities, immutable events, provider observations and drafts, records a durable receipt and requires editorial review again. Repaired groups are locked against automatic title matching; existing URLs still receive corrections. The inspector explicitly warns that older groups may have used broader similarity matching.

## Native bridge credentials

The Mac loads one cached credential from `bridge.token`. An unreadable, invalid, non-regular, symlinked or unowned token file is an error; it does not cause silent token replacement. Startup leaves the bridge offline and shows the file path and repair/reload action. An existing valid token is retained and tightened to mode 0600.

Creation uses 32 random bytes encoded as 64 hexadecimal characters, a private staged file, synchronization and exclusive atomic publication. Cooperating processes serialize creation/rotation through a bounded file lock. The token itself is never placed in logs.

Settings provides:

- **Reload credential:** reread a repaired or externally rotated file and apply it to the bridge. A read failure disables the bridge instead of keeping a misleading displayed token.
- **Rotate bridge credential:** confirm replacement, compare the saved token with the cached token, atomically save the new token, then update the running listener. Clients must reread the token file. If the file changed externally, rotation fails and asks for reload; it does not overwrite the changed credential.

The rotation test exercises the actual listener: the former token receives 401, the new saved token receives 200, and the listener remains usable. This is not a distributed rotation protocol: other application processes and external clients must reload their own cached credentials. Atomic file replacement protects a complete token value; it does not claim survival of every possible filesystem/power failure.

## Verification

Regression coverage includes cross-runtime URL fixtures, actual RSS rejection/coverage, legacy snapshot and export blocking, distinct-event grouping, equivalent-headline grouping, unchanged source identity after corrections, and immutable prior events. Native credential fixtures cover concurrent creators, private mode, directory/symlink/invalid-file failures, stale rotation requests and live-listener rotation. All fixtures use isolated temporary data and synthetic credentials.

This document records the scoped link/grouping/credential repair batch. The [release checklist](RELEASE_CHECKLIST.md) and [release notes](RELEASE_NOTES_1.2.0.md) track subsequent implementation, publication evidence and integration/accessibility limitations.
