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
                TextField("Server URL", text: $settings.worldMonitorURL)
                Picker("Edition", selection: $settings.worldMonitorVariant) {
                    ForEach(WorldMonitorClient.variants, id: \.self) { Text($0).tag($0) }
                }
                SecureField("API key (if required)", text: $worldMonitorKey)
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
                SecureField("API bearer token (optional)", text: $hermesToken)
                Text("Native send uses `hermes chat -Q` when the CLI is on PATH. Otherwise POST /v1/chat/completions. Local Hermes was detected at ~/.local/bin/hermes.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Section("OpenClaw") {
                TextField("Gateway URL", text: $settings.openClawURL)
                SecureField("Gateway token (optional)", text: $openClawToken)
                Text("The app probes the gateway on loopback and can send via the `openclaw` CLI. Agents can also pull GET /v1/fresh from the local bridge.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
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
                originalCredentials[account] = value
                messages.append("\(account): saved.")
            } catch { messages.append("\(account): \(error.localizedDescription). Previous credential preserved if update failed.") }
        }
        credentialMessage = messages.isEmpty ? "No credential changes." : messages.joined(separator: "\n")
    }

}
