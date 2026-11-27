import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var engine: NewsEngine
    @ObservedObject private var settings = SettingsStore.shared
    @State private var showCoverage = false
    @State private var showLibrary = false
    @State private var showAlerts = false
    @State private var alertCount: Int?

    var body: some View {
        NavigationSplitView {
            sidebar
                .navigationSplitViewColumnWidth(min: 210, ideal: 240, max: 280)
        } content: {
            articleList
                .navigationSplitViewColumnWidth(min: 380, ideal: 520)
        } detail: {
            inspector
        }
        .background(Theme.bgDeep)
        .toolbar { toolbar }
        .sheet(isPresented: Binding(get: { !settings.setupCompleted }, set: { _ in })) {
            SetupView().environmentObject(engine).interactiveDismissDisabled()
        }
        .sheet(isPresented: $showCoverage) { CoverageView().environmentObject(engine) }
        .sheet(isPresented:$showAlerts) { AlertsView(store:engine.stories) }
        .task(id:settings.setupCompleted) {
            guard settings.setupCompleted else { return }
            while !Task.isCancelled {
                do {
                    let store=engine.stories
                    let count=try await Task.detached { try store.alerts(["limit":"1"])["unread_count"] as? Int ?? 0 }.value
                    try Task.checkCancellation();alertCount=count
                } catch is CancellationError { break } catch { alertCount=nil }
                do { try await Task.sleep(nanoseconds:5_000_000_000) } catch { break }
            }
        }
        .sheet(isPresented: $showLibrary) { LibraryView(store: engine.stories, refreshSnapshot: engine.refreshRepairedSnapshot) }
    }

    private var sidebar: some View {
        List(selection: $engine.selectedDesk) {
            Section("Desks") {
                Button {
                    engine.selectedDesk = nil
                } label: {
                    Label("All stories", systemImage: "square.grid.2x2")
                }
                .buttonStyle(.plain)
                ForEach(Desk.allCases) { desk in
                    if engine.settings.enabledDesks.contains(desk) {
                        let count = engine.articles.filter { $0.displayDesks.contains(desk) }.count
                        NavigationLink(value: desk) {
                            Label {
                                HStack {
                                    Text(desk.title)
                                    Spacer()
                                    if count > 0 {
                                        Text("\(count)")
                                            .font(.caption.monospacedDigit())
                                            .foregroundStyle(Theme.gold)
                                    }
                                }
                            } icon: {
                                Image(systemName: desk.systemImage)
                            }
                        }
                    }
                }
            }
            Section("Agents") {
                AgentBadge(status: engine.hermes)
                AgentBadge(status: engine.openclaw)
                HStack {
                    Circle().fill(engine.bridgeReady ? Theme.success : Theme.danger).frame(width: 8, height: 8)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Local bridge")
                        Text(engine.bridgeReady ? engine.bridge.endpoint : "Offline — check Settings")
                            .font(.caption)
                            .foregroundStyle(Theme.textLow)
                    }
                }
            }
        }
        .listStyle(.sidebar)
    }

    private var articleList: some View {
        VStack(spacing: 0) {
            HStack {
                TextField("Filter titles, sources, tags", text: $engine.search)
                    .textFieldStyle(.roundedBorder)
                Text(engine.status)
                    .font(.caption)
                    .foregroundStyle(Theme.textMed)
                    .lineLimit(2)
            }
            .padding(12)
            if !engine.snapshotError.isEmpty {
                HStack(alignment: .top) {
                    Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(Theme.warning)
                    Text(engine.snapshotError).font(.caption).textSelection(.enabled)
                    Button("Retry save") { engine.retrySnapshotSave() }.disabled(engine.isFetching)
                }.padding(12)
            }
            Divider()
            if engine.isFetching {
                ProgressView("Fetching stories and revisions…")
                    .padding()
                Spacer()
            } else if engine.filtered.isEmpty {
                VStack(spacing: 10) {
                    Image(systemName: "sparkles")
                        .font(.largeTitle)
                        .foregroundStyle(Theme.gold)
                    Text("No stories in this desk’s lookback window")
                        .font(.title3.weight(.semibold))
                    Text("Fetch Fresh scans RSS, Hacker News, Polymarket, markets, and weather. New stories and changed reports inside the lookback window appear here. Open Coverage to inspect source failures.")
                        .foregroundStyle(Theme.textMed)
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: 420)
                    Button("Fetch Fresh") { Task { await engine.fetchFresh() } }
                        .keyboardShortcut("r", modifiers: [.command])
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List(engine.filtered, selection: $engine.selected) { article in
                    ArticleRow(article: article)
                        .tag(article)
                        .contextMenu {
                            Button("Copy tweet") { engine.copyTweet(article) }
                                .disabled(article.tweet.isEmpty || article.tweetChars > 280)
                            Button("Open story") { engine.openStory(article) }
                            Button("Send to Hermes") { Task { await engine.sendToHermes([article]) } }
                            Button("Send to OpenClaw") { Task { await engine.sendToOpenClaw([article]) } }
                        }
                }
                .listStyle(.inset)
            }
        }
    }

    private var inspector: some View {
        Group {
            if let article = engine.selected ?? engine.filtered.first {
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        Text(article.desks.map(\.title).joined(separator: " · ").uppercased())
                            .font(.caption.weight(.bold))
                            .foregroundStyle(Theme.gold)
                        Text(article.title)
                            .font(.title2.weight(.semibold))
                            .textSelection(.enabled)
                        Text("\(article.source) · \(DateStamp.age(article.published))")
                            .foregroundStyle(Theme.textMed)
                        if !article.summary.isEmpty {
                            Text(article.summary)
                                .foregroundStyle(Theme.textMed)
                                .textSelection(.enabled)
                        }
                        if article.wire["disagreement"] as? Bool == true {
                            Label("Reports differ — review before use", systemImage: "exclamationmark.triangle.fill")
                                .font(.headline).foregroundStyle(Theme.warning)
                            Text("Provider headlines or related reports disagree. This is a review flag, not a verdict about which report is correct.")
                                .font(.caption).foregroundStyle(Theme.textMed)
                        }
                        if let revision = article.wire["revision"] as? Int {
                            Text("Revision \(revision) · \(article.wire["change"] as? String ?? "new")")
                                .font(.caption).foregroundStyle(Theme.textLow)
                        }
                        let sources = article.wire["sources"] as? [[String: Any]] ?? []
                        if !sources.isEmpty {
                            VStack(alignment: .leading, spacing: 8) {
                                Text("Sources (\(sources.count))").font(.headline)
                                Text("Grouped by shared URL or equivalent substantive headline wording. Older groups may use broader similarity. Multiple links do not establish independent confirmation.")
                                    .font(.caption).foregroundStyle(Theme.textLow)
                                ForEach(sources.indices, id: \.self) { i in
                                    let source = sources[i]
                                    if let raw = source["url"] as? String, let url = ArticleURL.validated(raw) {
                                        Link(source["source"] as? String ?? raw, destination: url)
                                        Text(source["title"] as? String ?? "").font(.caption)
                                        let observations = source["observations"] as? [String: [String: Any]] ?? [:]
                                        if !observations.isEmpty {
                                            DisclosureGroup("Provider observations") {
                                                ForEach(observations.keys.sorted(), id: \.self) { provider in
                                                    let observation = observations[provider] ?? [:]
                                                    VStack(alignment: .leading, spacing: 4) {
                                                        Text(provider == "worldmonitor" ? "World Monitor" : provider.capitalized).font(.caption.bold())
                                                        Text(observation["title"] as? String ?? "").font(.caption)
                                                        Text(observation["summary"] as? String ?? "").font(.caption).foregroundStyle(Theme.textMed)
                                                        if provider == source["selected_provider"] as? String {
                                                            Text("Selected headline").font(.caption2).foregroundStyle(Theme.aqua)
                                                        }
                                                    }.padding(.vertical, 4).textSelection(.enabled)
                                                }
                                            }
                                        }
                                        if let metadata = source["metadata"] as? [String: Any],
                                           let location = metadata["locationName"] as? String, !location.isEmpty {
                                            Text("World Monitor location: \(location)").font(.caption).foregroundStyle(Theme.textLow)
                                        }
                                    }
                                }
                            }.padding(10).anbCard()
                        }
                        StoryHistoryView(store: engine.stories, storyID: article.id, revision: article.wire["revision"] as? Int ?? 0)
                        FlowTags(tags: article.hashtags)
                        VStack(alignment: .leading, spacing: 6) {
                            HStack {
                                Text("Tweet")
                                    .font(.caption.weight(.semibold))
                                Spacer()
                                Text("\(article.tweetChars) / 280")
                                    .font(.caption.monospacedDigit())
                                    .foregroundStyle(article.tweetChars > 280 ? Theme.danger : Theme.textLow)
                            }
                            Text(article.tweet.isEmpty ? "Draft unavailable. Refresh to retry validation." : article.tweet)
                                .font(.body)
                                .textSelection(.enabled)
                                .padding(10)
                                .anbCard()
                        }
                        HStack {
                            Button("Copy tweet") { engine.copyTweet(article) }
                                .disabled(article.tweet.isEmpty || article.tweetChars > 280)
                            Button("Open story") { engine.openStory(article) }
                        }
                        HStack {
                            Button("Send to Hermes") { Task { await engine.sendToHermes([article]) } }
                                .disabled(engine.agentTestRunning || !engine.hermes.canSend)
                            Button("Send to OpenClaw") { Task { await engine.sendToOpenClaw([article]) } }
                                .disabled(engine.agentTestRunning || !engine.openclaw.canSend)
                        }
                        if !engine.lastAgentReply.isEmpty {
                            Text("Last agent reply")
                                .font(.caption.weight(.semibold))
                            Text(engine.lastAgentReply)
                                .font(.system(.body, design: .monospaced))
                                .textSelection(.enabled)
                                .padding(10)
                                .anbCard()
                        }
                    }
                    .padding(20)
                }
            } else {
                Text("Select a story")
                    .foregroundStyle(Theme.textLow)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .background(Theme.bgRaised)
    }

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        ToolbarItem(placement: .navigation) {
            Text("Agent News Bot")
                .font(.headline)
                .foregroundStyle(Theme.textHi)
        }
        ToolbarItemGroup(placement: .primaryAction) {
            Button { showAlerts = true } label: {
                HStack(spacing:3) {
                    Image(systemName:(alertCount ?? 0)>0 ? "bell.badge.fill" : "bell")
                    if let alertCount, alertCount>0 { Text(String(alertCount)).font(.caption.monospacedDigit()) }
                }
            }.accessibilityLabel(Text(alertCount.map { "Alerts, \($0) unread" } ?? "Alerts unavailable"))
                .help("Saved-search alerts")
            Button { showLibrary = true } label: { Label("Library / drafts", systemImage: "books.vertical") }
            Button { showCoverage = true } label: { Label("Coverage", systemImage: "list.bullet.clipboard") }
            Button {
                Task { await engine.fetchFresh() }
            } label: {
                Label("Fetch Fresh", systemImage: "arrow.clockwise")
            }
            .disabled(engine.isFetching)
            Button {
                Task { await engine.sendToHermes(Array(engine.filtered.prefix(8))) }
            } label: {
                Label("Send desk to Hermes", systemImage: "paperplane")
            }
            .disabled(engine.agentTestRunning || !engine.hermes.canSend || engine.filtered.isEmpty)
            Button(action: engine.revealExports) {
                Label("Exports", systemImage: "folder")
            }
        }
    }
}

struct AgentBadge: View {
    let status: AgentStatus
    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Circle()
                .fill(status.ready ? Theme.success : Theme.warning)
                .frame(width: 8, height: 8)
                .padding(.top, 6)
            VStack(alignment: .leading, spacing: 2) {
                Text(status.label)
                Text(status.detail)
                    .font(.caption)
                    .foregroundStyle(Theme.textLow)
                    .textSelection(.enabled)
            }
        }
    }
}

struct ArticleRow: View {
    let article: Article
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(article.desk.title)
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(Theme.gold)
                Text(article.source)
                    .font(.caption)
                    .foregroundStyle(Theme.textLow)
                Spacer()
                Text(DateStamp.age(article.published))
                    .font(.caption)
                    .foregroundStyle(Theme.textLow)
            }
            Text(article.title)
                .font(.headline)
                .foregroundStyle(Theme.textHi)
                .fixedSize(horizontal: false, vertical: true)
            Text(article.hashtags.joined(separator: " "))
                .font(.caption)
                .foregroundStyle(Theme.aqua)
                .lineLimit(1)
        }
        .padding(.vertical, 4)
    }
}

struct FlowTags: View {
    let tags: [String]
    var body: some View {
        HStack(spacing: 6) {
            ForEach(tags, id: \.self) { tag in
                Text(tag)
                    .font(.caption.weight(.semibold))
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(Theme.gold.opacity(0.15))
                    .clipShape(Capsule())
            }
        }
    }
}
