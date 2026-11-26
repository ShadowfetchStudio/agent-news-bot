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

    var port: UInt16 = 18765
    var token: String = ""
    var articlesProvider: () -> [Article] = { [] }
    var desksProvider: () -> [DeskResult] = { [] }
    var statusProvider: () -> BridgeStatusSnapshot = { BridgeStatusSnapshot(fresh: 0, fetchedAt: nil, fetching: false) }
    var fetchHandler: (() async -> Int)?

    var endpoint: String { "http://127.0.0.1:\(port)" }

    @discardableResult
    func start(port: Int, token: String) -> Bool {
        stop()
        guard (1...65535).contains(port) else { return false }
        self.port = UInt16(port)
        self.token = token

        let sock = socket(AF_INET, SOCK_STREAM, 0)
        guard sock >= 0 else { return false }
        var yes: Int32 = 1
        setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &yes, socklen_t(MemoryLayout<Int32>.size))
        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = self.port.bigEndian
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
        fd = sock
        let src = DispatchSource.makeReadSource(fileDescriptor: sock, queue: queue)
        src.setEventHandler { [weak self] in
            self?.acceptOne()
        }
        src.setCancelHandler {
            close(sock)
        }
        source = src
        src.resume()
        return true
    }

    func stop() {
        source?.cancel()
        source = nil
        fd = -1
    }

    private func acceptOne() {
        var addr = sockaddr()
        var len: socklen_t = socklen_t(MemoryLayout<sockaddr>.size)
        let client = accept(fd, &addr, &len)
        guard client >= 0 else { return }
        var tv = timeval(tv_sec: 5, tv_usec: 0)
        setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        queue.async { [weak self] in
            self?.serve(client)
            close(client)
        }
    }

    private func serve(_ client: Int32) {
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while data.range(of: Data("\r\n\r\n".utf8)) == nil {
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
        let authorized = Self.constantTimeEqual(authHeader, "Bearer \(token)")
            || Self.constantTimeEqual(altHeader, token)

        let response: (Int, Data)
        if path == "/health" {
            response = method == "GET"
                ? json(200, ["ok": true, "app": "Agent News Bot", "bridge": endpoint])
                : json(405, ["error": "method not allowed"])
        } else if !authorized {
            response = json(401, ["error": "bearer token required"])
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
                response = json(200, payload)
            }
        } else if path == "/v1/fetch" {
            if method != "POST" {
                response = json(405, ["error": "method not allowed"])
            } else if let handler = fetchHandler {
                let sem = DispatchSemaphore(value: 0)
                var total = 0
                Task {
                    total = await handler()
                    sem.signal()
                }
                _ = sem.wait(timeout: .now() + 60)
                response = json(200, ["ok": true, "total_articles": total])
            } else {
                response = json(503, ["error": "fetch handler unavailable"])
            }
        } else {
            response = json(404, ["error": "not found"])
        }
        writeResponse(client, status: response.0, body: response.1)
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
        [
            "id": article.id,
            "desk": article.desk.rawValue,
            "category": article.desk.title,
            "title": article.title,
            "url": article.url,
            "source": article.source,
            "published": article.published.map { ISO8601DateFormatter().string(from: $0) } ?? "",
            "summary": article.summary,
            "tweet": article.tweet,
            "tweet_chars": article.tweetChars,
            "hashtags": article.hashtags
        ]
    }

    private func json(_ status: Int, _ object: [String: Any]) -> (Int, Data) {
        let data = (try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys])) ?? Data("{}".utf8)
        return (status, data)
    }

    private func writeResponse(_ client: Int32, status: Int, body: Data) {
        let reason: String
        switch status {
        case 200: reason = "OK"
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
                if n <= 0 { break }
                sent += n
            }
        }
    }
}
