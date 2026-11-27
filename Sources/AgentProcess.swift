import Foundation
import Darwin

// One owned process group per explicit handoff. Never invoke a shell or signal an unrelated PID.
enum AgentProcess {
    enum Failure: LocalizedError {
        case launch(Int32), timedOut, outputLimit, exited(Int32, String)
        var errorDescription: String? {
            switch self {
            case .launch(let code): return "Could not start agent process (POSIX error \(code))."
            case .timedOut: return "Agent command exceeded its time limit and was stopped."
            case .outputLimit: return "Agent command exceeded the output limit and was stopped."
            case .exited(let code, let detail): return "Agent command exited \(code): \(detail)"
            }
        }
    }
    private final class Cancellation: @unchecked Sendable {
        private let lock = NSLock()
        private var value = false
        func cancel() { lock.withLock { value = true } }
        var cancelled: Bool { lock.withLock { value } }
    }

    static func run(path: String, arguments: [String], timeout: TimeInterval = 120, maxOutput: Int = 2 * 1024 * 1024) async throws -> String {
        let cancellation = Cancellation()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                DispatchQueue.global(qos: .userInitiated).async {
                    do { continuation.resume(returning: try execute(path: path, arguments: arguments, timeout: timeout, maxOutput: maxOutput, cancellation: cancellation)) }
                    catch { continuation.resume(throwing: error) }
                }
            }
        } onCancel: { cancellation.cancel() }
    }

    private static func execute(path: String, arguments: [String], timeout: TimeInterval, maxOutput: Int, cancellation: Cancellation) throws -> String {
        if cancellation.cancelled { throw CancellationError() }
        var output: [Int32] = [-1, -1], errors: [Int32] = [-1, -1]
        guard pipe(&output) == 0 else { throw Failure.launch(errno) }
        guard pipe(&errors) == 0 else { close(output[0]); close(output[1]); throw Failure.launch(errno) }
        defer { for fd in output + errors where fd >= 0 { close(fd) } }
        for fd in output + errors { _ = fcntl(fd, F_SETFD, FD_CLOEXEC) }
        _ = fcntl(output[0], F_SETFL, O_NONBLOCK)
        _ = fcntl(errors[0], F_SETFL, O_NONBLOCK)
        var actions: posix_spawn_file_actions_t?, attributes: posix_spawnattr_t?
        guard posix_spawn_file_actions_init(&actions) == 0, posix_spawnattr_init(&attributes) == 0 else { throw Failure.launch(ENOMEM) }
        defer { posix_spawn_file_actions_destroy(&actions); posix_spawnattr_destroy(&attributes) }
        posix_spawn_file_actions_addopen(&actions, STDIN_FILENO, "/dev/null", O_RDONLY, 0)
        posix_spawn_file_actions_adddup2(&actions, output[1], STDOUT_FILENO)
        posix_spawn_file_actions_adddup2(&actions, errors[1], STDERR_FILENO)
        for fd in output + errors { posix_spawn_file_actions_addclose(&actions, fd) }
        // The child becomes group leader atomically at spawn. Its descendants inherit that group.
        let groupCode = posix_spawnattr_setpgroup(&attributes, 0)
        guard groupCode == 0 else { throw Failure.launch(groupCode) }
        let flagsCode = posix_spawnattr_setflags(&attributes, Int16(POSIX_SPAWN_SETPGROUP))
        guard flagsCode == 0 else { throw Failure.launch(flagsCode) }
        let argv = ([path] + arguments).map { strdup($0) }
        let env = ProcessInfo.processInfo.environment.sorted(by: { $0.key < $1.key }).map { strdup($0.key + "=" + $0.value) }
        defer { for pointer in argv + env { free(pointer) } }
        var argvNull = argv + [nil], envNull = env + [nil]
        var pid: pid_t = 0
        let code = posix_spawn(&pid, path, &actions, &attributes, &argvNull, &envNull)
        guard code == 0 else { throw Failure.launch(code) }
        close(output[1]); output[1] = -1; close(errors[1]); errors[1] = -1
        var reaped = false, status: Int32 = 0
        defer {
            if !reaped {
                kill(-pid, SIGKILL)
                while waitpid(pid, &status, 0) < 0 && errno == EINTR {}
            }
        }
        let deadline = ProcessInfo.processInfo.systemUptime + max(0.01, timeout)
        var stdout = Data(), stderr = Data(), outEOF = false, errEOF = false
        var buffer = [UInt8](repeating: 0, count: 8192)
        while !reaped || !outEOF || !errEOF {
            if cancellation.cancelled { kill(-pid, SIGKILL); throw CancellationError() }
            if ProcessInfo.processInfo.systemUptime >= deadline { kill(-pid, SIGKILL); throw Failure.timedOut }
            for stream in 0...1 {
                if (stream == 0 && outEOF) || (stream == 1 && errEOF) { continue }
                let fd = stream == 0 ? output[0] : errors[0]
                // Bound each read pass so a continuous writer cannot starve timeout/cancellation checks.
                for _ in 0..<32 {
                    let count = read(fd, &buffer, buffer.count)
                    if count > 0 {
                        if stream == 0 { stdout.append(contentsOf: buffer.prefix(count)) }
                        else { stderr.append(contentsOf: buffer.prefix(count)) }
                        if stdout.count + stderr.count > maxOutput { kill(-pid, SIGKILL); throw Failure.outputLimit }
                    } else {
                        if count == 0 { if stream == 0 { outEOF = true } else { errEOF = true } }
                        else if errno != EAGAIN && errno != EINTR { throw Failure.launch(errno) }
                        break
                    }
                }
            }
            if !reaped {
                let result = waitpid(pid, &status, WNOHANG)
                if result == pid { reaped = true }
                else if result < 0 && errno != EINTR { throw Failure.launch(errno) }
            }
            if !reaped || !outEOF || !errEOF { usleep(10_000) }
        }
        let text = String(decoding: stdout, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
        let exitCode = status & 0x7f == 0 ? (status >> 8) & 0xff : 128 + (status & 0x7f)
        guard exitCode == 0 else { throw Failure.exited(exitCode, String(decoding: stderr.isEmpty ? stdout : stderr, as: UTF8.self).prefix(4000).description) }
        return text
    }
}
