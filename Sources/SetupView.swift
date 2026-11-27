import SwiftUI

/// Tests the currently entered values explicitly; does not save credentials or ingest stories.
struct WorldMonitorConnectionView: View {
    @Binding var base: String
    @Binding var variant: String
    @Binding var key: String
    @State private var result: WorldMonitorClient.Diagnostic?
    @State private var checkedAt: Date?
    @State private var running = false
    @State private var testTask: Task<Void, Never>?
    @State private var generation = UUID()

    var body: some View {
        TextField("Server origin", text: $base)
        Picker("Edition", selection: $variant) {
            ForEach(WorldMonitorClient.variants, id: \.self) { Text($0).tag($0) }
        }
        SecureField("API key (if required)", text: $key)
        Button(running ? "Testing connection…" : "Test World Monitor connection") { test() }
            .disabled(running)
            // Configuration changes immediately invalidate even an in-flight result.
            .onChange(of: base) { invalidate() }
            .onChange(of: variant) { invalidate() }
            .onChange(of: key) { invalidate() }
            .onDisappear { invalidate() }
        Text("Test sends the entered key to this server and reads one digest. It does not save the key or add stories. The server may count this against your API quota.")
            .font(.caption).foregroundStyle(.secondary)
        if let result {
            Label(result.message, systemImage: result.succeeded ? "checkmark.circle" : "exclamationmark.triangle")
                .font(.caption).textSelection(.enabled)
            if let checkedAt {
                Text("Checked \(checkedAt.formatted(date: .abbreviated, time: .standard)) · this configuration only")
                    .font(.caption2).foregroundStyle(.secondary)
            }
        }

    }

    private func invalidate() {
        generation = UUID(); testTask?.cancel(); testTask = nil
        result = nil; checkedAt = nil; running = false
    }
    private func test() {
        invalidate()
        let current = generation, origin = base, edition = variant, credential = key
        running = true
        testTask = Task {
            let value = await WorldMonitorClient.test(base: origin, key: credential, variant: edition)
            guard !Task.isCancelled, generation == current, origin == base, edition == variant, credential == key else { return }
            result = value; checkedAt = Date(); running = false
        }
    }
}

struct SetupView: View {
    @EnvironmentObject private var engine: NewsEngine
    @ObservedObject private var settings = SettingsStore.shared
    @State private var mode = "direct"
    @State private var key = ""
    @State private var originalKey: String?
    @State private var error = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Set up your news desk").font(.title2.bold())
            Text("Choose where stories come from. You can change these settings later.")
            Form {
                Section("Sources") {
                    Picker("Connection mode", selection: $mode) {
                        Text("Direct sources — no World Monitor account").tag("direct")
                        Text("Direct sources + my World Monitor server").tag("selfhosted")
                        Text("Direct sources + authorized hosted API").tag("hosted")
                    }
                    Text("Direct sources remain enabled in every mode. This app connects to a World Monitor news digest; it does not deploy a server or include its map dashboard.")
                        .font(.caption).foregroundStyle(.secondary)
                    if mode != "direct" {
                        WorldMonitorConnectionView(base: $settings.worldMonitorURL, variant: $settings.worldMonitorVariant, key: $key)
                        Text(mode == "hosted" ? "Use an API you have permission to access. A public dashboard URL is not necessarily an API subscription." : "Start your own compatible server first. Remote servers require HTTPS; HTTP is allowed only on loopback.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                Section("Desks and refresh") {
                    ForEach(Desk.allCases) { desk in
                        Toggle(desk.title, isOn: Binding(get: { settings.enabledDesks.contains(desk) }, set: { on in
                            if on { settings.enabledDesks.insert(desk) } else { settings.enabledDesks.remove(desk) }
                        }))
                    }
                    Toggle("Refresh automatically while running", isOn: $settings.autoRefresh)
                    Text("Automatic refresh starts after setup and repeats every \(settings.refreshMinutes) minutes. You can also use Fetch Fresh manually.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }.formStyle(.grouped)
            if !error.isEmpty { Text(error).font(.caption).foregroundStyle(.red).textSelection(.enabled) }
            Text("Agent connections are optional. Configure them in Settings after opening the desk. Nothing is posted to social media.")
                .font(.caption).foregroundStyle(.secondary)
            HStack { Spacer(); Button("Save and open desk", action: finish).keyboardShortcut(.defaultAction) }
        }.padding(20).frame(width: 590, height: 650)
        .onAppear {
            mode = settings.worldMonitorEnabled ? "selfhosted" : "direct"
            do { originalKey = try Keychain.get("worldmonitor") ?? ""; key = originalKey ?? "" }
            catch { self.error = "Existing World Monitor key could not be read. It will be preserved. Direct sources remain available." }
        }
    }
    private func finish() {
        guard !settings.enabledDesks.isEmpty else { error = "Choose at least one desk."; return }
        if mode != "direct" {
            do {
                _ = try WorldMonitorClient.origin(settings.worldMonitorURL)
                guard WorldMonitorClient.variants.contains(settings.worldMonitorVariant),
                      settings.worldMonitorURL.utf8.count <= 4096, key.utf8.count <= 8192,
                      key.unicodeScalars.allSatisfy({ $0.value >= 32 && $0.value <= 126 }) else { throw WorldMonitorClient.Failure.configuration }
                guard let originalKey else { error = "The existing key could not be read. Resolve Keychain access or choose direct sources."; return }
                if key != originalKey { try Keychain.set(key, account: "worldmonitor") }
            } catch {
                self.error = error is WorldMonitorClient.Failure ? WorldMonitorClient.diagnosis(error).message : error.localizedDescription
                return
            }
        }
        settings.worldMonitorEnabled = mode != "direct"
        settings.setupCompleted = true
        settings.save()
        if let desk = engine.selectedDesk, !settings.enabledDesks.contains(desk) { engine.selectedDesk = nil }
        engine.configureAutomaticRefresh()
        Task { await engine.refreshAgents() }
    }
}
