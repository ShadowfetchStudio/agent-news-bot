import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var engine: NewsEngine
    @ObservedObject private var settings = SettingsStore.shared
    @State private var hermesToken = ""
    @State private var openClawToken = ""
    @State private var worldMonitorKey = ""
    @State private var originalCredentials: [String: String] = [:]
    @State private var credentialMessage = ""
    @State private var confirmRotation = false

    var body: some View {
        Form {
            Section("Setup status") {
                Label(settings.setupCompleted ? "Source setup saved" : "First-run setup pending", systemImage: settings.setupCompleted ? "checkmark.circle" : "circle")
                Label(engine.bridgeReady ? "Local bridge listening" : "Bridge offline — see credential and port controls below", systemImage: engine.bridgeReady ? "checkmark.circle" : "exclamationmark.triangle")
                Text("Current coverage: \(engine.coverageState). Open Operations in the desk to inspect individual source results. Agent connections are optional and require their own tests.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section("Freshness") {
                Toggle("Refresh automatically while the app is running", isOn: $settings.autoRefresh)
                Stepper("Refresh every \(settings.refreshMinutes) minutes", value: $settings.refreshMinutes, in: 5...120, step: 5)
                    .disabled(!settings.autoRefresh)
                Stepper(value: $settings.lookbackHours, in: 2...48, step: 1) {
                    Text("Lookback \(Int(settings.lookbackHours)) hours")
                }
                Stepper(value: $settings.maxPerDesk, in: 5...50, step: 5) {
                    Text("Max \(settings.maxPerDesk) stories per desk")
                }
                Text("New stories, added sources and changed reports appear inside the lookback window. Markets appear only on a real move. Weather appears when the forecast changes.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Section("World Monitor (optional)") {
                Toggle("Enable World Monitor", isOn: $settings.worldMonitorEnabled)
                WorldMonitorConnectionView(base: $settings.worldMonitorURL, variant: $settings.worldMonitorVariant, key: $worldMonitorKey)
                Text("Connect your own server or a licensed hosted API. Existing news sources work independently. HTTP is accepted only on loopback; use HTTPS for remote servers.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section("Weather & markets") {
                TextField("Weather city", text: $settings.weatherCity)
                TextField("Yahoo symbols", text: $settings.marketSymbols, axis: .vertical)
                    .lineLimit(2...4)
            }
            Section("Hermes") {
                Toggle("Prefer `hermes chat` CLI", isOn: $settings.hermesUseCLI)
                TextField("API base URL", text: $settings.hermesAPIURL)
                TextField("API model name", text: $settings.hermesModel)
                SecureField("API bearer token (optional)", text: $hermesToken)
                Text("CLI detection and a compatible model list do not verify model access. Test reply sends a short fixed prompt using your saved credentials and may use paid model capacity. Save credential changes first.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Button("Test Hermes reply") { Task { await engine.testAgent(.hermes) } }.disabled(engine.agentTestRunning)
                Button("Check connections without a model reply") { Task { await engine.refreshAgents() } }.disabled(engine.agentTestRunning)
                Text(engine.hermes.detail).font(.caption).textSelection(.enabled)
            }
            Section("OpenClaw") {
                TextField("Gateway URL", text: $settings.openClawURL)
                SecureField("Gateway token (optional)", text: $openClawToken)
                Text("The app probes the gateway on loopback and can send via the `openclaw` CLI. Agents can also pull GET /v1/fresh from the local bridge.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Button("Test OpenClaw reply") { Task { await engine.testAgent(.openclaw) } }.disabled(engine.agentTestRunning)
                Text("Test reply uses the CLI's configured model and credentials and may use paid capacity. Gateway tokens do not configure the CLI.").font(.caption).foregroundStyle(.secondary)
                Text(engine.openclaw.detail).font(.caption).textSelection(.enabled)
            }
            Section("Agent bridge") {
                Stepper(value: $settings.bridgePort, in: 1025...64999, step: 1) {
                    Text("Listen 127.0.0.1:\(settings.bridgePort)")
                }
                LabeledContent("Bearer token") {
                    Text(engine.bridgeToken)
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                }
                Button("Install agent skills") { engine.installAgentSkills() }
                Button("Reload credential") { engine.reloadBridgeCredential() }
                Button("Rotate bridge credential…") { confirmRotation = true }.disabled(engine.bridgeToken.isEmpty)
                if !engine.bridgeCredentialError.isEmpty { Text(engine.bridgeCredentialError).foregroundStyle(.red).textSelection(.enabled) }
                Text("Loopback only. Install skills for Hermes and other agents explicitly. Existing skill files are preserved.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Section("Credentials") {
                Button("Save credential changes") { saveCredentials() }
                Text("Credentials are saved only with this button. Clearing a loaded field removes that credential. Closing Settings does not change saved credentials.")
                    .font(.caption).foregroundStyle(.secondary)
                if !credentialMessage.isEmpty { Text(credentialMessage).textSelection(.enabled) }
                if !settings.credentialError.isEmpty { Text(settings.credentialError).foregroundStyle(.red) }
            }
            Section("Desks") {
                ForEach(Desk.allCases) { desk in
                    Toggle(desk.title, isOn: Binding(
                        get: { settings.enabledDesks.contains(desk) },
                        set: { on in
                            if on { settings.enabledDesks.insert(desk) } else { settings.enabledDesks.remove(desk) }
                        }
                    ))
                }
            }
        }
        .formStyle(.grouped)
        .onChange(of: settings.hermesAPIURL) { engine.invalidateAgent(.hermes) }
        .onChange(of: settings.hermesModel) { engine.invalidateAgent(.hermes) }
        .onChange(of: settings.hermesUseCLI) { engine.invalidateAgent(.hermes) }
        .onChange(of: settings.openClawURL) { engine.invalidateAgent(.openclaw) }
        .confirmationDialog("Replace the bridge credential? Existing clients must reload the token file.", isPresented: $confirmRotation) {
            Button("Rotate credential", role: .destructive) { engine.rotateBridgeCredential() }
            Button("Cancel", role: .cancel) {}
        }
        .onAppear {
            originalCredentials = [:]
            settings.credentialError = ""
            for account in ["worldmonitor", "hermes-api", "openclaw"] {
                do { originalCredentials[account] = try Keychain.get(account) ?? "" }
                catch { settings.credentialError += "\(account): \(error.localizedDescription)\n" }
            }
            worldMonitorKey = originalCredentials["worldmonitor"] ?? ""
            hermesToken = originalCredentials["hermes-api"] ?? ""
            openClawToken = originalCredentials["openclaw"] ?? ""
        }
        .onDisappear {
            settings.save()
            engine.applyBridgeSettings()
            engine.configureAutomaticRefresh()
            Task { await engine.refreshAgents() }
        }
    }
    private func saveCredentials() {
        var messages: [String] = []
        for (account, value) in [("worldmonitor", worldMonitorKey), ("hermes-api", hermesToken), ("openclaw", openClawToken)] {
            guard let original = originalCredentials[account] else {
                messages.append("\(account): unchanged because the existing credential could not be read.")
                continue
            }
            guard value != original else { continue }
            do {
                try Keychain.set(value, account: account)
                if account == "hermes-api" { engine.invalidateAgent(.hermes) }
                if account == "openclaw" { engine.invalidateAgent(.openclaw) }
                originalCredentials[account] = value
                messages.append("\(account): saved.")
            } catch { messages.append("\(account): \(error.localizedDescription). Previous credential preserved if update failed.") }
        }
        credentialMessage = messages.isEmpty ? "No credential changes." : messages.joined(separator: "\n")
    }

}
