import Foundation
import Darwin
import Security

// A failed read/write must never silently create a different live credential.
enum BridgeCredential {
    enum Failure: LocalizedError {
        case invalid, changed
        var errorDescription: String? {
            switch self {
            case .invalid: return "Bridge token must be an owned, regular private file containing 16–256 ASCII token characters. Existing data was preserved."
            case .changed: return "The saved bridge token changed. Reload it before rotating."
            }
        }
    }

    static func loadOrCreate(at path: URL) throws -> String {
        try locked(path) {
            if let existing = try read(path) { return existing }
            let token = try generate()
            try publish(token, at: path, replacing: false)
            return token
        }
    }

    static func rotate(at path: URL, expected: String) throws -> String {
        try locked(path) {
            guard try read(path) == expected else { throw Failure.changed }
            let token = try generate()
            try publish(token, at: path, replacing: true)
            return token
        }
    }

    private static func posix() -> Error { POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }

    private static func ownedFile(_ fd: Int32) throws {
        var info = stat()
        guard fstat(fd, &info) == 0 else { throw posix() }
        guard (info.st_mode & S_IFMT) == S_IFREG, info.st_uid == getuid() else { throw Failure.invalid }
        guard fchmod(fd, 0o600) == 0 else { throw posix() }
    }

    private static func locked<T>(_ path: URL, action: () throws -> T) throws -> T {
        try FileManager.default.createDirectory(at: path.deletingLastPathComponent(), withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let fd = open(path.path + ".lock", O_RDWR | O_CREAT | O_NOFOLLOW | O_NONBLOCK, mode_t(0o600))
        guard fd >= 0 else { throw posix() }
        defer { close(fd) }
        try ownedFile(fd)
        // Bounded lock acquisition; another process must not freeze the UI indefinitely.
        let deadline = ProcessInfo.processInfo.systemUptime + 2
        while flock(fd, LOCK_EX | LOCK_NB) != 0 {
            guard errno == EWOULDBLOCK, ProcessInfo.processInfo.systemUptime < deadline else { throw posix() }
            usleep(10_000)
        }
        defer { flock(fd, LOCK_UN) }
        return try action()
    }

    private static func read(_ path: URL) throws -> String? {
        let fd = open(path.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        guard fd >= 0 else {
            if errno == ENOENT { return nil }
            throw posix()
        }
        defer { close(fd) }
        try ownedFile(fd)
        var bytes = [UInt8](repeating: 0, count: 258)
        var count = 0
        try bytes.withUnsafeMutableBytes { buffer in
            while count < buffer.count {
                let received = Darwin.read(fd, buffer.baseAddress!.advanced(by: count), buffer.count - count)
                if received < 0 && errno == EINTR { continue }
                guard received >= 0 else { throw posix() }
                if received == 0 { break }
                count += received
            }
        }
        guard count < bytes.count, let raw = String(bytes: bytes.prefix(count), encoding: .utf8) else { throw Failure.invalid }
        let token = raw.trimmingCharacters(in: .newlines)
        guard (16...256).contains(token.utf8.count), token.utf8.allSatisfy({
            (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || [45, 46, 95, 126].contains($0)
        }) else { throw Failure.invalid }
        return token
    }

    private static func generate() throws -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else { throw Failure.invalid }
        return bytes.map { String(format: "%02x", $0) }.joined()
    }

    private static func publish(_ token: String, at path: URL, replacing: Bool) throws {
        let temporary = path.deletingLastPathComponent().appendingPathComponent(".bridge-token-" + UUID().uuidString)
        let fd = open(temporary.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, mode_t(0o600))
        guard fd >= 0 else { throw posix() }
        defer { close(fd); unlink(temporary.path) }
        let bytes = Array((token + "\n").utf8)
        try bytes.withUnsafeBytes { buffer in
            var offset = 0
            while offset < buffer.count {
                let wrote = Darwin.write(fd, buffer.baseAddress!.advanced(by: offset), buffer.count - offset)
                if wrote < 0 && errno == EINTR { continue }
                guard wrote > 0 else { throw posix() }
                offset += wrote
            }
        }
        guard fsync(fd) == 0 else { throw posix() }
        let result = replacing ? rename(temporary.path, path.path) : link(temporary.path, path.path)
        guard result == 0 else { throw posix() }
    }
}
