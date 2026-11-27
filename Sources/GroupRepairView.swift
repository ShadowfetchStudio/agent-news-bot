import SwiftUI

struct GroupRepairView: View {
    let store: StoryStore, storyID: String
    var refreshSnapshot: () -> String?
    @Environment(\.dismiss) private var dismiss
    @State private var source: [String: Any] = [:]
    @State private var target: [String: Any] = [:]
    @State private var action = "split"
    @State private var targetID = ""
    @State private var chosen = Set<String>()
    @State private var reason = ""
    @State private var message = ""
    @State private var receipt = ""
    @State private var busy = false
    @State private var confirming = false
    @State private var finished = false
    @State private var operationID = UUID().uuidString
    private var sources: [[String: Any]] { source["sources"] as? [[String: Any]] ?? [] }
    private var canApply: Bool {
        !busy && !finished && source["merged_into"] == nil && !source.isEmpty &&
        !reason.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && reason.unicodeScalars.count <= 1000 &&
        (action == "split" ? (!chosen.isEmpty && chosen.count < sources.count) : (!target.isEmpty && target["id"] as? String == targetID && targetID != storyID && target["merged_into"] == nil))
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Repair story group").font(.title2.bold())
                Spacer(); Button("Done") { dismiss() }.keyboardShortcut(.cancelAction).disabled(busy)
            }
            Text(source["title"] as? String ?? "Loading story…").font(.headline)
            Text("Story ID: \(storyID) · revision \(source["revision"] as? Int ?? 0)").font(.caption).textSelection(.enabled)
            Text("Old revisions, source evidence and saved drafts stay in the journal. Every affected group requires editorial review again. Future title matching will not add unrelated sources to these groups.").font(.callout)
            Picker("Repair", selection: $action) {
                Text("Split selected reports into a new story").tag("split")
                Text("Merge this story into another").tag("merge")
            }.pickerStyle(.segmented).disabled(busy || finished)
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    if action == "split" {
                        Text("Select the reports to move. At least one must remain in the original group.").font(.caption)
                        ForEach(sources.indices, id: \.self) { i in
                            let url = sources[i]["url"] as? String ?? ""
                            Toggle(isOn: Binding(get: { chosen.contains(url) }, set: { if $0 { chosen.insert(url) } else { chosen.remove(url) } })) {
                                VStack(alignment: .leading) {
                                    Text(sources[i]["title"] as? String ?? "Untitled report")
                                    Text(url).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                                }
                            }.disabled(busy || finished)
                        }
                        Text("\(chosen.count) move to a new group; \(sources.count - chosen.count) stay. The new group starts without drafts.").font(.caption)
                    } else {
                        HStack {
                            TextField("Destination story ID (from Story tab or library API)", text: $targetID).textFieldStyle(.roundedBorder)
                                .disabled(busy || finished).onChange(of: targetID) { _, _ in target = [:] }
                            Button("Load destination") { Task { await loadTarget() } }.disabled(busy || finished || targetID.isEmpty)
                        }
                        if !target.isEmpty {
                            Text(target["title"] as? String ?? "").font(.headline)
                            Text("Destination revision \(target["revision"] as? Int ?? 0) · \((target["sources"] as? [Any] ?? []).count) sources").font(.caption)
                            Text("The destination keeps its title and drafts. This story becomes a retained history record pointing to the destination; its drafts are not overwritten or combined.")
                            ForEach((target["sources"] as? [[String: Any]] ?? []).indices, id: \.self) { index in
                                Text((target["sources"] as! [[String: Any]])[index]["url"] as? String ?? "").font(.caption).textSelection(.enabled)
                            }
                        }
                    }
                }
            }.frame(minHeight: 150)
            Text("Reason for the repair (required, up to 1,000 characters)").font(.caption)
            TextEditor(text: $reason).accessibilityLabel("Reason for grouping repair").frame(height: 65).border(.secondary.opacity(0.3)).disabled(busy || finished)
            if busy { ProgressView().controlSize(.small) }
            if !message.isEmpty { Text(message).textSelection(.enabled) }
            if !receipt.isEmpty { Text("Repair receipt: \(receipt)").font(.caption).textSelection(.enabled) }
            HStack {
                Button("Reload current evidence") { Task { await loadSource() } }.disabled(busy || finished)
                Spacer()
                if finished { Button("Refresh visible batch") { message = refreshSnapshot() ?? "Visible batch refreshed; review affected drafts." } }
                Button("Review and apply repair…") { confirming = true }.disabled(!canApply)
            }
        }.padding(20).frame(width: 790, height: 650)
        .task { await loadSource() }
        .confirmationDialog("Apply this \(action) and require editorial review?", isPresented: $confirming, titleVisibility: .visible) {
            Button("Apply \(action)") { Task { await apply() } }
        } message: {
            Text(action == "split" ? "Move \(chosen.count) selected reports to a new story. Preserve original drafts and record your reason." : "Merge into \(target["title"] as? String ?? "the selected destination"). Both groups' histories and drafts remain available.")
        }
    }
    private func read(_ id: String) async throws -> [String: Any] {
        let data = try await Task.detached { () throws -> Data in
            guard let row = try store.editorial(id) else { throw LibraryFailure.missing }
            return try JSONSerialization.data(withJSONObject: row)
        }.value
        return try JSONSerialization.jsonObject(with: data) as! [String: Any]
    }
    private func loadSource() async {
        busy = true; defer { busy = false }
        source = [:]
        do {
            source = try await read(storyID); chosen = []; target = [:]; operationID = UUID().uuidString
            message = source["merged_into"] == nil ? "" : "This group was already merged into \(source["merged_into"] as? String ?? ""). Open that story to repair it."
        } catch { message = "Could not load the story. Reload before making changes." }
    }
    private func loadTarget() async {
        busy = true; defer { busy = false }
        let requested = targetID
        do {
            let row = try await read(requested)
            guard row["id"] as? String == requested, requested != storyID, row["merged_into"] == nil else {
                target = [:]; message = "Use a different active canonical story ID."; return
            }
            target = row; message = ""
        } catch { target = [:]; message = "Destination not found or unavailable." }
    }
    private func apply() async {
        guard canApply else { return }
        busy = true; defer { busy = false }
        var request: [String: Any] = ["operation_id": operationID, "action": action, "source_id": storyID,
            "expected_revision": source["revision"] ?? 0,
            "expected_editorial_version": (source["editorial"] as? [String: Any])?["version"] ?? 0, "reason": reason]
        if action == "split" { request["source_urls"] = chosen.sorted() }
        else {
            request["target_id"] = targetID; request["target_revision"] = target["revision"] ?? 0
            request["target_editorial_version"] = (target["editorial"] as? [String: Any])?["version"] ?? 0
        }
        do {
            let data = try JSONSerialization.data(withJSONObject: request)
            _ = try await Task.detached {
                let request = try JSONSerialization.jsonObject(with: data) as! [String: Any]
                return try JSONSerialization.data(withJSONObject: store.repairGroup(request))
            }.value
            finished = true; receipt = operationID
            message = refreshSnapshot() ?? "Repair saved. Affected drafts now need review."
        } catch LibraryFailure.conflict(let detail) { message = detail }
        catch LibraryFailure.invalid(let detail) { message = detail }
        catch { message = "Repair could not be saved. Existing journal data is preserved; retry or reload evidence." }
    }
}
