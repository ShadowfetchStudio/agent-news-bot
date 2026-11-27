import Foundation

enum APIContract {
    static let appVersion = "1.2.0"
    static let features = Dictionary(uniqueKeysWithValues: [
        "async_fetch", "durable_stories", "source_observations", "story_revisions", "consumer_delivery",
        "consumer_generations", "consumer_leases", "research_library", "editorial_drafts", "research_export",
        "group_repairs", "worldmonitor_digest", "source_health"].map { ($0, 1) })
    static let routes: [String: [String]] = [
        "/health": ["GET"], "/v1/capabilities": ["GET"], "/v1/status": ["GET"], "/v1/fresh": ["GET"],
        "/v1/articles/{id}": ["GET"], "/v1/desks": ["GET"], "/v1/sources": ["GET"], "/v1/fetch": ["POST"],
        "/v1/stories/{id}": ["GET"], "/v1/stories/{id}/revisions": ["GET"], "/v1/events": ["GET"],
        "/v1/events/ack": ["POST"], "/v1/consumers": ["GET", "POST"], "/v1/library": ["GET"],
        "/v1/library/settings": ["GET", "POST"], "/v1/library/editorial": ["GET", "POST"],
        "/v1/library/export": ["GET"], "/v1/library/repairs": ["GET", "POST"]]
    static let errors: [Int: (String, String)] = [
        400: ("invalid_request", "correct_request"), 401: ("unauthorized", "reload_credentials"),
        403: ("forbidden", "check_permissions"), 404: ("not_found", "check_resource"),
        405: ("method_not_allowed", "check_capabilities"), 408: ("request_timeout", "inspect_state_before_retry"),
        409: ("conflict", "reload_state"), 413: ("payload_too_large", "reduce_size_and_inspect_state"),
        414: ("uri_too_long", "shorten_uri"), 429: ("rate_limited", "inspect_state_then_backoff"),
        431: ("headers_too_large", "reduce_headers"), 500: ("internal_error", "inspect_state_before_retry"),
        501: ("not_implemented", "check_capabilities"), 503: ("temporarily_unavailable", "inspect_state_then_backoff"),
        505: ("http_version_not_supported", "use_http_1_1")]

    static func errorPayload(_ status: Int, _ payload: [String: Any]) -> [String: Any] {
        guard status >= 400 else { return payload }
        var result = payload
        let (code, recovery) = errors[status] ?? ("http_error", "inspect_state_before_retry")
        result["error"] = result["error"] ?? "Request failed"
        result["error_detail"] = ["schema_version": 1, "code": code, "http_status": status,
                                  "recovery": recovery, "automatic_retry_safe": false]
        return result
    }

    static func capabilities() -> [String: Any] {
        ["api_version": 1, "contract_version": 1, "app_version": appVersion, "implementation": "swift",
         "features": features, "routes": routes,
         "authentication": ["scheme": "Bearer", "alternative_header": "X-Agent-Token", "public_routes": ["GET /health"]],
         "limits": ["json_request_bytes": 65536, "response_bytes": 16777216, "fresh_page": 500,
                    "event_page": 500, "library_page": 100, "revision_page": 100, "repair_evidence_bytes": 4194304,
                    "lease_min_seconds": 10, "lease_max_seconds": 300],
         "delivery": ["semantics": "at_least_once", "filtered_acknowledgement": false,
                      "commit_before_ack": true, "repair_retry": "same_operation_id_and_request"],
         "limitations": ["automatic_publishing": false, "embedded_worldmonitor_dashboard": false,
                         "source_fact_verification": false, "media_rights_clearance": false],
         "error_schema_version": 1,
         "errors": Dictionary(uniqueKeysWithValues: errors.map { (String($0.key), ["code": $0.value.0, "recovery": $0.value.1]) })]
    }

    static func allowedMethods(_ path: String) -> [String]? {
        if let methods = routes[path] { return methods }
        let parts = path.split(separator: "/", omittingEmptySubsequences: false).map(String.init)
        if parts.count == 4, parts[0].isEmpty, parts[1] == "v1", ["articles", "stories"].contains(parts[2]), !parts[3].isEmpty { return ["GET"] }
        if parts.count == 5, parts[0].isEmpty, parts[1] == "v1", parts[2] == "stories", !parts[3].isEmpty, parts[4] == "revisions" { return ["GET"] }
        return nil
    }
}
