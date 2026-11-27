"""Official twitter-text parser, shared byte-for-byte with the native Mac app.

Only the packaged, pinned JavaScript executes. User text is a function argument.
Each worker owns its own bounded QuickJS context; no Node service or network access.
"""
from __future__ import annotations

import json
import threading
from importlib.resources import files

import quickjs

_local = threading.local()


def request(value: dict) -> dict:
    context = getattr(_local, "context", None)
    if context is None:
        context = quickjs.Context()
        context.set_memory_limit(64 * 1024 * 1024)
        context.set_max_stack_size(1024 * 1024)
        context.set_time_limit(2)
        context.eval(files("agent_newsbot").joinpath("vendor/twitter-text.js").read_text())
        _local.context = context
        _local.request = context.eval("NewsBotXText.request")
    return json.loads(_local.request(json.dumps(value, ensure_ascii=True)))


def measure(text: str) -> dict:
    return request({"op": "measure", "text": text})


def compose(title: str, url: str, tags: list[str]) -> tuple[str, int]:
    result = request({"op": "compose", "title": title[:16384], "url": url[:8192], "tags": [t[:1024] for t in tags[:6]]})
    if result["text"] and not result["valid"]:
        raise ValueError("X draft failed validation")
    return result["text"], result["weightedLength"]
