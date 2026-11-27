import SwiftUI
import AppKit

private struct LibraryItem: Identifiable {
    let id: String, title: String, source: String, status: String
    let bookmarked: Bool, priority: Int
}
private struct LibraryPreference: Identifiable {
    let kind: String, name: String, filters: [String: String]
    var id: String { kind + ":" + name }
}

struct LibraryView: View {
    let store: StoryStore
    var refreshSnapshot: () -> String? = { nil }
    @Environment(\.dismiss) private var dismiss
    @State private var q = ""
    @State private var entity = ""
    @State private var source = ""
    @State private var desk = ""
    @State private var provider = ""
    @State private var status = ""
    @State private var sort = "recent"
    @State private var bookmarked = false
    @State private var includeMuted = false
    @State private var includeMerged = false
    @State private var watchName = ""
    @State private var message = ""
    @State private var busy = false
    @State private var offset = 0
    @State private var nextOffset: Int?
    @State private var refresh = 0
    @State private var rows: [LibraryItem] = []
    @State private var preferences: [LibraryPreference] = []
    @State private var selected: String?
    @State private var showAlerts = false
    private var filters: [String: String] {
        ["q": q, "entity": entity, "source": source, "desk": desk, "provider": provider, "status": status,
         "sort": sort, "bookmarked": bookmarked ? "true" : "false", "include_muted": includeMuted ? "true" : "false", "include_merged": includeMerged ? "true" : "false"]
    }
    private var filterKey: String { filters.keys.sorted().map { $0 + "=" + filters[$0]! }.joined(separator: "|") }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Research library").font(.title2.bold())
                Text("All retained stories, beyond the fresh batch").font(.caption).foregroundStyle(.secondary)
                Spacer(); Button("Done") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            HStack {
                TextField("Search title, summary and sources", text: $q)
                TextField("Entity or location keyword", text: $entity)
                TextField("Source name or URL", text: $source)
            }.textFieldStyle(.roundedBorder)
            HStack {
                Picker("Desk", selection: $desk) { Text("All").tag(""); ForEach(Desk.allCases) { Text($0.title).tag($0.rawValue) } }
                Picker("Provider", selection: $provider) { Text("All").tag(""); Text("Direct").tag("direct"); Text("World Monitor").tag("worldmonitor") }
                Picker("State", selection: $status) { Text("All").tag(""); ForEach(LibraryModel.states.sorted(), id: \.self) { Text($0.replacingOccurrences(of: "_", with: " ")).tag($0) } }
                Picker("Order", selection: $sort) { Text("Recent revisions").tag("recent"); Text("Review priority").tag("priority") }
            }
            HStack {
                Toggle("Bookmarks", isOn: $bookmarked); Toggle("Include muted sources", isOn: $includeMuted); Toggle("Include merged history", isOn: $includeMerged)
                Spacer()
                Menu("Watchlists") {
                    let saved = preferences.filter { $0.kind == "search" }
                    if saved.isEmpty {
                        Text("No saved watchlists")
                    } else {
                        ForEach(saved) { item in
                            Button(item.name) { apply(item.filters); watchName = item.name }
                        }
                        Divider()
                        Menu("Delete watchlist") {
                            ForEach(saved) { item in
                                Button("Delete \(item.name)", role: .destructive) {
                                    preference(kind: "search", name: item.name, value: NSNull())
                                }
                            }
                        }
                    }
                }
                TextField("Watchlist name", text: $watchName).frame(width: 150)
                Button("Save filters") { preference(kind: "search", name: watchName, value: filters) }.disabled(watchName.trimmingCharacters(in: .whitespaces).isEmpty)
                Menu("Muted sources") {
                    ForEach(preferences.filter { $0.kind == "mute" }) { item in
                        Button("Unmute \(item.name)") { preference(kind: "mute", name: item.name, value: NSNull()) }
                    }
                }
            }
            HStack {
                Button("Watch for changes") { enableAlerts() }.disabled(busy || includeMerged || watchName.trimmingCharacters(in: .whitespaces).isEmpty)
                    .help("Watch active story revisions. Turn off Include merged history to enable alerts.")
                Button("Alerts inbox") { showAlerts = true }
                Button("Review queue") { apply(["status":"needs_review","sort":"priority","include_muted":"true"]) }
                Text("Alerts start with future matching revisions; approval review is separate.").font(.caption).foregroundStyle(.secondary)
            }
            Text("Priority highlights source breadth, corrections and disagreement for review. It is not a truth or credibility score.").font(.caption).foregroundStyle(.secondary)
            if !message.isEmpty { Text(message).font(.callout).textSelection(.enabled) }
            if busy { ProgressView().controlSize(.small) }
            List(rows, selection: $selected) { item in
                VStack(alignment: .leading, spacing: 4) {
                    HStack { Text(item.title).font(.headline); if item.bookmarked { Image(systemName: "bookmark.fill").foregroundStyle(Theme.gold) } }
                    Text("\(item.source) · \(item.status.replacingOccurrences(of: "_", with: " ")) · review priority \(item.priority)").font(.caption).foregroundStyle(.secondary)
                }
                .padding(.vertical, 5)
                .contentShape(Rectangle())
                .onTapGesture(count: 2) { selected = item.id; editorID = item.id }
                .tag(item.id)
            }
            HStack {
                Button("Previous") { offset = max(0, offset - 50) }.disabled(offset == 0 || busy)
                Text("Showing \(offset + (rows.isEmpty ? 0 : 1))–\(offset + rows.count)").font(.caption)
                Button("Next") { if let nextOffset { offset = nextOffset } }.disabled(nextOffset == nil || busy)
                Spacer()
                Button("Repair grouping…") { if let selected { repairID = selected } }.disabled(selected == nil)
                Button("Open story and drafts") { if let selected { editorID = selected } }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
                    .disabled(selected == nil)
            }
        }.padding(20).frame(minWidth: 1000, minHeight: 650)
        .sheet(isPresented:$showAlerts) { AlertsView(store:store) }
        .onChange(of: filterKey) { _, _ in offset = 0 }
        .task(id: filterKey + ":\(offset):\(refresh)") { await load() }
        .sheet(item: Binding(get: { editorID.map { EditorSelection(id: $0) } }, set: { editorID = $0?.id }), onDismiss: { refresh += 1 }) { item in
            EditorialView(store: store, storyID: item.id) { refresh += 1 }
        }
        .sheet(item: Binding(get: { repairID.map { EditorSelection(id: $0) } }, set: { repairID = $0?.id }), onDismiss: { refresh += 1 }) { item in
            GroupRepairView(store: store, storyID: item.id, refreshSnapshot: refreshSnapshot)
        }
    }
    @State private var repairID: String?
    @State private var editorID: String?
    private struct EditorSelection: Identifiable { let id: String }
    private func enableAlerts() {
        guard !busy else { return }
        busy=true
        let name=watchName.trimmingCharacters(in:.whitespacesAndNewlines)
        let values=filters.filter { AlertSchema.filterKeys.contains($0.key) }, db=store
        Task {
            defer { busy=false }
            do {
                try await Task.detached {
                    let subscriptions=try db.alerts()["subscriptions"] as? [[String:Any]] ?? []
                    let previous=subscriptions.first { $0["name"] as? String == name }
                    _=try db.alertChange(["action":"subscribe","name":name,"expected_version":previous?["version"] ?? 0,"filters":values,"enabled":true])
                }.value
                message="Watching future matching revisions for \(name). Open Alerts inbox to review them."
            } catch { message="Could not enable this alert. Use a name up to 80 characters and reload before retrying." }
        }
    }
    private func apply(_ values: [String: String]) {
        q = values["q"] ?? ""; entity = values["entity"] ?? ""; source = values["source"] ?? ""
        desk = values["desk"] ?? ""; provider = values["provider"] ?? ""; status = values["status"] ?? ""
        sort = values["sort"] ?? "recent"; bookmarked = values["bookmarked"] == "true"; includeMuted = values["include_muted"] == "true"; includeMerged = values["include_merged"] == "true"
    }
    private func load() async {
        do {
            try await Task.sleep(nanoseconds: 200_000_000)
            busy = true; defer { busy = false }
            let query = filters.merging(["offset": String(offset)]) { _, new in new }
            let data = try await Task.detached { try JSONSerialization.data(withJSONObject: ["page": store.library(query), "settings": store.librarySettings()]) }.value
            try Task.checkCancellation()
            let value = try JSONSerialization.jsonObject(with: data) as! [String: Any]
            let page = value["page"] as! [String: Any]
            rows = (page["stories"] as? [[String: Any]] ?? []).map { row in
                let editorial = row["editorial"] as? [String: Any] ?? [:]
                return LibraryItem(id: row["id"] as? String ?? "", title: row["title"] as? String ?? "", source: row["source"] as? String ?? "",
                    status: editorial["status"] as? String ?? "unreviewed", bookmarked: editorial["bookmarked"] as? Bool ?? false, priority: row["review_priority"] as? Int ?? 0)
            }
            if let selected, !rows.contains(where: { $0.id == selected }) { self.selected = nil }
            nextOffset = page["next_offset"] as? Int
            preferences = ((value["settings"] as? [String: Any])?["preferences"] as? [[String: Any]] ?? []).map { LibraryPreference(kind: $0["kind"] as? String ?? "", name: $0["name"] as? String ?? "", filters: $0["value"] as? [String: String] ?? [:]) }
            message = rows.isEmpty ? "No retained stories match these filters." : ""
        } catch is CancellationError {} catch { message = "Could not load the library: \(error.localizedDescription)" }
    }
    private func preference(kind: String, name: String, value: Any) {
        let db = store
        let encodedValue = try? JSONSerialization.data(withJSONObject: value, options: [.fragmentsAllowed])
        Task {
            do {
                try await Task.detached {
                    let decoded: Any = encodedValue.flatMap { try? JSONSerialization.jsonObject(with: $0, options: [.fragmentsAllowed]) } ?? NSNull()
                    _ = try db.librarySettings(kind: kind, name: name, value: decoded, write: true)
                }.value
                refresh += 1
            } catch { message = "Could not save this preference." }
        }
    }
}

struct EditorialView: View {
    let store: StoryStore, storyID: String
    var changed: () -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var title = ""
    @State private var notes = ""
    @State private var status = "unreviewed"
    @State private var message = ""
    @State private var drafts: [String: String] = [:]
    @State private var savedDrafts: [String: String] = [:]
    @State private var savedStatus = "unreviewed"
    @State private var platformStates: [String: [String: Any]] = [:]
    @State private var quality: [String] = []
    @State private var sources: [String] = []
    @State private var revision = 0
    @State private var version = 0
    @State private var bookmarked = false
    @State private var loaded = false
    @State private var confirmGenerate = false
    @State private var pendingDiscard: String?
    @State private var savedFingerprint = ""
    @State private var summary = ""
    @State private var sourceURLs: [String] = []
    @State private var groupingDetails = ""
    @State private var savedBrief: [String: Any]?
    @State private var retired = false
    @State private var busy = false
    @State private var xWeightedLength: Int?
    @State private var xValid = true
    @State private var copiedField: String?
    private var fingerprint: String {
        let value: [String: Any] = ["notes": notes, "status": status, "bookmarked": bookmarked, "drafts": drafts]
        return (try? JSONSerialization.data(withJSONObject: value, options: .sortedKeys)).map { String(decoding: $0, as: UTF8.self) } ?? ""
    }
    private var dirty: Bool { loaded && fingerprint != savedFingerprint }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack { Text(title.isEmpty ? "Story editor" : title).font(.title3.bold()); Spacer(); Button("Done") { if dirty { pendingDiscard = "close" } else { dismiss() } } }
            HStack {
                Text("Story revision \(revision) · draft version \(version)").font(.caption)
                Toggle("Bookmark", isOn: $bookmarked).disabled(retired)
                Picker("Whole-package state", selection: $status) { ForEach(LibraryModel.states.sorted(), id: \.self) { Text($0.replacingOccurrences(of: "_", with: " ")).tag($0) } }.disabled(retired)
            }
            Text("Approve individual platforms in their tabs. Changing the whole-package state applies to every platform. Nothing is posted automatically.").font(.caption).foregroundStyle(.secondary)
            ForEach(quality, id: \.self) { Text($0).font(.caption).foregroundStyle(.secondary) }
            TabView {
                ScrollView {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Story ID: \(storyID)").font(.caption).textSelection(.enabled)
                        if !groupingDetails.isEmpty { Text(groupingDetails).font(.callout).textSelection(.enabled) }
                        Text(summary.isEmpty ? "No summary was supplied by this source." : summary).textSelection(.enabled)
                        Text("Read the original sources").font(.headline)
                        ForEach(sourceURLs, id: \.self) { raw in
                            if let url = ArticleURL.validated(raw) {
                                Link(raw, destination: url).textSelection(.enabled)
                            } else { Text(raw).textSelection(.enabled) }
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading).padding()
                }.tabItem { Text("Story") }
                Group {
                    if let savedBrief { EditorialBriefView(brief: savedBrief) }
                    else { Text("The saved research changed or could not be read. Reload the saved version to view its brief.").padding() }
                }.tabItem { Text("Brief") }
                draftEditor("youtube_title", label: "YouTube title", second: "youtube_description").tabItem { Text("YouTube") }
                draftEditor("x_post", label: "X post").tabItem { Text("X") }
                draftEditor("tiktok_caption", label: "TikTok caption").tabItem { Text("TikTok") }
                draftEditor("narration", label: "Narration starter — verify and develop before recording").tabItem { Text("Narration") }
                ScrollView { StoryHistoryView(store: store, storyID: storyID, revision: revision).padding() }.tabItem { Text("History") }
                VStack(alignment: .leading) {
                    Text("Research notes"); TextEditor(text: $notes).disabled(retired)
                    ForEach(sources, id: \.self) { source in HStack { Text(source); Spacer(); Button("Mute in library") { mute(source) } } }
                }.padding().tabItem { Text("Notes / sources") }
            }
            if !message.isEmpty { Text(message).font(.callout).textSelection(.enabled) }
            if busy { ProgressView().controlSize(.small) }
            if dirty { Text("Unsaved changes. The brief and exports contain the last saved version.").font(.caption).foregroundStyle(.orange) }
            HStack {
                Button("Reload saved version") { if dirty { pendingDiscard = "reload" } else { load() } }.disabled(busy)
                Button(drafts.isEmpty ? "Generate draft templates" : "Regenerate templates…") { if drafts.isEmpty { save(generate: true) } else { confirmGenerate = true } }.disabled(retired || busy)
                Spacer()
                Button("Export saved research package") { exportPackage() }.disabled(!loaded || retired || busy)
                Button("Save changes") { save(generate: false) }.buttonStyle(.borderedProminent).disabled(!loaded || retired || busy)
            }
        }.padding(20).frame(minWidth: 850, minHeight: 660)
        .onAppear { load() }
        .task(id: drafts["x_post"] ?? "") {
            let text = drafts["x_post"] ?? ""
            if text.isEmpty { xWeightedLength = 0; xValid = true; return }
            do { try await Task.sleep(nanoseconds: 120_000_000) } catch { return }
            let result = await Task.detached { () -> (Int, Bool)? in
                guard let m = try? XText.request(["op": "measure", "text": text]) else { return nil }
                return (m["weightedLength"] as? Int ?? 0, m["valid"] as? Bool ?? false)
            }.value
            if let result {
                xWeightedLength = result.0
                xValid = result.1
            } else {
                xWeightedLength = nil
                xValid = false
            }
        }
        .interactiveDismissDisabled(dirty)
        .confirmationDialog("Discard unsaved changes?", isPresented: Binding(get: { pendingDiscard != nil }, set: { if !$0 { pendingDiscard = nil } })) {
            Button("Discard changes", role: .destructive) {
                let action = pendingDiscard; pendingDiscard = nil
                if action == "close" { dismiss() } else { load() }
            }
            Button("Keep editing", role: .cancel) { pendingDiscard = nil }
        }
        .confirmationDialog("Replace the current drafts with templates from this revision?", isPresented: $confirmGenerate) {
            Button("Replace drafts", role: .destructive) { save(generate: true) }
        }
    }
    private func copyDraft(_ text: String, field: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        copiedField = field
        Task {
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            if copiedField == field { copiedField = nil }
        }
    }
    @ViewBuilder private func draftEditor(_ key: String, label: String, second: String? = nil) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            let platform = LibraryModel.platforms.first(where: { $0.value.contains(key) })!.key
            let display = platform == "x" ? "X" : platform == "youtube" ? "YouTube" : platform == "tiktok" ? "TikTok" : "narration"
            let edited = LibraryModel.platforms[platform]!.contains { (drafts[$0] ?? "") != (savedDrafts[$0] ?? "") }
            Text("Saved " + display + " state: " + ((platformStates[platform]?["status"] as? String ?? "unreviewed").replacingOccurrences(of: "_", with: " "))).font(.caption.bold())
            if edited { Text("Unsaved edits will clear this platform's prior approval.").font(.caption).foregroundStyle(.orange) }
            HStack {
                Button("Save and approve " + display) { save(generate: false, platform: platform, decision: "approved") }
                Menu("Set " + display + " state") {
                    Button("Needs review") { save(generate: false, platform: platform, decision: "needs_review") }
                    Button("Rejected") { save(generate: false, platform: platform, decision: "rejected") }
                    Button("Draft") { save(generate: false, platform: platform, decision: "draft") }
                }
                Spacer()
                Button(copiedField == key ? "Copied" : "Copy \(display)") {
                    let combined = second.map { (drafts[key] ?? "") + "\n\n" + (drafts[$0] ?? "") } ?? (drafts[key] ?? "")
                    copyDraft(combined, field: key)
                }
                .disabled((drafts[key] ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }.disabled(!loaded || status != savedStatus || busy)
            if status != savedStatus { Text("Save or revert the whole-package state change first.").font(.caption) }
            Text(label).font(.caption)
            TextEditor(text: Binding(get: { drafts[key] ?? "" }, set: { drafts[key] = $0 })).font(.body).accessibilityLabel(label)
            if key == "x_post" {
                let overLimit = !xValid || (xWeightedLength ?? 0) > 280
                Text(xWeightedLength.map { "\($0) / 280 weighted characters" } ?? "X validation unavailable")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(overLimit ? Theme.danger : .secondary)
            }
            if let second { Text("Description").font(.caption); TextEditor(text: Binding(get: { drafts[second] ?? "" }, set: { drafts[second] = $0 })).accessibilityLabel("YouTube description") }
        }.padding().disabled(retired)
    }
    private func apply(_ row: [String: Any], brief: [String: Any]?, repairReason: String?) {
        let state = row["editorial"] as? [String: Any] ?? [:]
        title = row["title"] as? String ?? ""; revision = row["revision"] as? Int ?? 0; version = state["version"] as? Int ?? 0
        status = state["status"] as? String ?? "unreviewed"; bookmarked = state["bookmarked"] as? Bool ?? false
        notes = state["notes"] as? String ?? ""; drafts = state["drafts"] as? [String: String] ?? [:]
        savedDrafts = drafts; savedStatus = status; platformStates = state["platforms"] as? [String: [String: Any]] ?? [:]
        quality = row["quality_notes"] as? [String] ?? []
        summary = row["summary"] as? String ?? ""
        retired = row["merged_into"] != nil
        var details: [String] = []
        if let id = row["merged_into"] as? String { details.append("Retained history — merged into \(id). Drafts are read-only; use the destination for new work.") }
        if let id = row["split_from"] as? String { details.append("Split from \(id). Earlier shared evidence remains in that story's history.") }
        if let id = row["last_repair_id"] as? String {
            details.append("Repair receipt: " + id)
            if let repairReason { details.append("Reason: " + repairReason) }
        }
        groupingDetails = details.joined(separator: "\n")
        sourceURLs = Array(Set((row["sources"] as? [[String: Any]] ?? []).compactMap { $0["url"] as? String })).sorted()
        sources = Array(Set((row["sources"] as? [[String: Any]] ?? []).compactMap { $0["source"] as? String })).sorted()
        if let brief, brief["story_revision"] as? Int == revision, brief["editorial_version"] as? Int == version {
            savedBrief = brief
        } else { savedBrief = nil }
        loaded = true
        savedFingerprint = fingerprint
    }
    private func hydrate(_ rowData: Data) async throws -> ([String: Any], [String: Any]?, String?) {
        let db = store, sid = storyID
        let bundle = try await Task.detached { () throws -> (Data, Data?, String?) in
            let row = try JSONSerialization.jsonObject(with: rowData) as! [String: Any]
            let reason = (row["last_repair_id"] as? String).flatMap { (try? db.repairReceipt($0))?["reason"] as? String }
            let briefData = (try? db.researchBrief(sid)).flatMap { try? JSONSerialization.data(withJSONObject: $0) }
            return (rowData, briefData, reason)
        }.value
        let row = try JSONSerialization.jsonObject(with: bundle.0) as! [String: Any]
        let brief = bundle.1.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
        return (row, brief, bundle.2)
    }
    private func load() {
        let db = store, sid = storyID
        busy = true
        Task {
            defer { busy = false }
            do {
                let rowData = try await Task.detached { () throws -> Data in
                    guard let row = try db.editorial(sid) else { throw LibraryFailure.missing }
                    return try JSONSerialization.data(withJSONObject: row)
                }.value
                let (row, brief, reason) = try await hydrate(rowData)
                apply(row, brief: brief, repairReason: reason); message = ""
            } catch { message = "Could not load the retained story." }
        }
    }
    private func save(generate: Bool, platform: String? = nil, decision: String? = nil) {
        var change: [String: Any] = ["expected_revision": revision, "expected_editorial_version": version, "bookmarked": bookmarked, "notes": notes]
        if generate { change["generate"] = true }
        else {
            if let platform, let decision { change["platform_status"] = [platform: decision] }
            else if status != savedStatus { change["status"] = status }
            let patch = Dictionary(uniqueKeysWithValues: LibraryModel.fields.keys.filter { (drafts[$0] ?? "") != (savedDrafts[$0] ?? "") }.map { ($0, drafts[$0] ?? "") })
            if !patch.isEmpty { change["draft_patch"] = patch }
        }
        guard let changeData = try? JSONSerialization.data(withJSONObject: change) else { return }
        let db = store, sid = storyID
        busy = true
        Task {
            defer { busy = false }
            do {
                let rowData = try await Task.detached { () throws -> Data in
                    let parsed = try JSONSerialization.jsonObject(with: changeData) as! [String: Any]
                    let saved = try db.saveEditorial(sid, change: parsed)
                    return try JSONSerialization.data(withJSONObject: saved)
                }.value
                let (row, brief, reason) = try await hydrate(rowData)
                apply(row, brief: brief, repairReason: reason); message = "Saved. No posts were published."; changed()
            }
            catch LibraryFailure.conflict(let detail) { message = detail + ". Your unsaved text is still here; copy it before reloading." }
            catch LibraryFailure.invalid(let detail) { message = detail }
            catch { message = "Save failed. Your edits are still here." }
        }
    }
    private func mute(_ source: String) {
        let db = store
        busy = true
        Task {
            defer { busy = false }
            do {
                try await Task.detached { _ = try db.librarySettings(kind: "mute", name: source, value: true, write: true) }.value
                message = "Muted in the library. Evidence and agent deliveries are preserved."; changed()
            } catch { message = "Could not mute this source." }
        }
    }
    private func exportPackage() {
        let db = store, sid = storyID
        busy = true
        Task {
            defer { busy = false }
            do {
                let folder = try await Task.detached { () throws -> URL in
                    let payload = try db.researchExport(sid)
                    return try ResearchPackage.write(payload, in: AppPaths.exports)
                }.value
                NSWorkspace.shared.activateFileViewerSelecting([folder]); message = "Exported the saved version to \(folder.lastPathComponent)."
            } catch { message = "Could not export the research package." }
        }
    }
}
