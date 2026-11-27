import Foundation
import Darwin

// Publish complete, private bytes. A failed latest commit leaves the previous
// snapshot intact. This is atomic replacement, not a power-loss durability claim.
enum SnapshotWriter {
    enum Failure: LocalizedError {
        case tooLarge, invalidDestination, io(Int32)
        var errorDescription: String? {
            switch self {
            case .tooLarge: return "Snapshot exceeds the 16 MiB restore limit. Reduce the displayed story count."
            case .invalidDestination: return "Snapshot destination must be a regular file, not a directory or symbolic link."
            case .io(let code): return "Snapshot storage failed: \(String(cString: strerror(code)))."
            }
        }
    }

    @discardableResult
    static func save(_ payload: [String: Any], in directory: URL,
                     commit: (String, String) throws -> Void = replace) throws -> URL {
        let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys])
        guard data.count <= 16 * 1024 * 1024 else { throw Failure.tooLarge }
        let latest = directory.appendingPathComponent("latest.json")
        var attributes = stat()
        if lstat(latest.path, &attributes) == 0 {
            guard attributes.st_mode & S_IFMT == S_IFREG else { throw Failure.invalidDestination }
        } else if errno != ENOENT { throw Failure.io(errno) }
        let identifier = UUID().uuidString
        let temporary = directory.appendingPathComponent(".snapshot-\(identifier)")
        let archive = directory.appendingPathComponent("agentnewsbot_\(Int(Date().timeIntervalSince1970))_\(identifier).json")
        let descriptor = open(temporary.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard descriptor >= 0 else { throw Failure.io(errno) }
        defer { close(descriptor); unlink(temporary.path) }
        try data.withUnsafeBytes { buffer in
            var offset = 0
            while offset < buffer.count {
                let count = Darwin.write(descriptor, buffer.baseAddress!.advanced(by: offset), buffer.count - offset)
                if count < 0 && errno == EINTR { continue }
                guard count > 0 else { throw Failure.io(count == 0 ? EIO : errno) }
                offset += count
            }
        }
        guard fsync(descriptor) == 0 else { throw Failure.io(errno) }
        // Exclusive archive publication cannot overwrite an earlier export.
        guard link(temporary.path, archive.path) == 0 else { throw Failure.io(errno) }
        do { try commit(temporary.path, latest.path) }
        catch { unlink(archive.path); throw error }
        return archive
    }

    static func replace(_ source: String, _ destination: String) throws {
        guard rename(source, destination) == 0 else { throw Failure.io(errno) }
    }
}
