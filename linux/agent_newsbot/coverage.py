"""Explicit source observations. Successful transport/parsing is not factual verification."""
from datetime import datetime, timezone


def check(source: str, state: str = "success", detail: str = "", provider: str = "direct") -> dict:
    return {"source": source, "provider": provider, "state": state, "detail": detail,
            "checked_at": datetime.now(timezone.utc).isoformat()}


def summary(checks: list[dict]) -> dict:
    unique = {(c["provider"], c["source"]): c for c in checks}
    rows = [unique[key] for key in sorted(unique)]
    successful = sum(row["state"] == "success" for row in rows)
    if not rows:
        state = "unknown"
    elif successful == len(rows):
        state = "success"
    elif successful or any(r["state"] == "partial" for r in rows):
        state = "partial"
    elif any(r["state"] == "stale" for r in rows):
        state = "stale"
    else:
        state = "unavailable"
    return {"state": state, "checked": len(rows), "successful": successful,
            "degraded": len(rows) - successful, "sources": rows}


def result_checks(result) -> list[dict]:
    checks = list(result.source_checks)
    if result.source_errors and not any(c["state"] != "success" for c in checks):
        checks.append(check(result.desk + " warnings", "unavailable", "; ".join(result.source_errors)))
    return checks
