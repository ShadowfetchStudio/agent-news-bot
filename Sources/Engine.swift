import Foundation
import Combine
import AppKit
import CryptoKit

private final class LockedSnapshot: @unchecked Sendable {
    private let lock = NSLock()
    private var articles: [Article] = []
    private var desks: [DeskResult] = []
    private var fetchedAt: String?
    private var fetching = false
    private var persistenceError: String?

    func setPersistenceError(_ value: String?) {
        lock.lock(); defer { lock.unlock() }
        persistenceError = value
    }

    func update(articles: [Article], desks: [DeskResult], fetchedAt: String?) {
        lock.lock()
        self.articles = articles
        self.desks = desks
        self.fetchedAt = fetchedAt
        lock.unlock()
    }

    func setFetching(_ value: Bool) {
        lock.lock()
        self.fetching = value
        lock.unlock()
    }

    func readArticles() -> [Article] {
        lock.lock()
        defer { lock.unlock() }
        return articles
    }

    func readDesks() -> [DeskResult] {
        lock.lock()
        defer { lock.unlock() }
        return desks
    }

    func readStatus() -> BridgeStatusSnapshot {
        lock.lock()
        defer { lock.unlock() }
        return BridgeStatusSnapshot(fresh: articles.filter { $0.wire["carried"] as? Bool != true }.count, fetchedAt: fetchedAt, fetching: fetching, persistenceError: persistenceError)
    }
}

@MainActor
final class NewsEngine: ObservableObject {
    static let shared = NewsEngine()

    @Published var articles: [Article] = []
    @Published var desks: [DeskResult] = []
    @Published var selectedDesk: Desk? = .breaking
    @Published var selected: Article?
    @Published var isFetching = false
    @Published var status = "Ready. Fetch Fresh finds new stories and updates inside the lookback window."
    @Published var report: FetchReport?
    @Published var coverageState = "unknown"
    @Published private(set) var snapshotError = ""
    @Published var hermes = AgentStatus(kind: .hermes, label: "Hermes", detail: "Checking…", ready: false)
    @Published var openclaw = AgentStatus(kind: .openclaw, label: "OpenClaw", detail: "Checking…", ready: false)
    @Published var bridgeReady = false
    @Published var lastAgentReply = ""
    @Published private(set) var agentTestRunning = false
    private var verifiedAgents: [AgentKind: (String, Date)] = [:]
    @Published var search = ""

    let settings = SettingsStore.shared
    let store = FreshnessStore(path: AppPaths.seenDB)
    let bridge = AgentBridge()
    let stories = StoryStore(path: AppPaths.support.appendingPathComponent("stories.sqlite"))

    private let snapshot = LockedSnapshot()
    private var completedFetches = 0
    private var started = false
    private var autoRefreshTask: Task<Void, Never>?

    var filtered: [Article] {
        var items = articles
        if let desk = selectedDesk {
            items = items.filter { $0.displayDesks.contains(desk) }
        }
        let q = search.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if !q.isEmpty {
            items = items.filter {
                $0.title.lowercased().contains(q)
                    || $0.source.lowercased().contains(q)
                    || $0.hashtags.joined().lowercased().contains(q)
            }
        }
        return items
    }

    @Published private(set) var bridgeToken = ""
    @Published private(set) var bridgeCredentialError = ""

    func start() {
        guard !started else { return }
        started = true
        if let selectedDesk, !settings.enabledDesks.contains(selectedDesk) { self.selectedDesk = nil }
        restoreSnapshot()
        settings.save()
        let snap = snapshot
        bridge.stories = stories
        bridge.groupRepairHandler = { [weak self] in
            DispatchQueue.main.sync { self?.refreshRepairedSnapshot() }
        }
        bridge.articlesProvider = { snap.readArticles() }
        bridge.desksProvider = { snap.readDesks() }
        bridge.statusProvider = { snap.readStatus() }
        bridge.fetchHandler = { [weak self] in
            guard let self else { throw BridgeFetchFailure.unavailable }
            return try await self.fetchForBridge()
        }
        reloadBridgeCredential()
        if settings.setupCompleted { Task { await refreshAgents() } }
        configureAutomaticRefresh()
    }

    private func restoreSnapshot() {
        let url = AppPaths.exports.appendingPathComponent("latest.json")
        guard FileManager.default.fileExists(atPath: url.path) else { return }
        do {
            let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
            guard size <= 16 * 1024 * 1024 else { throw StoryStore.Failure.database }
            let data = try Data(contentsOf: url)
            guard data.count <= 16 * 1024 * 1024,
                  var payload = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw StoryStore.Failure.database }
            payload["articles"] = try stories.reconcileGroups(payload["articles"] as? [[String: Any]] ?? [])
            let restored = SnapshotPolicy.restore(payload, lookback: settings.lookbackHours, enabled: settings.enabledDesks, limit: settings.maxPerDesk)
            articles = restored.articles; desks = restored.desks; selected = articles.first
            coverageState = Coverage.summary(desks.flatMap(\.coverageChecks))["state"] as? String ?? "unknown"
            snapshot.update(articles: articles, desks: desks, fetchedAt: restored.fetchedAt)
            status = "Restored \(articles.count) stories. Source checks are from the previous session."
        } catch { status = "Saved snapshot could not be restored. The story journal is unchanged; fetch again." }
    }

    @discardableResult func refreshRepairedSnapshot() -> String? {
        do {
            let current = try stories.reconcileGroups(articles.map(\.wire)).map { try Article.fromWire($0) }
            articles = SnapshotPolicy.merge(previous: current, fresh: [],
                previousFetchedAt: snapshot.readStatus().fetchedAt.flatMap(DateStamp.parse), lookback: settings.lookbackHours,
                enabled: settings.enabledDesks, limit: settings.maxPerDesk)
            for i in desks.indices { desks[i].articles = articles.filter { $0.displayDesks.contains(desks[i].desk) } }
            selected = articles.first { $0.id == selected?.id } ?? articles.first
            let fetchedAt = snapshot.readStatus().fetchedAt
            snapshot.update(articles: articles, desks: desks, fetchedAt: fetchedAt)
            if let fetchedAt, !persistSnapshot(fetchedAt: fetchedAt) { return snapshotError }
            status = "Story groups repaired. Review affected drafts before approval."
            return nil
        } catch {
            let message = "Repair saved in the journal, but the visible snapshot could not be refreshed. Reload the app to retry."
            status = message; snapshotError = message; snapshot.setPersistenceError(message)
            return message
        }
    }

    func configureAutomaticRefresh() {
        autoRefreshTask?.cancel()
        autoRefreshTask = nil
        guard settings.setupCompleted && settings.autoRefresh else { return }
        let delay = UInt64(min(120, max(5, settings.refreshMinutes))) * 60 * 1_000_000_000
        autoRefreshTask = Task { [weak self] in
            // Opening/reopening windows must not create overlapping refresh loops.
            // First check starts after a short grace period; subsequent checks use the selected interval.
            do { try await Task.sleep(nanoseconds: 2_000_000_000) } catch { return }
            while !Task.isCancelled {
                guard let self else { return }
                await self.fetchFresh()
                do { try await Task.sleep(nanoseconds: delay) } catch { return }
            }
        }
    }

    enum BridgeFetchFailure: Error { case unavailable, busy, failed }

    func applyBridgeSettings() {
        guard !bridgeToken.isEmpty else { bridgeReady = false; return }
        let requested = settings.bridgePort
        let applied = bridge.start(port: requested, token: bridgeToken)
        bridgeReady = bridge.isListening
        if !applied {
            settings.bridgePort = Int(bridge.port)
            settings.save()
            status = "Cannot use port \(requested). " + (bridgeReady ? "Bridge remains on \(bridge.port)." : "Bridge is offline.")
        }
    }

    private func fetchForBridge() async throws -> Int {
        guard !isFetching else { throw BridgeFetchFailure.busy }
        let previous = completedFetches
        await fetchFresh()
        guard completedFetches != previous else { throw BridgeFetchFailure.failed }
        return snapshot.readArticles().count
    }

    func fetchFresh() async {
        guard settings.setupCompleted else { status = "Complete setup before fetching."; return }
        guard !isFetching else { return }
        isFetching = true
        snapshot.setFetching(true)
        defer { isFetching = false; snapshot.setFetching(false) }
        status = "Scanning desks for new stories and updates…"
        let started = Date()
        let lookback = settings.lookbackHours
        let maxItems = settings.maxPerDesk
        let city = settings.weatherCity
        let symbols = settings.marketSymbols
        let enabled = settings.enabledDesks
        await refreshAgents()

        var results: [DeskResult] = []
        await withTaskGroup(of: DeskResult.self) { group in
            for desk in Desk.allCases where enabled.contains(desk) {
                group.addTask {
                    switch desk {
                    case .hackerNews:
                        return await HNClient.fetch(lookbackHours: lookback, maxItems: maxItems, store: self.store)
                    case .polymarket:
                        return await PolymarketClient.fetch(lookbackHours: lookback, maxItems: maxItems, store: self.store)
                    case .markets:
                        return await MarketsClient.fetch(symbols: symbols, maxItems: maxItems, store: self.store)
                    case .weather:
                        return await WeatherClient.fetch(city: city, store: self.store)
                    default:
                        return await RSSClient.fetch(desk: desk, lookbackHours: lookback, maxItems: maxItems, store: self.store)
                    }
                }
            }
            for await result in group {
                results.append(result)
            }
        }

        let wm = await WorldMonitorClient.fetch(enabled: settings.worldMonitorEnabled, base: settings.worldMonitorURL,
            key: settings.worldMonitorKey, variant: settings.worldMonitorVariant, lookback: lookback, limit: maxItems)
        for original in wm {
            var result = original
            if !enabled.contains(result.desk) { result.articles = [] }
            if result.articles.isEmpty && result.sourceErrors.isEmpty && result.sourceChecks.isEmpty { continue }
            if let i = results.firstIndex(where: { $0.desk == result.desk }) {
                results[i].articles += result.articles
                results[i].skippedOld += result.skippedOld
                results[i].sourceErrors += result.sourceErrors
                results[i].sourceChecks += result.coverageChecks
            } else { results.append(result) }
        }
        results.sort { $0.desk.title < $1.desk.title }
        let candidates = results.flatMap(\.articles)
        var merged: [Article]
        do { merged = try stories.ingest(candidates) }
        catch { status = "Story storage failed. No deliveries acknowledged; retry the fetch."; return }
        for result in results {
            for update in result.cacheUpdates {
                store.saveFeedCache(update.url, etag: update.etag, lastModified: update.lastModified, body: update.body, observedAt: update.observedAt)
            }
            for (symbol, payload) in result.quoteUpdates { store.saveQuote(symbol, payload: payload) }
        }
        for article in candidates {
            store.mark(id: article.id, url: article.url, title: article.title, desk: article.desk.rawValue)
        }
        let changedCount = merged.count
        let current: [Article]
        do { current = try stories.reconcileGroups(articles.map(\.wire)).map { try Article.fromWire($0) } }
        catch { status = "Could not reconcile repaired groups; the story journal is preserved."; return }
        merged = SnapshotPolicy.merge(previous: current, fresh: merged,
            previousFetchedAt: snapshot.readStatus().fetchedAt.flatMap(DateStamp.parse), lookback: lookback,
            enabled: enabled, limit: maxItems)
        for i in results.indices { results[i].articles = merged.filter { $0.displayDesks.contains(results[i].desk) } }
        store.pruneSeen(olderThanDays: 30)

        let finished = Date()
        let fetchedAtISO = ISO8601DateFormatter().string(from: finished)
        desks = results
        coverageState = Coverage.summary(results.flatMap(\.coverageChecks))["state"] as? String ?? "unknown"
        articles = merged
        selected = merged.first { $0.id == selected?.id } ?? merged.first
        snapshot.update(articles: merged, desks: results, fetchedAt: fetchedAtISO)

        let scanned = results.reduce(0) { $0 + $1.articles.count + $1.skippedOld + $1.skippedDup }
        report = FetchReport(started: started, finished: finished, freshCount: changedCount, scanned: scanned, desks: results)
        let saved = persistSnapshot(fetchedAt: fetchedAtISO)
        if saved { completedFetches += 1 }
        let errors = results.flatMap(\.sourceErrors).count
        let coverageLabel = "Coverage: " + coverageState + " · "
        if changedCount == 0 {
            status = errors > 0
                ? "No new stories returned · \(errors) source warnings. Full coverage could not be confirmed."
                : "No new or updated stories in the last \(Int(lookback))h."
        } else {
            status = "\(changedCount) new or updated stories · \(errors) source warnings"
        }
        status = coverageLabel + status
        if !saved { status += " · Snapshot not saved; see storage warning." }
        isFetching = false
        snapshot.setFetching(false)
    }

    func copyTweet(_ article: Article) {
        guard !article.tweet.isEmpty, article.tweetChars <= 280 else { status = "Draft is unavailable or over the X limit."; return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(article.tweet, forType: .string)
        status = "Copied tweet for \(article.source)"
    }

    func openStory(_ article: Article) {
        if let url = ArticleURL.validated(article.url) {
            NSWorkspace.shared.open(url)
        } else { status = "Cannot open a non-web or invalid article link." }
    }

    func sendToHermes(_ items: [Article]? = nil) async {
        let payload = items ?? (selected.map { [$0] } ?? Array(articles.prefix(8)))
        guard !payload.isEmpty else { return }
        status = "Sending \(payload.count) item(s) to Hermes…"
        let identity = agentIdentity(.hermes)
        do {
            let reply = try await HermesClient.send(articles: payload, settings: settings)
            lastAgentReply = reply
            if identity == agentIdentity(.hermes) { recordAgentReply(.hermes) }
            status = "Hermes accepted \(payload.count) item(s)"
        } catch {
            verifiedAgents[.hermes] = nil; hermes.ready = false
            hermes.detail = "Reply failed: \(error.localizedDescription)"
            status = "Hermes send failed: \(error.localizedDescription)"
        }
    }

    func sendToOpenClaw(_ items: [Article]? = nil) async {
        let payload = items ?? (selected.map { [$0] } ?? Array(articles.prefix(8)))
        guard !payload.isEmpty else { return }
        status = "Sending \(payload.count) item(s) to OpenClaw…"
        let identity = agentIdentity(.openclaw)
        do {
            let reply = try await OpenClawClient.send(articles: payload, settings: settings)
            lastAgentReply = reply
            if identity == agentIdentity(.openclaw) { recordAgentReply(.openclaw) }
            status = "OpenClaw accepted \(payload.count) item(s)"
        } catch {
            verifiedAgents[.openclaw] = nil; openclaw.ready = false
            openclaw.detail = "Reply failed: \(error.localizedDescription)"
            status = "OpenClaw send failed: \(error.localizedDescription)"
        }
    }

    func refreshAgents() async {
        let hermesIdentity = agentIdentity(.hermes), clawIdentity = agentIdentity(.openclaw)
        let hermesResult = await HermesClient.probe(settings: settings)
        let clawResult = await OpenClawClient.probe(settings: settings)
        if hermesIdentity == agentIdentity(.hermes) { hermes = hermesResult }
        if clawIdentity == agentIdentity(.openclaw) { openclaw = clawResult }
        applyVerifiedReply(.hermes); applyVerifiedReply(.openclaw)
    }

    private func agentIdentity(_ kind: AgentKind) -> String {
        let values = kind == .hermes
            ? [settings.hermesAPIURL, settings.hermesModel, settings.hermesToken, String(settings.hermesUseCLI), HermesClient.which("hermes") ?? ""]
            : [settings.openClawURL, settings.openClawToken, HermesClient.which("openclaw") ?? ""]
        return SHA256.hash(data: Data(values.joined(separator: "\u{0}").utf8)).map { String(format: "%02x", $0) }.joined()
    }

    func invalidateAgent(_ kind: AgentKind) {
        verifiedAgents[kind] = nil
        var value = kind == .hermes ? hermes : openclaw
        value.ready = false; value.canSend = false
        value.detail = "Settings changed or test in progress. Check the connection or test a reply."
        if kind == .hermes { hermes = value } else { openclaw = value }
    }

    private func recordAgentReply(_ kind: AgentKind) {
        verifiedAgents[kind] = (agentIdentity(kind), Date())
        if kind == .hermes { hermes.canSend = true } else { openclaw.canSend = true }
        applyVerifiedReply(kind)
    }

    private func applyVerifiedReply(_ kind: AgentKind) {
        guard let (identity, date) = verifiedAgents[kind], identity == agentIdentity(kind) else { return }
        var value = kind == .hermes ? hermes : openclaw
        guard value.canSend else { return }
        value.ready = true
        value.detail = "Reply verified at \(date.formatted(date: .omitted, time: .standard)). Re-test after changing agent configuration."
        if kind == .hermes { hermes = value } else { openclaw = value }
    }

    func testAgent(_ kind: AgentKind) async {
        guard !agentTestRunning else { return }
        agentTestRunning = true
        defer { agentTestRunning = false }
        let identity = agentIdentity(kind)
        invalidateAgent(kind)
        do {
            if kind == .hermes { try await HermesClient.test(settings: settings) }
            else { try await OpenClawClient.test(settings: settings) }
            guard identity == agentIdentity(kind) else { status = "Agent settings changed during the test. Run it again."; return }
            await refreshAgents()
            guard identity == agentIdentity(kind) else { status = "Agent settings changed during the test. Run it again."; return }
            recordAgentReply(kind)
            status = "\(kind.rawValue) connection test passed. No story material was sent."
        } catch {
            var value = kind == .hermes ? hermes : openclaw
            value.ready = false; value.detail = "Test failed: \(error.localizedDescription)"
            if kind == .hermes { hermes = value } else { openclaw = value }
            status = value.detail
        }
    }

    func revealExports() {
        NSWorkspace.shared.open(AppPaths.exports)
    }

    func retrySnapshotSave() {
        guard !isFetching, let fetchedAt = snapshot.readStatus().fetchedAt else { return }
        if persistSnapshot(fetchedAt: fetchedAt) { status = "Current snapshot saved. No sources were fetched again." }
    }

    @discardableResult
    private func persistSnapshot(fetchedAt: String) -> Bool {
        do {
            try exportJSON(fetchedAt: fetchedAt)
            snapshotError = ""; snapshot.setPersistenceError(nil)
            return true
        } catch {
            snapshotError = "Snapshot not saved: \(error.localizedDescription) The preceding snapshot and story journal are preserved. Check \(AppPaths.exports.path), then Retry save."
            snapshot.setPersistenceError(snapshotError)
            return false
        }
    }

    private func exportJSON(fetchedAt: String) throws {
        let payload: [String: Any] = [
            "fetched_at": fetchedAt,
            "total_articles": articles.count,
            "coverage": Coverage.summary(desks.flatMap(\.coverageChecks)),
            "desks": desks.map(\.wire),
            "articles": articles.map(\.wire),
            "lookback_hours": settings.lookbackHours,
            "categories": Dictionary(uniqueKeysWithValues: Desk.allCases.map { desk in
                (desk.title, articles.filter { $0.displayDesks.contains(desk) }.map(\.wire))
            })
        ]
        try SnapshotWriter.save(payload, in: AppPaths.exports)
        pruneOldExports(keep: 50)
    }

    private func pruneOldExports(keep: Int) {
        guard let files = try? FileManager.default.contentsOfDirectory(
            at: AppPaths.exports,
            includingPropertiesForKeys: [.contentModificationDateKey],
            options: [.skipsHiddenFiles]
        ) else { return }
        let timestamped = files
            .filter { $0.lastPathComponent.hasPrefix("agentnewsbot_") && $0.pathExtension == "json" }
            .sorted { $0.lastPathComponent > $1.lastPathComponent }
        for old in timestamped.dropFirst(keep) {
            try? FileManager.default.removeItem(at: old)
        }
    }

    func reloadBridgeCredential() {
        do {
            let token = try BridgeCredential.loadOrCreate(at: AppPaths.tokenFile)
            bridgeToken = token; bridgeCredentialError = ""
            applyBridgeSettings()
        } catch {
            bridge.stop(); bridgeReady = false; bridgeToken = ""
            bridgeCredentialError = "Bridge disabled: \(error.localizedDescription) Repair \(AppPaths.tokenFile.path), then Reload credential."
            status = bridgeCredentialError
        }
    }

    func rotateBridgeCredential() {
        do {
            let token = try BridgeCredential.rotate(at: AppPaths.tokenFile, expected: bridgeToken)
            bridge.replaceToken(token)
            bridgeToken = token; bridgeCredentialError = ""
            status = "Bridge credential rotated. Connected clients must reload the token file."
        } catch {
            bridgeCredentialError = "Credential rotation failed: \(error.localizedDescription)"
        }
    }

    func installAgentSkills() {
        let tokenPath = AppPaths.tokenFile.path
        let skill = """
        ---
        name: agent-news-bot
        description: Pull new and updated news from the local Agent News Bot desk (RSS, AI, HN, Polymarket, markets, weather) over localhost HTTP.
        version: 1.2.0
        ---

        # Agent News Bot

        Agent News Bot is a macOS newsroom on this machine. It keeps a durable story journal. Snapshot reads can repeat. Use consumer events and acknowledge only after processing.

        Base URL: \(bridge.endpoint)
        Token file (0600): `\(tokenPath)`
        Auth: `Authorization: Bearer $(cat "\(tokenPath)")`

        ## When to use
        Use this when you need current headlines, AI/tech stories, Hacker News, Polymarket, stock moves, or weather for social or newsroom work.

        ## Calls
        - `GET /health`
        - `GET /v1/capabilities` — authenticate, check API/contract/feature versions before automation
        - `GET /v1/fresh?desk=<desk>&limit=<n>` — repeatable current snapshot
        - `GET /v1/events?consumer=<name>` — persistent ordered revisions
        - `POST /v1/events/ack?consumer=<name>&through=<next_cursor>&generation=<generation>` — acknowledge processed events only
        - `GET /v1/articles/<id>` — single article in the fresh batch
        - `GET /v1/stories/<id>` — current durable story and provider context
        - `GET /v1/stories/<id>/revisions` — recorded revisions and differences
        - `GET /v1/desks` — per-desk counts and source errors
        - `GET /v1/status` — snapshot status
        - `POST /v1/fetch` — request refresh (202 accepted, 409 already running); poll /v1/status fetch_job for completion

        Errors preserve `error` and add versioned `error_detail` codes/recovery guidance. Do not blindly retry writes after ambiguous failures. Reload consumer/editorial state on 409. Repair retries require the same operation ID and identical request. Use `GET /v1/consumers` for generation/lag/lease status; active leases require X-Consumer-Lease. Commit every delivered event before acknowledging. Capabilities describe support, not source/agent readiness; inspect coverage separately. Empty output does not prove coverage is current. No request automatically authorizes publication or clears media rights.

        Do not invent URLs. Prefer the tweet and hashtags fields when drafting social posts. Never print the bearer token in public channels.
        """
        var installed = 0, preserved = 0, failed = 0
        for rel in [".hermes/skills/agent-news-bot", ".agents/skills/agent-news-bot"] {
            let dest = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(rel, isDirectory: true)
            let file = dest.appendingPathComponent("SKILL.md")
            do {
                if try SkillInstaller.install(skill, at: file) { installed += 1 }
                else { preserved += 1 }
            } catch { failed += 1 }
        }
        status = "Agent skills: \(installed) installed, \(preserved) existing files preserved, \(failed) failed."
    }
}
