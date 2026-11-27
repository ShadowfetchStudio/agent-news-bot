import Foundation
import Darwin

enum AppPaths {
    static let appName = "Agent News Bot"
    static var bundleId: String {
        if let home = ProcessInfo.processInfo.environment["AGENT_NEWSBOT_HOME"], !home.isEmpty {
            // Isolated app runs must not share user preferences or Keychain accounts with production.
            return "ai.shadowfetch.agentnewsbot.test." + String(home.utf8.reduce(UInt64(14695981039346656037)) { ($0 ^ UInt64($1)) &* 1099511628211 }, radix: 16)
        }
        return "ai.shadowfetch.agentnewsbot"
    }

    static var support: URL {
        let url: URL
        if let home = ProcessInfo.processInfo.environment["AGENT_NEWSBOT_HOME"], !home.isEmpty {
            url = URL(fileURLWithPath: home, isDirectory: true)
        } else {
            url = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("AgentNewsBot", isDirectory: true)
        }
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        return url
    }

    static var exports: URL {
        let url = support.appendingPathComponent("Exports", isDirectory: true)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        return url
    }

    static var seenDB: URL { support.appendingPathComponent("seen.sqlite") }
    static var tokenFile: URL { support.appendingPathComponent("bridge.token") }

    static func recoveryIncomplete(in folder: URL) -> Bool {
        ["INCOMPLETE.json", "RECOVERY_INCOMPLETE.json"].contains { marker in
            var info = stat()
            return lstat(folder.appendingPathComponent(marker).path, &info) == 0
        }
    }

    static func prepareDatabase(_ url: URL) throws {
        guard !recoveryIncomplete(in: url.deletingLastPathComponent()) else { throw CocoaError(.fileReadCorruptFile) }
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let fd = open(url.path, O_RDWR | O_CREAT | O_NOFOLLOW | O_NONBLOCK, mode_t(0o600))
        guard fd >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        defer { close(fd) }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFREG, fchmod(fd, mode_t(0o600)) == 0 else { throw CocoaError(.fileWriteNoPermission) }
    }
}
