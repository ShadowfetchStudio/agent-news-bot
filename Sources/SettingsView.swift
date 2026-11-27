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
    @State private var confirmSkillOverwrite = false
    @State private var showBridgeToken = false
    @State private var copiedToken = false
    private var credentialsDirty: Bool {
        (originalCredentials["worldmonitor"] != nil && worldMonitorKey != originalCredentials["worldmonitor"])
            || (originalCredentials["hermes-api"] != nil && hermesToken != originalCredentials["hermes-api"])
            || (originalCredentials["openclaw"] != nil && openClawToken != originalCredentials["openclaw"])
    }
    private var hermesTokenDirty: Bool {
        originalCredentials["hermes-api"] != nil && hermesToken != originalCredentials["hermes-api"]
    }

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
                Button("Test Hermes reply") { Task { await engine.testAgent(.hermes) } }.disabled(engine.agentTestRunning || hermesTokenDirty)
                if hermesTokenDirty {
                    Text("Save credential changes below before testing Hermes with the new token.")
                        .font(.caption).foregroundStyle(.orange)
                }
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
                HStack {
                    Text("Listen 127.0.0.1:")
                    TextField("Port", value: $settings.bridgePort, format: .number.grouping(.never))
                        .frame(width: 74)
                        .textFieldStyle(.roundedBorder)
                    Stepper("", value: $settings.bridgePort, in: 1025...64999, step: 1)
                        .labelsHidden()
                }
                LabeledContent("Bearer token") {
                    HStack(spacing: 6) {
                        if showBridgeToken {
                            Text(engine.bridgeToken)
                                .font(.system(.caption, design: .monospaced))
                                .textSelection(.enabled)
                        } else {
                            Text(engine.bridgeToken.isEmpty ? "Unavailable" : String(repeating: "•", count: 16) + String(engine.bridgeToken.suffix(6)))
                                .font(.system(.caption, design: .monospaced))
                        }
                        Button(showBridgeToken ? "Hide" : "Reveal") { showBridgeToken.toggle() }
                            .disabled(engine.bridgeToken.isEmpty)
                        Button(copiedToken ? "Copied" : "Copy") {
                            NSPasteboard.general.clearContents()
                            NSPasteboard.general.setString(engine.bridgeToken, forType: .string)
                            copiedToken = true
                            Task {
                                try? await Task.sleep(nanoseconds: 1_500_000_000)
                                copiedToken = false
                            }
                        }
                        .disabled(engine.bridgeToken.isEmpty)
                    }
                }
                HStack {
                    Button("Install agent skills") { engine.installAgentSkills(overwrite: false) }
                    Button("Update existing skills…") { confirmSkillOverwrite = true }
                }
                if !engine.skillInstallMessage.isEmpty {
                    Text(engine.skillInstallMessage).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                }
                HStack {
                    Button("Reload credential") { engine.reloadBridgeCredential() }
                    Button("Rotate bridge credential…") { confirmRotation = true }.disabled(engine.bridgeToken.isEmpty)
                }
                if !engine.bridgeCredentialError.isEmpty { Text(engine.bridgeCredentialError).foregroundStyle(.red).textSelection(.enabled) }
                Text("Loopback only. Install creates missing skill files; Update replaces existing files with the current \(APIContract.appVersion) contract.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Section("Credentials") {
                if credentialsDirty {
                    Label("You have unsaved credential changes.", systemImage: "exclamationmark.triangle.fill")
                        .font(.caption.bold()).foregroundStyle(.orange)
                }
                Button("Save credential changes") { saveCredentials() }
                    .buttonStyle(.borderedProminent)
                    .disabled(!credentialsDirty)
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
                            if on { settings.enabledDesks.insert(desk) }
                            else if settings.enabledDesks.count > 1 { settings.enabledDesks.remove(desk) }
                        }
                    ))
                    .disabled(settings.enabledDesks.count == 1 && settings.enabledDesks.contains(desk))
                }
                Text("At least one desk must remain enabled.").font(.caption).foregroundStyle(.secondary)
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
        .confirmationDialog("Replace existing agent SKILL.md files with the current \(APIContract.appVersion) version?", isPresented: $confirmSkillOverwrite) {
            Button("Update skills", role: .destructive) { engine.installAgentSkills(overwrite: true) }
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
