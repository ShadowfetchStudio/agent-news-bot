import SwiftUI

private struct OperatorSnapshot: @unchecked Sendable { let value: [String: Any] }

struct OperatorControlsView: View {
    @EnvironmentObject private var engine: NewsEngine
    @State private var report: [String: Any] = [:]
    @State private var ceiling = ""
    @State private var reserve = ""
    @State private var busy = false
    @State private var message = ""
    @State private var confirmReload = false
    private var sections: [String: Any] { report["sections"] as? [String: Any] ?? [:] }
    private var storage: [String: Any] { sections["storage"] as? [String: Any] ?? [:] }
    private var policy: [String: Int] { storage["policy"] as? [String: Int] ?? [:] }
    private var changed: Bool {
        ceiling != String((policy["max_journal_bytes"] ?? 0)/1048576) || reserve != String((policy["min_free_bytes"] ?? 0)/1048576)
    }
    private func bytes(_ key: String) -> String { ByteCountFormatter.string(fromByteCount: Int64(storage[key] as? Int ?? 0), countStyle: .file) }
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Operations").font(.headline)
                Spacer()
                Button("Reload operations") {
                    if !policy.isEmpty && changed { confirmReload = true }
                    else { Task { await reload() } }
                }.disabled(busy)
            }
            Text("Observed: \(report["observed_at"] as? String ?? "Not loaded") · Separate local snapshots; reload after changes.")
                .font(.caption).foregroundStyle(.secondary)
            if let errors = report["errors"] as? [String: String] {
                ForEach(errors.keys.sorted(), id: \.self) { key in Text("\(key): \(errors[key]!)").foregroundStyle(.red) }
            }
            if !storage.isEmpty {
                Text("Journal \(bytes("database_bytes")) · Reusable pages \(bytes("reusable_bytes")) · WAL \(bytes("wal_bytes")) · Volume free \(bytes("free_bytes"))").font(.callout)
                Text("\(storage["stories"] as? Int ?? 0) stories · \(storage["events"] as? Int ?? 0) events · \(storage["unread_alerts"] as? Int ?? 0) unread alerts · \(storage["bookmarked_stories"] as? Int ?? 0) bookmarks").font(.caption)
                if storage["ingestion_blocked"] as? Bool == true {
                    Text("Ingestion paused: " + (storage["blocking_reasons"] as? [String] ?? []).joined(separator: ", "))
                        .foregroundStyle(.red).font(.callout.bold())
                } else if storage["nearing_ceiling"] as? Bool == true {
                    Text("Journal is near its ingestion ceiling.").foregroundStyle(Theme.warning)
                }
                HStack {
                    VStack(alignment:.leading) {
                        Text("Journal ceiling (MiB)").font(.caption)
                        TextField("1–8192", text: $ceiling).accessibilityLabel("Journal ceiling in MiB")
                    }
                    VStack(alignment:.leading) {
                        Text("Free-space reserve (MiB)").font(.caption)
                        TextField("64–8192", text: $reserve).accessibilityLabel("Free space reserve in MiB")
                    }
                    Button("Save limits") { Task { await save() } }.disabled(busy || !changed)
                }
                Text("Ceiling: 1–8192 MiB. Reserve: 64–8192 MiB. Ingestion only; manual edits, exports, caches and backups are outside this limit. Reads, acknowledgements and recovery remain available. Nothing is automatically pruned or acknowledged.")
                    .font(.caption).foregroundStyle(.secondary)
                Text("To recover: back up, inspect retention holds and consumer lag, then review eligible history or raise the budget. Pruning makes pages reusable; it does not shrink the database file. Never reset a lagging consumer just to clear its backlog.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if !message.isEmpty { Text(message).font(.callout).textSelection(.enabled).accessibilityElement(children: .ignore).accessibilityLabel("Storage feedback: " + message).id(message) }
            if let delivery = sections["delivery"] as? [String: Any] {
                let consumers = delivery["consumers"] as? [[String: Any]] ?? []
                DisclosureGroup("Consumer delivery (\(consumers.count))") {
                    if consumers.isEmpty { Text("No registered consumers. Unacknowledged research is retained.").font(.caption) }
                    ForEach(Array(consumers.enumerated()), id: \.offset) { _, row in
                        VStack(alignment:.leading, spacing:4) {
                            Text(row["consumer"] as? String ?? "Consumer").font(.callout.bold())
                            Text("Lag: \(row["lag"] as? Int ?? 0) events · Acknowledged: \(row["cursor"] as? Int ?? 0) · Offered: \(row["offered"] as? Int ?? 0) · Generation: \(row["generation"] as? Int ?? 0)").font(.caption)
                            Text("Last acknowledgement: \(timestamp(row["last_ack_at"]))").font(.caption)
                            if let worker = row["lease_worker"] as? String, !worker.isEmpty {
                                Text("Lease worker: \(worker) · Expires: \(timestamp(row["lease_expires_at"]))").font(.caption)
                            }
                        }.accessibilityElement(children:.contain).padding(.vertical,4)
                    }
                }
            }
        }.padding(12).anbCard().disabled(busy)
            .task { await reload() }
            .confirmationDialog("Discard unsaved storage limits and reload?", isPresented: $confirmReload) {
                Button("Discard and reload", role: .destructive) { Task { await reload() } }
                Button("Cancel", role: .cancel) { }
            }
    }
    private func timestamp(_ raw: Any?) -> String {
        guard let seconds = raw as? Double, seconds > 0 else { return "Never" }
        return Date(timeIntervalSince1970:seconds).formatted(date:.abbreviated,time:.standard)
    }
    @MainActor private func reload() async {
        busy = true; defer { busy = false }
        let journal = engine.stories, health = engine.store.health
        let snapshot = await Task.detached { OperatorSnapshot(value: OperatorReport.read(stories: journal,health: health)) }.value
        report = snapshot.value
        ceiling = policy["max_journal_bytes"].map { String($0/1048576) } ?? ""
        reserve = policy["min_free_bytes"].map { String($0/1048576) } ?? ""
        message = ""
    }
    @MainActor private func save() async {
        guard let max = Int(ceiling), (1...8192).contains(max), let min = Int(reserve), (64...8192).contains(min), let version = policy["version"] else {
            message = "Use whole MiB within the displayed ranges."; return
        }
        busy = true
        let journal = engine.stories
        do {
            try await Task.detached { _ = try journal.storageChange(["expected_version":version,"max_journal_bytes":max*1048576,"min_free_bytes":min*1048576]) }.value
            await reload(); message = "Storage limits saved."
        } catch LibraryFailure.conflict {
            message = "Limits changed elsewhere. Your input is preserved; reload and review before saving again."
        } catch { message = "Limits could not be saved. Your input is preserved; inspect local storage and reload." }
        busy = false
    }
}
