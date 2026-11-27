# Agent News Bot 1.3.0 (macOS Native)

Agent News Bot **1.3.0** is a comprehensive security, reliability, performance, and editorial workflow upgrade for the **native macOS SwiftUI desktop app** (macOS 14+, Apple Silicon). Starting with `1.3.0`, active development focuses exclusively on native macOS; the legacy Linux Python daemon remains in the repository frozen at `1.2.0`.

## Security & Reliability Hardening

- **SSRF redirect guard (`HTTPTransport.swift`)**: Blocks remote feeds from redirecting to loopback, RFC 1918 private networks, link-local / cloud metadata IPs (`169.254.169.254`, `fe80::/10`), ULA, or multicast addresses; strips `Authorization` and `Cookie` headers on cross-origin redirects; normalizes scheme checks case-insensitively.
- **Hermes API HTTPS enforcement (`HermesClient.swift`)**: Restricts `http://` to loopback (`localhost`, `127.0.0.1`, `::1`) and requires `https://` for remote endpoints, matching `WorldMonitorClient`. Validates bearer token ASCII/length bounds.
- **Pinned `twitter-text.js` integrity (`XText.swift`)**: Verifies the bundled parser against its pinned SHA-256 digest before evaluating in JavaScriptCore and guards input length.
- **HTTP Bridge hardening (`AgentBridge.swift`, `ConsumerStore.swift`, `LibraryStore.swift`)**:
  - Guards against empty bearer tokens; uses constant-time comparison for consumer lease tokens.
  - Strict `\r\n` header framing, duplicate single-valued header rejection (`400`), `EINTR`-safe socket reads, explicit `408`/`431`/`413` JSON error envelopes, `pid` in `GET /health`, strict `/v1/stories//id` routing, and query-param validation on `/v1/library/settings`.
  - Rejects `POST /v1/library/repairs` with `409 Conflict` while a fetch is in progress, eliminating fetch/repair snapshot races.
- **SQLite & Concurrency Overhaul (`StoryStore.swift`, `Freshness.swift`, `Engine.swift`)**:
  - Schema/WAL/migration initialization runs once instead of on every query; sets `PRAGMA trusted_schema=OFF`; read-only queries use `BEGIN DEFERRED` instead of `BEGIN IMMEDIATE` write locks.
  - Moves `fetchFresh()` ingestion, cache updates, group reconciliation, and seen-ledger pruning off `@MainActor` so the UI stays responsive during refreshes.
  - Fixes `FreshnessStore.articleID` empty-URL fallback, `sqlite3_close` leak on open failure, `WeatherClient` URL construction without force-unwraps, `Engine.swift` `scanned` count, and full HTML numeric/named entity decoding.

## Workflow & Editorial UX Upgrades

- **Reopenable News Desk (`App.swift`)**: Switched to macOS 14 native `Window(id: "desk")` with `@Environment(\.openWindow)` in the menu bar (`Open Desk`) and `⌘N` (`Open News Desk`) so closing the window is never a dead end. Added `⌘B` (Bookmark) and `⇧⌘C` (Copy Tweet) shortcuts, disabled states on menu items, and split-screen-friendly minimum window dimensions (`920×620`).
- **One-Click Editorial Loop (`ContentView.swift`, `LibraryView.swift`)**:
  - Added **Open in editor / drafts**, **Bookmark**, and **Repair group…** directly in the live story inspector and row context menu.
  - Eliminated X draft typing stalls by moving `XText` character counting out of `View.body` into a debounced background `.task`, with red over-limit highlighting.
  - Added one-click **Copy** buttons on every platform draft tab (YouTube, X, TikTok, Narration), double-click / Return to open stories in Library, and moved all editor SQLite/export I/O off `@MainActor`.
  - Protected Watchlist deletion inside a dedicated destructive submenu; upgraded hashtags (`FlowTags`) to a true wrapping SwiftUI `Layout`; improved empty search state with a **Clear filter** action; added total count and selection highlight on **All stories**.
- **Searchable Story Merge Picker (`GroupRepairView.swift`)**: Added a live story search picker inside the Merge tab so you no longer need to copy-paste 24-hex IDs across sheets, and auto-selects Merge when a story has fewer than 2 sources.
- **Polished Settings & Operations (`SettingsView.swift`, `CoverageView.swift`, `OperatorControlsView.swift`)**:
  - Editable port `TextField` + stepper, masked bearer token with **Reveal** and **Copy** buttons, inline skill install status + explicit **Update existing skills…** backed by the bundled canonical `SKILL.md`, unsaved credential warning banner, and guard preventing unchecking the last desk.
  - Closing Settings no longer restarts auto-refresh or triggers a spurious fetch when refresh settings did not change.
  - Unified Operations reload (**Reload all**) and switched byte formatting to binary MiB (`countStyle: .binary`) to match storage budget inputs.
- **Human-Readable Research Exports (`ResearchPackage.swift`)**: Export folders are now named `Research-YYYY-MM-DD-<title-slug>-<short-id>` with tool-agnostic handoff notes.
- **Code Signing (`build.sh`)**: Added `--sign <identity>` support (defaulting to ad-hoc `-` for CI) and bundled `SKILL.md` directly into `Contents/Resources/`.
