import Foundation
import Darwin

enum HTTPTransport {
    struct Failure: LocalizedError {
        let status: Int
        var retryAfter: String? = nil
        var errorDescription: String? {
            switch status {
            case 401: return "Authentication required or rejected (401). Check the saved API token."
            case 403: return "Access denied (403). Check provider permissions."
            case 404: return "Endpoint or model not found (404). Check the API URL and model."
            case 429: return "Provider rate limit reached (429). Wait before retrying."
            case 300..<400: return "Redirect refused. Configure the final API URL."
            default: return "Provider returned HTTP \(status)."
            }
        }
    }

    static func receive(_ request: URLRequest, timeout: TimeInterval = 14,
                        maxBytes: Int = 4 * 1024 * 1024, redirects: Bool = true) async throws -> (Data, HTTPURLResponse) {
        try SourceAttempt.current?.check()
        do { return try await transfer(request, timeout: timeout, maxBytes: maxBytes, redirects: redirects) }
        catch {
            if !Task.isCancelled {
                if let failure = error as? Failure { SourceAttempt.current?.record("http_\(failure.status)", retryAfter: failure.retryAfter) }
                else { SourceAttempt.current?.record("transport") }
            }
            throw error
        }
    }

    private static func transfer(_ request: URLRequest, timeout: TimeInterval,
                                 maxBytes: Int, redirects: Bool) async throws -> (Data, HTTPURLResponse) {
        guard let url = request.url, ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
              url.host != nil, url.user == nil, url.password == nil else { throw URLError(.badURL) }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = max(0.1, timeout)
        configuration.timeoutIntervalForResource = max(0.1, timeout)
        let session = URLSession(configuration: configuration, delegate: RedirectPolicy(allowed: redirects, initialURL: url), delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        let (bytes, response) = try await session.bytes(for: request)
        guard let http = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        guard (200..<300).contains(http.statusCode) || http.statusCode == 304 else { throw Failure(status: http.statusCode, retryAfter: http.value(forHTTPHeaderField: "Retry-After")) }
        if http.statusCode == 304 { return (Data(), http) }
        // URLSession yields decoded bytes; compressed transfers cannot bypass the cap.
        var data = Data()
        for try await byte in bytes {
            guard data.count < maxBytes else { throw URLError(.dataLengthExceedsMaximum) }
            data.append(byte)
        }
        return (data, http)
    }
}

private final class RedirectPolicy: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    let allowed: Bool
    let initialLoopback: Bool
    private var count = 0
    init(allowed: Bool, initialURL: URL) {
        self.allowed = allowed
        self.initialLoopback = Self.isLoopbackHost(initialURL.host)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        count += 1
        let fromScheme = response.url?.scheme?.lowercased() ?? ""
        let toScheme = request.url?.scheme?.lowercased() ?? ""
        let downgrade = fromScheme == "https" && toScheme != "https"
        guard allowed, count <= 5, !downgrade, let url = request.url,
              ["http", "https"].contains(toScheme), let host = url.host, !host.isEmpty,
              url.user == nil, url.password == nil else {
            completionHandler(nil); return
        }
        // Block remote feeds from redirecting to loopback, private LAN, link-local, or cloud metadata IPs.
        if !initialLoopback && !Self.isPublicHost(host) {
            completionHandler(nil); return
        }
        var sanitized = request
        let crossOrigin = response.url?.scheme?.lowercased() != toScheme
            || response.url?.host?.lowercased() != host.lowercased()
            || response.url?.port != url.port
        if crossOrigin {
            sanitized.setValue(nil, forHTTPHeaderField: "Authorization")
            sanitized.setValue(nil, forHTTPHeaderField: "Cookie")
        }
        completionHandler(sanitized)
    }

    private static func isLoopbackHost(_ host: String?) -> Bool {
        guard let h = host?.lowercased() else { return false }
        return ["localhost", "127.0.0.1", "::1", "[::1]"].contains(h)
    }

    private static func isPublicHost(_ host: String) -> Bool {
        let trimmed = host.trimmingCharacters(in: CharacterSet(charactersIn: "[]")).lowercased()
        if trimmed == "localhost" || trimmed.hasSuffix(".local") || trimmed.hasSuffix(".internal") { return false }
        var hints = addrinfo()
        hints.ai_family = AF_UNSPEC
        hints.ai_socktype = SOCK_STREAM
        var result: UnsafeMutablePointer<addrinfo>?
        guard getaddrinfo(trimmed, nil, &hints, &result) == 0, let first = result else { return false }
        defer { freeaddrinfo(first) }
        var cursor: UnsafeMutablePointer<addrinfo>? = first
        var sawAny = false
        while let info = cursor {
            sawAny = true
            if info.pointee.ai_family == AF_INET, let sa = info.pointee.ai_addr {
                let addr = sa.withMemoryRebound(to: sockaddr_in.self, capacity: 1) { $0.pointee.sin_addr.s_addr }
                let ip = UInt32(bigEndian: addr)
                let a = (ip >> 24) & 0xFF, b = (ip >> 16) & 0xFF
                if a == 0 || a == 10 || a == 127 || (a == 100 && (b & 0xC0) == 64) ||
                   (a == 169 && b == 254) || (a == 172 && (b & 0xF0) == 16) ||
                   (a == 192 && b == 168) || a >= 224 { return false }
            } else if info.pointee.ai_family == AF_INET6, let sa = info.pointee.ai_addr {
                let bytes = withUnsafeBytes(of: sa.withMemoryRebound(to: sockaddr_in6.self, capacity: 1) { $0.pointee.sin6_addr }) { Array($0) }
                let isZero = bytes.allSatisfy { $0 == 0 }
                let isLoopback = bytes.prefix(15).allSatisfy { $0 == 0 } && bytes[15] == 1
                let isULA = (bytes[0] & 0xFE) == 0xFC
                let isLinkLocal = bytes[0] == 0xFE && (bytes[1] & 0xC0) == 0x80
                let isMulticast = bytes[0] == 0xFF
                let isV4Mapped = bytes.prefix(10).allSatisfy { $0 == 0 } && bytes[10] == 0xFF && bytes[11] == 0xFF
                if isZero || isLoopback || isULA || isLinkLocal || isMulticast { return false }
                if isV4Mapped {
                    let a = UInt32(bytes[12]), b = UInt32(bytes[13])
                    if a == 0 || a == 10 || a == 127 || (a == 169 && b == 254) ||
                       (a == 172 && (b & 0xF0) == 16) || (a == 192 && b == 168) || a >= 224 { return false }
                }
            }
            cursor = info.pointee.ai_next
        }
        return sawAny
    }
}
