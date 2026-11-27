import Foundation
import Combine
import AppKit

private final class LockedSnapshot: @unchecked Sendable {
    private let lock = NSLock()
    private var articles: [Article] = []
    private var desks: [DeskResult] = []
    private var fetchedAt: String?
    private var fetching = false

    func update(articles: [Article], desks: [DeskResult], fetchedAt: String) {
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
        return BridgeStatusSnapshot(fresh: articles.count, fetchedAt: fetchedAt, fetching: fetching)
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
    @Published var hermes = AgentStatus(kind: .hermes, label: "Hermes", detail: "Checking…", ready: false)
    @Published var openclaw = AgentStatus(kind: .openclaw, label: "OpenClaw", detail: "Checking…", ready: false)
    @Published var bridgeReady = false
    @Published var lastAgentReply = ""
    @Published var search = ""

    let settings = SettingsStore.shared
    let store = FreshnessStore(path: AppPaths.seenDB)
    let bridge = AgentBridge()
    let stories = StoryStore(path: AppPaths.support.appendingPathComponent("stories.sqlite"))

    private let snapshot = LockedSnapshot()
    private var completedFetches = 0

    var filtered: [Article] {
        var items = articles
        if let desk = selectedDesk {
            items = items.filter { $0.desks.contains(desk) }
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

    var bridgeToken: String { loadOrCreateToken() }

    func start() {
        settings.save()
        let snap = snapshot
        bridge.stories = stories
        bridge.articlesProvider = { snap.readArticles() }
        bridge.desksProvider = { snap.readDesks() }
        bridge.statusProvider = { snap.readStatus() }
        bridge.fetchHandler = { [weak self] in
            guard let self else { throw BridgeFetchFailure.unavailable }
            return try await self.fetchForBridge()
        }
        bridgeReady = bridge.start(port: settings.bridgePort, token: bridgeToken)
        if !bridgeReady {
            status = "Bridge failed to bind on 127.0.0.1:\(settings.bridgePort) (port in use)."
        }
        Task { await refreshAgents() }
    }

    enum BridgeFetchFailure: Error { case unavailable, busy, failed }

    func applyBridgeSettings() {
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
        for i in results.indices { results[i].articles = merged.filter { $0.desks.contains(results[i].desk) } }
        merged.sort { ($0.published ?? .distantPast) > ($1.published ?? .distantPast) }
        store.pruneSeen(olderThanDays: 30)

        let finished = Date()
        let fetchedAtISO = ISO8601DateFormatter().string(from: finished)
        desks = results
        coverageState = Coverage.summary(results.flatMap(\.coverageChecks))["state"] as? String ?? "unknown"
        articles = merged
        selected = merged.first
        snapshot.update(articles: merged, desks: results, fetchedAt: fetchedAtISO)

        let scanned = results.reduce(0) { $0 + $1.articles.count + $1.skippedOld + $1.skippedDup }
        report = FetchReport(started: started, finished: finished, freshCount: merged.count, scanned: scanned, desks: results)
        exportJSON(fetchedAt: fetchedAtISO)
        completedFetches += 1
        let errors = results.flatMap(\.sourceErrors).count
        let coverageLabel = "Coverage: " + coverageState + " · "
        if merged.isEmpty {
            status = errors > 0
                ? "No new stories returned · \(errors) source warnings. Full coverage could not be confirmed."
                : "No new or updated stories in the last \(Int(lookback))h."
        } else {
            status = "\(merged.count) new or updated stories · \(errors) source warnings"
        }
        status = coverageLabel + status
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
        if let url = URL(string: article.url) {
            NSWorkspace.shared.open(url)
        }
    }

    func sendToHermes(_ items: [Article]? = nil) async {
        let payload = items ?? (selected.map { [$0] } ?? Array(articles.prefix(8)))
        guard !payload.isEmpty else { return }
        status = "Sending \(payload.count) item(s) to Hermes…"
        do {
            let reply = try await HermesClient.send(articles: payload, settings: settings)
            lastAgentReply = reply
            status = "Hermes accepted \(payload.count) item(s)"
        } catch {
            status = "Hermes send failed: \(error.localizedDescription)"
        }
    }

    func sendToOpenClaw(_ items: [Article]? = nil) async {
        let payload = items ?? (selected.map { [$0] } ?? Array(articles.prefix(8)))
        guard !payload.isEmpty else { return }
        status = "Sending \(payload.count) item(s) to OpenClaw…"
        do {
            let reply = try await OpenClawClient.send(articles: payload, settings: settings)
            lastAgentReply = reply
            status = "OpenClaw accepted \(payload.count) item(s)"
        } catch {
            status = "OpenClaw send failed: \(error.localizedDescription)"
        }
    }

    func refreshAgents() async {
        hermes = await HermesClient.probe(settings: settings)
        openclaw = await OpenClawClient.probe(settings: settings)
    }

    func revealExports() {
        NSWorkspace.shared.open(AppPaths.exports)
    }

    private func exportJSON(fetchedAt: String) {
        let payload: [String: Any] = [
            "fetched_at": fetchedAt,
            "total_articles": articles.count,
            "coverage": Coverage.summary(desks.flatMap(\.coverageChecks)),
            "desks": desks.map(\.wire),
            "articles": articles.map(\.wire),
            "lookback_hours": settings.lookbackHours,
            "categories": Dictionary(uniqueKeysWithValues: Desk.allCases.map { desk in
                (desk.title, articles.filter { $0.desks.contains(desk) }.map(\.wire))
            })
        ]
        let name = "agentnewsbot_\(Int(Date().timeIntervalSince1970)).json"
        let url = AppPaths.exports.appendingPathComponent(name)
        let latestURL = AppPaths.exports.appendingPathComponent("latest.json")
        if let data = try? JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: url, options: .atomic)
            try? data.write(to: latestURL, options: .atomic)
            pruneOldExports(keep: 50)
        }
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

    private func loadOrCreateToken() -> String {
        let url = AppPaths.tokenFile
        if let existing = try? String(contentsOf: url).trimmingCharacters(in: .whitespacesAndNewlines), existing.count >= 16 {
            return existing
        }
        let token = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        try? token.write(to: url, atomically: true, encoding: .utf8)
        try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        return token
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
        - `GET /v1/fresh?desk=<desk>&limit=<n>` — repeatable current snapshot
        - `GET /v1/events?consumer=<name>` — persistent ordered revisions
        - `POST /v1/events/ack?consumer=<name>&through=<next_cursor>` — acknowledge processed events only
        - `GET /v1/articles/<id>` — single article in the fresh batch
        - `GET /v1/stories/<id>` — current durable story and provider context
        - `GET /v1/stories/<id>/revisions` — recorded revisions and differences
        - `GET /v1/desks` — per-desk counts and source errors
        - `GET /v1/status` — snapshot status
        - `POST /v1/fetch` — request refresh (202 accepted, 409 already running); poll /v1/status fetch_job for completion

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
