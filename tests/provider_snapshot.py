"""Resolved provider behavior, dumped as plain data, so moving provider settings between code and profiles can be
checked to change nothing. Run directly to (re)write the fixture: python3 tests/provider_snapshot.py --write"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests/fixtures/provider_snapshot.json"
KEYS = ("OPENROUTER_API_KEY", "TYPESAFE_API_KEY", "OPENAI_API_KEY", "CLASSIFIER_COMPATIBLE_KEY")
CLEAN = {"CLASSIFIER_HOST": "other", "CLASSIFIER_ROUTE": "", "CLASSIFIER_PROVIDER": "", "CLASSIFIER_PROFILES": "",
         "OPENROUTER_DECISIONS_URL": "", "CLASSIFIER_COMPATIBLE_URL": "", "CLASSIFIER_COMPATIBLE_MODEL": "",
         "CODEX_THREAD_ID": "", **{k: "" for k in KEYS}}
URLS = ["https://openrouter.ai/api/alpha/decisions", "https://api.typesafe.ai/v1/systemone",
        "https://api.openai.com/v1/decisions", "http://127.0.0.1:11434/v1/systemone", "https://localhost:9/x",
        "http://localhost:8080/v1/systemone", "https://models.example.com/v1/systemone", "http://openrouter.ai/x",
        "https://evil.example.com/v1/systemone"]
SPEC_KEYS = ("name", "key", "model", "host", "endpoint", "explicit", "key_optional", "loopback_only", "shape",
             "url_env", "model_env")
MODELS = [("openrouter", "typesafe/jev-1.13"), ("openrouter", "openai/gpt-6-luna-decisions-20261006"),
          ("typesafe", "jev-1.13.0"), ("openai", "gpt-6-luna"), ("ollama", "nimble:9b"), ("ollama", "nimble:4b"),
          ("ollama", "tev1:4b"), ("compatible", "Winnow-12B")]


def load():
    spec = importlib.util.spec_from_file_location("jev_decide_snapshot", ROOT / "scripts/jev_decide.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def refused(fn) -> str | None:
    try:
        fn()
    except SystemExit as exc:
        return "exit" if exc.code is None else f"exit {exc.code}"
    return None


def largest(module, accept, low: int, high: int) -> int:
    """The largest n in [low, high] for which accept(n) is true, assuming acceptance is monotone; low - 1 if none."""
    if not accept(low):
        return low - 1
    while low < high:
        mid = (low + high + 1) // 2
        low, high = (mid, high) if accept(mid) else (low, mid - 1)
    return low


def limits(module, provider: str, model: str) -> dict:
    def ok(payload):
        try:
            if "state" in payload:
                module.check_size(payload)
            module.check_provider_limits(payload, provider)
        except SystemExit:
            return False
        return True

    def questions(n):
        return {f"q{i}": {"type": "noul", "instructions": "x"} for i in range(n)}

    def options(n):
        return {"q": {"type": "choice", "instructions": "x", "criteria": {f"o{i}": None for i in range(n)}}}

    base = {"model": model}
    out = {
        "questions": largest(module, lambda n: ok({**base, "state": "s", "questions": questions(n)}), 1, 200),
        "options": largest(module, lambda n: ok({**base, "state": "s", "questions": options(n)}), 1, 300),
        "state_ascii_chars": largest(module, lambda n: ok({**base, "state": "a" * n, "questions": questions(1)}),
                                     1, 400_000),
        "template_without_state_options": largest(module, lambda n: ok({**base, "questions": options(n)}), 1, 300),
    }
    text = {}
    for kind, question in {
        "instructions": {"type": "noul", "instructions": {"a": 1}},
        "choice": {"type": "choice", "instructions": "x", "criteria": {"a": {"b": 1}, "c": None}},
        "choice_null": {"type": "choice", "instructions": "x", "criteria": {"a": None, "c": None}},
        "noul": {"type": "noul", "instructions": "x", "criteria": {"true": {"b": 1}, "false": "f"}},
        "noul_null": {"type": "noul", "instructions": "x", "criteria": {"true": None, "false": "f"}},
        "score": {"type": "score", "instructions": "x", "criteria": [{"b": 1}, "high"]},
    }.items():
        payload = {**base, "state": "s", "questions": {"q": question}}
        text[kind] = refused(lambda: module.check_provider_limits(payload, provider))
    out["text_refused"] = text
    return out


def snapshot() -> dict:
    with mock.patch.dict(os.environ, CLEAN):
        module = load()
        out: dict = {"providers": {}, "select": {}, "route": {}, "limits": {}}
        for name in sorted(module.PROVIDERS):
            spec = module.PROVIDERS[name]
            entry = {key: spec.get(key) for key in SPEC_KEYS}
            entry["fields"] = sorted(spec["fields"]) if spec.get("fields") is not None else None
            env = {"CLASSIFIER_COMPATIBLE_URL": "https://models.example.com/v1/systemone",
                   "OPENROUTER_DECISIONS_URL": "https://openrouter.ai/api/v2/decisions"}
            with mock.patch.dict(os.environ, env):
                entry["endpoint_with_env"] = module.provider_endpoint(name)
                entry["accepts"] = {url: refused(lambda: module.check_endpoint(name, url)) is None for url in URLS}
            entry["endpoint_without_env"] = refused(lambda: module.provider_endpoint(name)) or \
                module.provider_endpoint(name)
            out["providers"][name] = entry
        for mask in range(1 << len(KEYS)):
            env = {key: ("k" if mask >> i & 1 else "") for i, key in enumerate(KEYS)}
            with mock.patch.dict(os.environ, env):
                label = ",".join(k for k in KEYS if env[k]) or "none"
                out["select"][label] = module.select_provider(None)
                for start in ("", "apikey", "openrouter"):
                    for dry in (False, True):
                        with mock.patch.dict(os.environ, {"CLASSIFIER_ROUTE": start}):
                            try:
                                plan = module.plan_route(None, None, dry)
                                result = {"route": plan["route"], "steps": [list(s) for s in plan["steps"]]}
                            except SystemExit as exc:
                                result = {"exit": exc.code}
                        out["route"][f"{label}|{start or '-'}|{'dry' if dry else 'send'}"] = result
        with mock.patch.dict(os.environ, {"CLASSIFIER_ROUTE": "chatgpt"}):
            out["route"]["bad_route"] = refused(lambda: module.plan_route(None))
        for provider, model in MODELS:
            out["limits"][f"{provider}|{model}"] = limits(module, provider, model)
        return out


if __name__ == "__main__":
    data = snapshot()
    if "--write" in sys.argv:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {FIXTURE}")
    else:
        print(json.dumps(data, indent=1, sort_keys=True))
