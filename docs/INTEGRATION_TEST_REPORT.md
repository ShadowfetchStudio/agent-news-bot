> Historical integration baseline. See [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) for subsequent audit repairs and current release gates.

# World Monitor integration test report

Date: 2026-11-27 (America/New_York)
Branch: `feature/worldmonitor-integration`
Base: `cc3d51e` from `origin/main`
Build/package version: `1.2.0` (local development build; not a published release)

## Scope completed

- Optional English World Monitor feed-digest connector in Python and Swift.
- Disabled by default; existing direct feeds remain independently usable.
- Configurable server origin/edition/key; macOS Settings and Keychain support.
- Source-preserving story groups, durable revisions and provider provenance/context.
- Content updates distinguished from score, timestamp and mention-count churn.
- Independent durable agent cursors and explicit prefix acknowledgements.
- Existing snapshot API retained; JSON exports and Mac inspector expose grouped sources.
- RSS and quote checkpoints advance only after durable story persistence.
- CI extended to execute native Swift integration contracts on macOS.

## Verification

| Check | Result |
|---|---|
| Full suite on macOS Apple Silicon, Python 3.15 | **41 passed** in 9.13s |
| Full suite on native Pop!_OS Linux, Python 3.12.3 | **36 passed, 5 skipped** in 6.00s; skips are macOS-only Swift harness tests |
| Swift/Python synthetic digest contract | Passed; source URL, title, summary, desk and provider metadata parity |
| Actual localhost HTTP calls to both bridges | Passed; authentication, cursor replay, acknowledgement bounds, independent consumers and method validation |
| Actual localhost provider HTTP calls | Passed; digest ingestion, key header and redirects rejected without forwarding credentials |
| Failure cases | Passed; 401/429/503, malformed/oversized response, timeout, invalid config/date/link, database failure |
| Persistence and recovery | Passed; process restart, updates at the same URL, source additions, rollback of an entire failed batch, unacknowledged replay |
| Duplicate/concurrency handling | Passed; overlapping desk snippets do not oscillate; concurrent ingestion emits one event |
| Native application build | `./build.sh --arch arm64` succeeded |
| Bundle architecture/version | arm64, 1.2.0 |
| Signature verification | `codesign --verify --deep --strict` passed (ad-hoc signature) |
| Shell syntax and whitespace | `bash -n build.sh linux/install.sh` and `git diff --check` passed |
| Hosted World Monitor probe without credentials | HTTP **403**; authenticated live data availability is not verified |

App executable SHA-256:
`731981c715204c266a4f241d12164af239c297c3447e82447682c210a90dcdf2`

Tests used isolated temporary state and synthetic news fixtures. No production application was
installed or replaced, no running service was reconfigured, and no posts were published. The Linux
copy ran in an isolated temporary directory and virtual environment; unrelated jobs were left alone.
The bundled Mac app was built and its underlying connector/store/bridge executed via the native
harness. A manual visual Settings/inspector walkthrough was not performed. Compiler warnings remain
in existing concurrent agent-client code; the build and signature verification succeeded.

## Operational limits

A permitted World Monitor endpoint and any required credential must be configured before live
use. This implementation does not provision a World Monitor server or purchase hosted access.
The connector ingests the news digest, not all map/data layers. Grouping remains heuristic and
is not fact verification or proof of independent corroboration. Delivery is at-least-once;
consumers must make external side effects idempotent. The journal currently retains history
without automatic pruning. Watchlists, editorial briefs/ranking and VideoBot exports are follow-up
features, outside this first integration.

See [setup and API contract](worldmonitor.md) for configuration, migration and consumption details.
