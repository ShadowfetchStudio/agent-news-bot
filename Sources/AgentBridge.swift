import Foundation
import Darwin

struct BridgeStatusSnapshot: Sendable {
    var fresh: Int
    var fetchedAt: String?
    var fetching: Bool
    var persistenceError: String? = nil
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
    var groupRepairHandler: (() -> String?)?

    var endpoint: String { "http://127.0.0.1:\(port)" }

    func replaceToken(_ token: String) {
        lifecycle.withLock { authToken = token }
    }

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
        let headerEnd = data.range(of: Data("\r\n\r\n".utf8))!.upperBound
        let headerText = String(data: data[..<headerEnd], encoding: .utf8) ?? ""
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
        } else if path == "/v1/library" || path.hasPrefix("/v1/library/") || path == "/v1/consumers" {
            var body: [String: Any] = [:]
            if method == "POST" {
                guard headers["transfer-encoding"] == nil, let length = Int(headers["content-length"] ?? ""), (0...65536).contains(length) else {
                    writeResponse(client, status: 400, body: Data("{\"error\":\"a JSON body with Content-Length up to 65536 is required\"}".utf8)); return
                }
                var bytes = Data(data[headerEnd...])
                while bytes.count < length {
                    guard ProcessInfo.processInfo.systemUptime < deadline else { writeResponse(client, status: 400, body: Data("{\"error\":\"incomplete request body\"}".utf8)); return }
                    let count = read(client, &buffer, min(buffer.count, length - bytes.count))
                    guard count > 0 else { writeResponse(client, status: 400, body: Data("{\"error\":\"incomplete request body\"}".utf8)); return }
                    bytes.append(buffer, count: count)
                }
                guard let parsed = try? JSONSerialization.jsonObject(with: bytes.prefix(length)) as? [String: Any] else {
                    writeResponse(client, status: 400, body: Data("{\"error\":\"JSON object required\"}".utf8)); return
                }
                body = parsed
            }
            response = path == "/v1/consumers" ? consumerResponse(method: method, body: body) : libraryResponse(path, method: method, query: Dictionary(queryItems.map { ($0.name, $0.value ?? "") }, uniquingKeysWith: { _, last in last }), body: body)
        } else if path.hasPrefix("/v1/stories/") {
            let parts = path.split(separator: "/").map(String.init)
            if method != "GET" { response = json(405, ["error": "method not allowed"]) }
            else if let stories, parts.count == 3 || (parts.count == 4 && parts[3] == "revisions") {
                do {
                    if let story = try stories.story(parts[2]) {
                        if parts.count == 4 {
                            guard let limit = Int(queryItems.first { $0.name == "limit" }?.value ?? "50") else { throw StoryStore.Failure.invalidLimit }
                            response = json(200, ["story_id": parts[2], "revisions": try stories.revisions(parts[2], limit: limit)])
                        } else { response = json(200, ["story": story]) }
                    } else { response = json(404, ["error": "story not found"]) }
                } catch StoryStore.Failure.invalidLimit { response = json(400, ["error": "limit must be 1-100"]) }
                catch { response = json(503, ["error": "story storage unavailable"]) }
            } else { response = json(404, ["error": "not found"]) }
        } else if path == "/v1/events" || path == "/v1/events/ack" {
            if method != (path == "/v1/events" ? "GET" : "POST") {
                response = json(405, ["error": "method not allowed"])
            } else if let stories {
                let consumer = queryItems.first { $0.name == "consumer" }?.value ?? ""
                do {
                    let allowed: Set<String> = path == "/v1/events" ? ["consumer", "limit"] : ["consumer", "through", "generation"]
                    guard Set(queryItems.map(\.name)).isSubset(of: allowed) else { throw ConsumerFailure.invalid("event filters are unsupported; process the complete offered range") }
                    if path == "/v1/events" {
                        guard let limit = Int(queryItems.first { $0.name == "limit" }?.value ?? "100") else { throw StoryStore.Failure.invalidLimit }
                        response = json(200, try stories.pending(consumer: consumer, limit: limit, lease: headers["x-consumer-lease"]))
                    } else {
                        guard let through = Int(queryItems.first { $0.name == "through" }?.value ?? "") else { throw StoryStore.Failure.invalidAcknowledgement }
                        let raw = queryItems.first { $0.name == "generation" }?.value
                        if let raw, Int(raw) == nil { throw ConsumerFailure.invalid("invalid generation") }
                        response = json(200, try stories.acknowledge(consumer: consumer, through: through, generation: raw.flatMap(Int.init), lease: headers["x-consumer-lease"]))
                    }
                } catch ConsumerFailure.conflict(let detail) { response = json(409, ["error": detail]) }
                catch ConsumerFailure.invalid(let detail) { response = json(400, ["error": detail]) }
                catch StoryStore.Failure.database { response = json(503, ["error": "story storage unavailable"]) }
                catch { response = json(400, ["error": "invalid consumer, limit, or acknowledgement"]) }
            } else { response = json(503, ["error": "story storage unavailable"]) }
        } else if path == "/v1/fresh" {
            if method != "GET" {
                response = json(405, ["error": "method not allowed"])
            } else {
                var items = articlesProvider()
                if let desk = queryItems.first(where: { $0.name == "desk" })?.value, !desk.isEmpty {
                    items = items.filter { $0.displayDesks.contains { $0.rawValue == desk } }
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
                let desks = desksProvider().map(\.wire)
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
                payload["displayed"] = articlesProvider().count
                payload["fetched_at"] = st.fetchedAt ?? NSNull()
                payload["snapshot_error"] = st.persistenceError ?? NSNull()
                payload["coverage"] = Coverage.summary(desksProvider().flatMap(\.coverageChecks))
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

    private func consumerResponse(method: String, body: [String: Any]) -> (Int, Data) {
        guard let stories else { return json(503, ["error": "story storage unavailable"]) }
        guard ["GET", "POST"].contains(method) else { return json(405, ["error": "method not allowed"]) }
        do { return json(200, try method == "GET" ? stories.consumerStatus() : stories.consumerChange(body)) }
        catch ConsumerFailure.conflict(let detail) { return json(409, ["error": detail]) }
        catch ConsumerFailure.invalid(let detail) { return json(400, ["error": detail]) }
        catch ConsumerFailure.missing { return json(404, ["error": "consumer not found"]) }
        catch StoryStore.Failure.invalidConsumer { return json(400, ["error": "invalid consumer or worker name"]) }
        catch { return json(503, ["error": "story storage unavailable"]) }
    }

    private func libraryResponse(_ path: String, method: String, query: [String: String], body: [String: Any]) -> (Int, Data) {
        guard let stories else { return json(503, ["error": "story storage unavailable"]) }
        let allowed = ["/v1/library", "/v1/library/settings", "/v1/library/editorial", "/v1/library/export", "/v1/library/repairs"]
        guard allowed.contains(path) else { return json(404, ["error": "not found"]) }
        guard method == "GET" || (method == "POST" && ["/v1/library/settings", "/v1/library/editorial", "/v1/library/repairs"].contains(path)) else { return json(405, ["error": "method not allowed"]) }
        do {
            switch path {
            case "/v1/library/repairs":
                if method == "POST" {
                    guard query.isEmpty else { throw LibraryFailure.invalid("repair POST does not accept query parameters") }
                    let receipt = try stories.repairGroup(body)
                    return json(200, ["repair": receipt, "snapshot_error": groupRepairHandler?() ?? NSNull()])
                }
                guard Set(query.keys) == ["operation_id"] else { throw LibraryFailure.invalid("operation_id is required") }
                guard let receipt = try stories.repairReceipt(query["operation_id"] ?? "") else { throw LibraryFailure.missing }
                return json(200, ["repair": receipt])
            case "/v1/library": return json(200, try stories.library(query))
            case "/v1/library/settings": return json(200, try stories.librarySettings(kind: body["kind"] as? String, name: body["name"] as? String, value: body["value"], write: method == "POST"))
            case "/v1/library/export": return json(200, try stories.researchExport(query["id"] ?? ""))
            default:
                if method == "POST" { return json(200, ["story": try stories.saveEditorial(query["id"] ?? "", change: body)]) }
                guard let story = try stories.editorial(query["id"] ?? "") else { throw LibraryFailure.missing }
                return json(200, ["story": story])
            }
        } catch LibraryFailure.missing { return json(404, ["error": "story not found"]) }
        catch LibraryFailure.invalid(let message) { return json(400, ["error": message]) }
        catch LibraryFailure.conflict(let message) { return json(409, ["error": message]) }
        catch { return json(503, ["error": "story storage unavailable"]) }
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
