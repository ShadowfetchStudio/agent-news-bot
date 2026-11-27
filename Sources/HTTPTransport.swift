import Foundation

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
        let session = URLSession(configuration: configuration, delegate: RedirectPolicy(allowed: redirects), delegateQueue: nil)
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
    private var count = 0
    init(allowed: Bool) { self.allowed = allowed }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        count += 1
        let downgrade = response.url?.scheme == "https" && request.url?.scheme != "https"
        guard allowed, count <= 5, !downgrade, let url = request.url,
              ["http", "https"].contains(url.scheme ?? ""), url.user == nil, url.password == nil else {
            completionHandler(nil); return
        }
        completionHandler(request)
    }
}
