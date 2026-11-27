import SwiftUI

private struct AlertSubscription: Identifiable {
    let name: String, version: Int, enabled: Bool, filters: [String: String]
    var id: String { name }
}
private struct AlertMatch: Identifiable {
    let name: String, storyID: String, title: String
    let seq: Int, revision: Int, current: Int, subscriptionVersion: Int, unread: Bool
    let merged: String?
    var id: String { "\(name.utf8.count):" + name + storyID }
}

struct AlertsView: View {
    let store: StoryStore
    @Environment(\.dismiss) private var dismiss
    @State private var subscriptions: [AlertSubscription] = []
    @State private var matches: [AlertMatch] = []
    @State private var unreadOnly = true
    @State private var unreadCount = 0
    @State private var offset = 0
    @State private var nextOffset: Int?
    @State private var message = ""
    @State private var busy = false
    @State private var refresh = 0
    @State private var removing: AlertSubscription?
    @State private var editorID: Selection?
    private struct Selection: Identifiable { let id: String }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Saved-search alerts").font(.title2.bold())
                Text("\(unreadCount) unread").foregroundStyle(.secondary)
                Spacer()
                Button("Reload") { refresh += 1 }
                Button("Done") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            Text("Local notifications for new matching revisions. Repeated matches for a story share one inbox item. Reading is not approval; use Research library → Review queue for changed approved drafts.")
                .font(.caption).foregroundStyle(.secondary)
            if !message.isEmpty { Text(message).foregroundStyle(Theme.warning).textSelection(.enabled) }
            if subscriptions.isEmpty {
                Text("Enable a named search in Research library with Watch for changes.").foregroundStyle(.secondary)
            } else {
                ScrollView {
                  LazyVStack(alignment: .leading, spacing: 12) {
                   ForEach(subscriptions) { subscription in
                    HStack {
                        VStack(alignment: .leading) {
                            Text(subscription.name).font(.headline)
                            Text("Version \(subscription.version) · \(subscription.enabled ? "Watching future revisions" : "Paused")").font(.caption)
                        }
                        Spacer()
                        Button(subscription.enabled ? "Pause" : "Resume") {
                            change(["action":"subscribe","name":subscription.name,"expected_version":subscription.version,
                                    "filters":subscription.filters,"enabled":!subscription.enabled])
                        }
                        Button("Remove…") { removing = subscription }
                    }.accessibilityElement(children:.contain)
                   }
                  }.padding(12)
                }.frame(height: 125)
            }
            Toggle("Unread only", isOn: $unreadOnly).onChange(of: unreadOnly) { _, _ in offset = 0 }
            if matches.isEmpty { Text("No alerts on this page. Searches only notify for revisions recorded while enabled.").foregroundStyle(.secondary) }
            ScrollView {
              LazyVStack(alignment: .leading, spacing: 12) {
               ForEach(matches) { item in
                VStack(alignment: .leading, spacing: 6) {
                    Text(item.title).font(.headline)
                    Text("\(item.name) · matched revision \(item.revision) · current revision \(item.current) · filter version \(item.subscriptionVersion)")
                        .font(.caption).foregroundStyle(.secondary)
                    if item.revision != item.current { Text("The story changed again after this match. Review the current report and retained history.").font(.caption).foregroundStyle(Theme.warning) }
                    if item.merged != nil { Text("This story was merged; Open current story follows its destination.").font(.caption).foregroundStyle(Theme.warning) }
                    HStack {
                        Button("Open current story") { editorID = Selection(id:item.merged ?? item.storyID) }
                        if item.unread {
                            Button("Mark seen") { change(["action":"mark_seen","name":item.name,"story_id":item.storyID,"expected_seq":item.seq]) }
                        } else { Text("Seen").font(.caption).foregroundStyle(.secondary) }
                    }
                }.padding(.vertical, 5).accessibilityElement(children:.contain)
               }
              }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
            }
            HStack {
                Button("Previous") { offset = max(0,offset-50) }.disabled(offset == 0 || busy)
                Text("Showing \(offset + (matches.isEmpty ? 0 : 1))–\(offset+matches.count)").font(.caption)
                Button("Next") { if let nextOffset { offset=nextOffset } }.disabled(nextOffset == nil || busy)
                Spacer()
                if busy { ProgressView().controlSize(.small) }
            }
        }.padding(20).frame(minWidth:800,minHeight:600)
        .task(id:"\(unreadOnly):\(offset):\(refresh)") {
            while !Task.isCancelled {
                await load()
                do { try await Task.sleep(nanoseconds:5_000_000_000) } catch { break }
            }
        }
        .confirmationDialog("Remove this search and its inbox items? Research stories, drafts and agent checkpoints remain.", isPresented:Binding(get:{removing != nil},set:{if !$0 {removing=nil}})) {
            if let removing { Button("Remove \(removing.name)",role:.destructive) { change(["action":"delete","name":removing.name,"expected_version":removing.version]);self.removing=nil } }
        }
        .sheet(item:$editorID,onDismiss:{refresh += 1}) { item in EditorialView(store:store,storyID:item.id) { refresh += 1 } }
    }
    private func load() async {
        do {
            let query=["unread":unreadOnly ? "true":"false","offset":String(offset)], db=store
            let data=try await Task.detached { try JSONSerialization.data(withJSONObject: db.alerts(query)) }.value
            try Task.checkCancellation()
            let value=try JSONSerialization.jsonObject(with:data) as! [String:Any]
            unreadCount=value["unread_count"] as? Int ?? 0;nextOffset=value["next_offset"] as? Int
            subscriptions=(value["subscriptions"] as? [[String:Any]] ?? []).map {
                AlertSubscription(name:$0["name"] as? String ?? "",version:$0["version"] as? Int ?? 0,
                                  enabled:$0["enabled"] as? Bool ?? false,filters:$0["filters"] as? [String:String] ?? [:])
            }
            matches=(value["matches"] as? [[String:Any]] ?? []).map {
                AlertMatch(name:$0["name"] as? String ?? "",storyID:$0["story_id"] as? String ?? "",title:$0["title"] as? String ?? "",
                           seq:$0["seq"] as? Int ?? 0,revision:$0["matched_revision"] as? Int ?? 0,current:$0["current_revision"] as? Int ?? 0,
                           subscriptionVersion:$0["subscription_version"] as? Int ?? 0,unread:$0["unread"] as? Bool ?? false,merged:$0["merged_into"] as? String)
            }
        } catch is CancellationError {} catch { message="Could not load saved-search alerts. Reload after the current write finishes." }
    }
    private func change(_ value:[String:Any]) {
        guard !busy else { return }
        busy=true
        Task {
            defer { busy=false }
            do {
                let data=try JSONSerialization.data(withJSONObject:value), db=store
                try await Task.detached {
                    let command=try JSONSerialization.jsonObject(with:data) as! [String:Any]
                    _=try db.alertChange(command)
                }.value
                message="";refresh += 1
            } catch LibraryFailure.conflict(let detail) { message=detail;refresh += 1 }
            catch { message="Could not save the change. Reload and try again." }
        }
    }
}
