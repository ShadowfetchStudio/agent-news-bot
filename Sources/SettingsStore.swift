import Foundation
import Security
import Combine

@MainActor
final class SettingsStore: ObservableObject {
    static let shared = SettingsStore()

    @Published var autoRefresh: Bool
    @Published var refreshMinutes: Int
    @Published var lookbackHours: Double
    @Published var maxPerDesk: Int
    @Published var weatherCity: String
    @Published var hermesAPIURL: String
    @Published var hermesUseCLI: Bool
    @Published var openClawURL: String
    @Published var bridgePort: Int
    @Published var enabledDesks: Set<Desk>
    @Published var worldMonitorEnabled: Bool
    @Published var worldMonitorURL: String
    @Published var worldMonitorVariant: String
    @Published var marketSymbols: String

    private let defaults = UserDefaults(suiteName: AppPaths.bundleId) ?? .standard

    private init() {
        autoRefresh = defaults.object(forKey: "autoRefresh") as? Bool ?? true
        refreshMinutes = min(120, max(5, defaults.object(forKey: "refreshMinutes") as? Int ?? 15))
        worldMonitorEnabled = defaults.bool(forKey: "worldMonitorEnabled")
        worldMonitorURL = defaults.string(forKey: "worldMonitorURL") ?? "http://127.0.0.1:3000"
        worldMonitorVariant = defaults.string(forKey: "worldMonitorVariant") ?? "full"
        lookbackHours = defaults.object(forKey: "lookbackHours") as? Double ?? 12
        maxPerDesk = defaults.object(forKey: "maxPerDesk") as? Int ?? 20
        weatherCity = defaults.string(forKey: "weatherCity") ?? "New York"
        hermesAPIURL = defaults.string(forKey: "hermesAPIURL") ?? "http://127.0.0.1:8642"
        hermesUseCLI = defaults.object(forKey: "hermesUseCLI") as? Bool ?? true
        openClawURL = defaults.string(forKey: "openClawURL") ?? "ws://127.0.0.1:18789"
        bridgePort = defaults.object(forKey: "bridgePort") as? Int ?? 18765
        marketSymbols = defaults.string(forKey: "marketSymbols")
            ?? "^GSPC,^DJI,^IXIC,SPY,QQQ,AAPL,MSFT,NVDA,GOOGL,AMZN,META,TSLA,BTC-USD,ETH-USD"
        if let raw = defaults.array(forKey: "enabledDesks") as? [String] {
            enabledDesks = Set(raw.compactMap(Desk.init(rawValue:)))
        } else {
            enabledDesks = Set(Desk.allCases)
        }
    }

    func save() {
        defaults.set(autoRefresh, forKey: "autoRefresh")
        defaults.set(refreshMinutes, forKey: "refreshMinutes")
        defaults.set(worldMonitorEnabled, forKey: "worldMonitorEnabled")
        defaults.set(worldMonitorURL, forKey: "worldMonitorURL")
        defaults.set(worldMonitorVariant, forKey: "worldMonitorVariant")
        defaults.set(lookbackHours, forKey: "lookbackHours")
        defaults.set(maxPerDesk, forKey: "maxPerDesk")
        defaults.set(weatherCity, forKey: "weatherCity")
        defaults.set(hermesAPIURL, forKey: "hermesAPIURL")
        defaults.set(hermesUseCLI, forKey: "hermesUseCLI")
        defaults.set(openClawURL, forKey: "openClawURL")
        defaults.set(bridgePort, forKey: "bridgePort")
        defaults.set(marketSymbols, forKey: "marketSymbols")
        defaults.set(enabledDesks.map(\.rawValue), forKey: "enabledDesks")
    }

    @Published var credentialError = ""

    var worldMonitorKey: String { readCredential("worldmonitor") }
    var hermesToken: String { readCredential("hermes-api") }
    var openClawToken: String { readCredential("openclaw") }

    private func readCredential(_ account: String) -> String {
        do { return try Keychain.get(account) ?? "" }
        catch { credentialError = error.localizedDescription; return "" }
    }
}

enum Keychain {
    struct Operations {
        var update: (CFDictionary, CFDictionary) -> OSStatus
        var add: (CFDictionary) -> OSStatus
        var delete: (CFDictionary) -> OSStatus
        var read: (CFDictionary) -> (OSStatus, Data?)
        static var system: Operations {
            Operations(update: { SecItemUpdate($0, $1) },
                add: { SecItemAdd($0, nil) }, delete: { SecItemDelete($0) },
                read: { query in
                    var out: AnyObject?
                    let status = SecItemCopyMatching(query, &out)
                    return (status, out as? Data)
                })
        }
    }

    struct Failure: LocalizedError {
        let status: OSStatus
        var errorDescription: String? {
            "Keychain operation failed: " + (SecCopyErrorMessageString(status, nil) as String? ?? String(status))
        }
    }

    static func set(_ value: String, account: String, using operations: Operations = .system) throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: AppPaths.bundleId,
            kSecAttrAccount as String: account
        ]
        if value.isEmpty {
            let status = operations.delete(query as CFDictionary)
            guard status == errSecSuccess || status == errSecItemNotFound else { throw Failure(status: status) }
            return
        }
        // Update in place. A failure never deletes the previous credential.
        let data = Data(value.utf8)
        let status = operations.update(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if status == errSecItemNotFound {
            var add = query
            add[kSecValueData as String] = data
            add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
            let added = operations.add(add as CFDictionary)
            guard added == errSecSuccess else { throw Failure(status: added) }
        } else if status != errSecSuccess { throw Failure(status: status) }
    }

    static func get(_ account: String, using operations: Operations = .system) throws -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: AppPaths.bundleId,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        let (status, data) = operations.read(query as CFDictionary)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else { throw Failure(status: status) }
        guard let data, let value = String(data: data, encoding: .utf8) else { throw Failure(status: errSecDecode) }
        return value
    }
}
