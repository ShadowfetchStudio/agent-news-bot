import Foundation
import Darwin

struct BridgeStatusSnapshot: Sendable {
    var fresh: Int
    var fetchedAt: String?
    var fetching: Bool
}

final class AgentBridge: @unchecked Sendable {
    private var source: DispatchSourceRead?
    private var fd: Int32 = -1
    private let queue = DispatchQueue(label: "ai.shadowfetch.agentnewsbot.bridge", attributes: .concurrent)
    private let listenerQueue = DispatchQueue(label: "ai.shadowfetch.agentnewsbot.listener")
    private let lifecycle = NSLock()
    private let jobLock = NSLock()
    private let clientSlots = DispatchSemaphore(value: 16)
    private var job: [String: Any] = ["state": "idle"]
    private var listenPort: UInt16 = 18765
    private var authToken = ""

    var port: UInt16 { lifecycle.withLock { listenPort } }
    var isListening: Bool { lifecycle.withLock { fd >= 0 } }
    var articlesProvider: () -> [Article] = { [] }
    var desksProvider: () -> [DeskResult] = { [] }
    var statusProvider: () -> BridgeStatusSnapshot = { BridgeStatusSnapshot(fresh: 0, fetchedAt: nil, fetching: false) }
    var stories: StoryStore?
    var fetchHandler: (() async throws -> Int)?

    var endpoint: String { "http://127.0.0.1:\(port)" }

    @discardableResult
    func start(port: Int, token: String) -> Bool {
        guard (1...65535).contains(port), !token.isEmpty else { return false }
        lifecycle.lock()
        defer { lifecycle.unlock() }
        if fd >= 0 && listenPort == UInt16(port) {
            authToken = token
            return true
        }
        // Bind the replacement before retiring a working listener.

        let sock = socket(AF_INET, SOCK_STREAM, 0)
        guard sock >= 0 else { return false }
        var yes: Int32 = 1
        setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &yes, socklen_t(MemoryLayout<Int32>.size))
        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = UInt16(port).bigEndian
        addr.sin_addr = in_addr(s_addr: inet_addr("127.0.0.1"))
        let bindResult = withUnsafePointer(to: &addr) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(sock, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bindResult == 0, listen(sock, 32) == 0 else {
            close(sock)
            return false
        }
        _ = fcntl(sock, F_SETFL, O_NONBLOCK)
        stopLocked()
        fd = sock
        listenPort = UInt16(port)
        authToken = token
        let src = DispatchSource.makeReadSource(fileDescriptor: sock, queue: listenerQueue)
        src.setEventHandler { [weak self] in
            self?.acceptOne(listener: sock)
        }
        let closed = DispatchSemaphore(value: 0)
        listenerClosed = closed
        src.setCancelHandler {
            close(sock)
            closed.signal()
        }
        source = src
        src.resume()
        return true
    }

    private var listenerClosed: DispatchSemaphore?

    func stop() {
        lifecycle.withLock { stopLocked() }
    }

    // Only called under lifecycle; listener callbacks never acquire this lock.
    private func stopLocked() {
        source?.cancel()
        if let closed = listenerClosed { closed.wait() }
        source = nil
        listenerClosed = nil
        fd = -1
    }

    private func acceptOne(listener: Int32) {
        var addr = sockaddr()
        var len: socklen_t = socklen_t(MemoryLayout<sockaddr>.size)
        let client = accept(listener, &addr, &len)
        guard client >= 0 else { return }
        _ = fcntl(client, F_SETFL, 0)
        var yes: Int32 = 1
        setsockopt(client, SOL_SOCKET, SO_NOSIGPIPE, &yes, socklen_t(MemoryLayout<Int32>.size))
        var tv = timeval(tv_sec: 5, tv_usec: 0)
        setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        guard clientSlots.wait(timeout: .now()) == .success else { close(client); return }
        let slots = clientSlots
        queue.async { [weak self] in
            defer { close(client); slots.signal() }
            self?.serve(client)
        }
    }

    private func serve(_ client: Int32) {
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        let deadline = ProcessInfo.processInfo.systemUptime + 5
        while data.range(of: Data("\r\n\r\n".utf8)) == nil {
            guard ProcessInfo.processInfo.systemUptime < deadline else { return }
            let n = read(client, &buffer, buffer.count)
            if n <= 0 { return }
            data.append(buffer, count: n)
            if data.count > 64_000 { return }
        }
        let headerText = String(data: data, encoding: .utf8) ?? ""
        let lines = headerText.split(whereSeparator: \.isNewline).map(String.init)
        let requestLine = lines.first ?? ""
        let parts = requestLine.split(separator: " ")
        let method = parts.first.map { String($0).uppercased() } ?? "GET"
        let rawTarget = parts.dropFirst().first.map(String.init) ?? "/"
        let components = URLComponents(string: "http://127.0.0.1" + rawTarget)
        let path = components?.path ?? rawTarget
        let queryItems = components?.queryItems ?? []

        var headers: [String: String] = [:]
        for line in lines.dropFirst() {
            if let idx = line.firstIndex(of: ":") {
                let key = String(line[..<idx]).trimmingCharacters(in: .whitespaces).lowercased()
                let value = String(line[line.index(after: idx)...]).trimmingCharacters(in: .whitespacesAndNewlines)
                headers[key] = value
            }
        }

        let authHeader = headers["authorization"] ?? ""
        let altHeader = headers["x-agent-token"] ?? ""
        let token = lifecycle.withLock { authToken }
        let authorized = Self.constantTimeEqual(authHeader, "Bearer \(token)")
            || Self.constantTimeEqual(altHeader, token)

        let response: (Int, Data)
        if path == "/health" {
            response = method == "GET"
                ? json(200, ["ok": true, "app": "Agent News Bot", "bridge": endpoint])
                : json(405, ["error": "method not allowed"])
        } else if !authorized {
            response = json(401, ["error": "bearer token required"])
        } else if path == "/v1/events" || path == "/v1/events/ack" {
            if method != (path == "/v1/events" ? "GET" : "POST") {
                response = json(405, ["error": "method not allowed"])
            } else if let stories {
                let consumer = queryItems.first { $0.name == "consumer" }?.value ?? ""
                do {
                    if path == "/v1/events" {
                        guard let limit = Int(queryItems.first { $0.name == "limit" }?.value ?? "100") else { throw StoryStore.Failure.invalidLimit }
                        response = json(200, try stories.pending(consumer: consumer, limit: limit))
                    } else {
                        guard let through = Int(queryItems.first { $0.name == "through" }?.value ?? "") else { throw StoryStore.Failure.invalidAcknowledgement }
                        response = json(200, try stories.acknowledge(consumer: consumer, through: through))
                    }
                } catch StoryStore.Failure.database { response = json(503, ["error": "story storage unavailable"]) }
                catch { response = json(400, ["error": "invalid consumer, limit, or acknowledgement"]) }
            } else { response = json(503, ["error": "story storage unavailable"]) }
        } else if path == "/v1/fresh" {
            if method != "GET" {
                response = json(405, ["error": "method not allowed"])
            } else {
                var items = articlesProvider()
                if let desk = queryItems.first(where: { $0.name == "desk" })?.value, !desk.isEmpty {
                    items = items.filter { $0.desk.rawValue == desk }
                }
                if let rawLimit = queryItems.first(where: { $0.name == "limit" })?.value, !rawLimit.isEmpty {
                    guard let parsed = Int(rawLimit), parsed >= 0 else {
                        writeResponse(client, status: 400, body: json(400, ["error": "invalid limit parameter"]).1)
                        return
                    }
                    let clamped = min(parsed, 500)
                    items = Array(items.prefix(clamped))
                } else {
                    items = Array(items.prefix(100))
                }
                response = json(200, ["count": items.count, "articles": items.map(encode)])
            }
        } else if path.hasPrefix("/v1/articles/") {
            if method != "GET" {
                response = json(405, ["error": "method not allowed"])
            } else {
                let targetId = String(path.dropFirst("/v1/articles/".count))
                if let found = articlesProvider().first(where: { $0.id == targetId }) {
                    response = json(200, ["article": encode(found)])
                } else {
                    response = json(404, ["error": "article not found"])
                }
            }
        } else if path == "/v1/desks" {
            if method != "GET" {
                response = json(405, ["error": "method not allowed"])
            } else {
                let desks = desksProvider().map { desk -> [String: Any] in
                    [
                        "desk": desk.desk.rawValue,
                        "title": desk.desk.title,
                        "fresh": desk.articles.count,
                        "skipped_old": desk.skippedOld,
                        "skipped_dup": desk.skippedDup,
                        "errors": desk.sourceErrors
                    ]
                }
                response = json(200, ["desks": desks])
            }
        } else if path == "/v1/status" {
            if method != "GET" {
                response = json(405, ["error": "method not allowed"])
            } else {
                let st = statusProvider()
                var payload: [String: Any] = [
                    "ok": true,
                    "fresh": st.fresh,
                    "fetching": st.fetching
                ]
                payload["fetched_at"] = st.fetchedAt ?? NSNull()
                let currentJob = jobLock.withLock { job }
                payload["fetch_job"] = currentJob
                payload["fetching"] = st.fetching || currentJob["state"] as? String == "running"
                response = json(200, payload)
            }
        } else if path == "/v1/fetch" {
            if method != "POST" {
                response = json(405, ["error": "method not allowed"])
            } else if let handler = fetchHandler {
                let alreadyFetching = statusProvider().fetching
                jobLock.lock()
                if job["state"] as? String == "running" || alreadyFetching {
                    let existing = job
                    jobLock.unlock()
                    response = json(409, ["error": "fetch already running", "fetching": true, "fetch_job": existing])
                } else {
                    let id = UUID().uuidString.lowercased()
                    job = ["id": id, "state": "running", "started_at": ISO8601DateFormatter().string(from: Date())]
                    let accepted = job
                    jobLock.unlock()
                    Task {
                        do {
                            let total = try await handler()
                            self.finishJob(total: total, error: nil)
                        } catch {
                            self.finishJob(total: nil, error: "fetch failed; inspect source and storage status")
                        }
                    }
                    response = json(202, ["accepted": true, "fetching": true, "fetch_job": accepted, "status_url": "/v1/status"])
                }
            } else {
                response = json(503, ["error": "fetch handler unavailable"])
            }
        } else {
            response = json(404, ["error": "not found"])
        }
        writeResponse(client, status: response.0, body: response.1)
    }

    private func finishJob(total: Int?, error: String?) {
        jobLock.withLock {
            job["state"] = error == nil ? "completed" : "failed"
            job["finished_at"] = ISO8601DateFormatter().string(from: Date())
            if let total { job["total_articles"] = total }
            if let error { job["error"] = error }
        }
    }

    private static func constantTimeEqual(_ a: String, _ b: String) -> Bool {
        let ab = Array(a.utf8)
        let bb = Array(b.utf8)
        guard ab.count == bb.count, !ab.isEmpty else { return false }
        var diff: UInt8 = 0
        for i in 0..<ab.count {
            diff |= ab[i] ^ bb[i]
        }
        return diff == 0
    }

    private func encode(_ article: Article) -> [String: Any] {
        article.wire
    }

    private func json(_ status: Int, _ object: [String: Any]) -> (Int, Data) {
        let data = (try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys])) ?? Data("{}".utf8)
        return (status, data)
    }

    private func writeResponse(_ client: Int32, status: Int, body: Data) {
        // Reject oversized responses before writing them to a client.
        if body.count > 16 * 1024 * 1024 {
            writeResponse(client, status: 413, body: Data("{\"error\":\"response too large; reduce limit\"}".utf8))
            return
        }
        let reason: String
        switch status {
        case 200: reason = "OK"
        case 202: reason = "Accepted"
        case 409: reason = "Conflict"
        case 413: reason = "Content Too Large"
        case 400: reason = "Bad Request"
        case 401: reason = "Unauthorized"
        case 404: reason = "Not Found"
        case 405: reason = "Method Not Allowed"
        case 503: reason = "Service Unavailable"
        default: reason = "Error"
        }
        var header = "HTTP/1.1 \(status) \(reason)\r\n"
        header += "Content-Type: application/json\r\n"
        header += "Content-Length: \(body.count)\r\n"
        header += "Connection: close\r\n\r\n"
        var payload = Data(header.utf8)
        payload.append(body)
        payload.withUnsafeBytes { raw in
            guard let base = raw.bindMemory(to: UInt8.self).baseAddress else { return }
            var sent = 0
            while sent < payload.count {
                let n = write(client, base + sent, payload.count - sent)
                if n < 0 && errno == EINTR { continue }
                if n <= 0 { break }
                sent += n
            }
        }
    }
}
