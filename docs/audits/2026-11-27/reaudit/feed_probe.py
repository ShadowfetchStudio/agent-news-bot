"""Read-only bounded feed sample; invoke in a subprocess with a 60-second deadline."""
import concurrent.futures
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
import feedparser
import requests

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "linux"))
from agent_newsbot.config import RSS_FEEDS, USER_AGENT


def check(item):
    url, label = item
    result = {"url": url, "source": label, "checked_at": datetime.now(timezone.utc).isoformat()}
    try:
        with requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=(4, 8), stream=True) as response:
            result["http"] = response.status_code
            if response.status_code == 200:
                body = bytearray()
                for part in response.iter_content(65536):
                    body.extend(part)
                    if len(body) >= 524288: break
                parsed = feedparser.parse(bytes(body))
                result.update(entries=len(parsed.entries), format=parsed.version, truncated=len(body) >= 524288)
    except requests.RequestException as exc:
        result["error"] = type(exc).__name__
    return result


urls = {url: label for feeds in RSS_FEEDS.values() for url, label in feeds}
results = []
dest = Path(__file__).with_name("feed-health.json")
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
    for future in concurrent.futures.as_completed([pool.submit(check, item) for item in urls.items()]):
        results.append(future.result())
        dest.write_text(json.dumps({"expected": len(urls), "completed": len(results), "sources": results}, indent=2) + "\n")
print(json.dumps({"checked": len(results), "ok_with_entries": sum(r.get("http") == 200 and bool(r.get("entries")) and bool(r.get("format")) for r in results),
    "other": [r for r in results if not r.get("entries") or r.get("http") != 200]}, indent=2))
