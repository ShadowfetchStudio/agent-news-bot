import Foundation

/// Explicit installation only. Custom files are never replaced unless the user explicitly updates them.
enum SkillInstaller {
    static func install(_ text: String, at file: URL, overwrite: Bool = false) throws -> Bool {
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        if overwrite {
            try Data(text.utf8).write(to: file, options: .atomic)
            return true
        }
        do {
            try Data(text.utf8).write(to: file, options: .withoutOverwriting)
            return true
        } catch let error as CocoaError where error.code == .fileWriteFileExists {
            return false
        }
    }

    static func matches(_ text: String, at file: URL) -> Bool {
        guard let existing = try? String(contentsOf: file, encoding: .utf8) else { return false }
        return existing == text
    }
}
