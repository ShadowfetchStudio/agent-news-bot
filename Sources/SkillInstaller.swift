import Foundation

/// Explicit installation only. Custom files are never replaced, even during an upgrade.
enum SkillInstaller {
    static func install(_ text: String, at file: URL) throws -> Bool {
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        do {
            try Data(text.utf8).write(to: file, options: .withoutOverwriting)
            return true
        } catch let error as CocoaError where error.code == .fileWriteFileExists {
            return false
        }
    }
}
