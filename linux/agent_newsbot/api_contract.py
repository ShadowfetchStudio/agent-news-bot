"""Versioned local API discovery and backward-compatible error envelopes."""
API_VERSION = 1
CONTRACT_VERSION = 1
APP_VERSION = "1.2.0"
FEATURES = {name: 1 for name in (
    "async_fetch", "durable_stories", "source_observations", "story_revisions",
    "consumer_delivery", "consumer_generations", "consumer_leases", "research_library",
    "editorial_drafts", "research_export", "group_repairs", "worldmonitor_digest", "source_health", "platform_approval", "editorial_brief", "retained_history", "saved_search_alerts")}
ROUTES = {
    "/health": ["GET"], "/v1/capabilities": ["GET"], "/v1/status": ["GET"],
    "/v1/fresh": ["GET"], "/v1/articles/{id}": ["GET"], "/v1/desks": ["GET"], "/v1/sources": ["GET"],
    "/v1/fetch": ["POST"], "/v1/stories/{id}": ["GET"], "/v1/stories/{id}/revisions": ["GET"],
    "/v1/events": ["GET"], "/v1/events/ack": ["POST"], "/v1/consumers": ["GET", "POST"],
    "/v1/library": ["GET"], "/v1/library/settings": ["GET", "POST"], "/v1/library/alerts": ["GET", "POST"],
    "/v1/library/editorial": ["GET", "POST"], "/v1/library/export": ["GET"], "/v1/library/brief": ["GET"],
    "/v1/library/repairs": ["GET", "POST"],
}
ERRORS = {
    400: ("invalid_request", "correct_request"), 401: ("unauthorized", "reload_credentials"),
    403: ("forbidden", "check_permissions"), 404: ("not_found", "check_resource"),
    405: ("method_not_allowed", "check_capabilities"), 408: ("request_timeout", "inspect_state_before_retry"),
    409: ("conflict", "reload_state"), 413: ("payload_too_large", "reduce_size_and_inspect_state"),
    414: ("uri_too_long", "shorten_uri"), 429: ("rate_limited", "inspect_state_then_backoff"),
    431: ("headers_too_large", "reduce_headers"), 500: ("internal_error", "inspect_state_before_retry"),
    501: ("not_implemented", "check_capabilities"), 503: ("temporarily_unavailable", "inspect_state_then_backoff"),
    505: ("http_version_not_supported", "use_http_1_1"),
}


def error_payload(status, payload):
    result = dict(payload)
    if status >= 400:
        code, recovery = ERRORS.get(status, ("http_error", "inspect_state_before_retry"))
        result.setdefault("error", "Request failed")
        result["error_detail"] = {"schema_version": 1, "code": code, "http_status": status,
                                  "recovery": recovery, "automatic_retry_safe": False}
    return result


def capabilities():
    return {"api_version": API_VERSION, "contract_version": CONTRACT_VERSION, "app_version": APP_VERSION,
            "implementation": "python", "features": dict(FEATURES), "routes": dict(ROUTES),
            "authentication": {"scheme": "Bearer", "alternative_header": "X-Agent-Token", "public_routes": ["GET /health"]},
            "limits": {"json_request_bytes": 65536, "response_bytes": 16777216, "fresh_page": 500,
                       "event_page": 500, "library_page": 100, "revision_page": 100, "repair_evidence_bytes": 4194304,
                       "alert_subscriptions": 32, "alert_page": 100, "lease_min_seconds": 10, "lease_max_seconds": 300},
            "delivery": {"semantics": "at_least_once", "filtered_acknowledgement": False,
                         "commit_before_ack": True, "repair_retry": "same_operation_id_and_request"},
            "limitations": {"automatic_publishing": False, "embedded_worldmonitor_dashboard": False,
                            "source_fact_verification": False, "media_rights_clearance": False},
            "error_schema_version": 1,
            "errors": {str(status): {"code": pair[0], "recovery": pair[1]} for status, pair in ERRORS.items()}}


def allowed_methods(path):
    if path in ROUTES: return ROUTES[path]
    parts = path.strip("/").split("/")
    if len(parts) == 3 and parts[:2] in (["v1", "articles"], ["v1", "stories"]) and parts[2]: return ["GET"]
    if len(parts) == 4 and parts[:2] == ["v1", "stories"] and parts[2] and parts[3] == "revisions": return ["GET"]
    return None
