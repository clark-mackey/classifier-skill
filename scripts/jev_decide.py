#!/usr/bin/env python3
"""Call a typed decision model through OpenRouter, TypeSafe, OpenAI Decisions, Ollama, or a compatible System One API.

Every response is checked against the questions asked before it is printed; a malformed answer exits 3 (or, in
batch mode, marks that line "invalid") so callers never act on it. Every response also gains a top-level "review"
object: question id -> reasons a human should check the answer (fallback option chosen, close runner-up, noul near
0.5, low score confidence). Empty means no flags."""

from __future__ import annotations

import argparse
import datetime
import email.utils
import hashlib
import http.client
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlparse

# Providers and models live in profiles.json next to this script (loaded below, after `fail`), plus an optional user
# file named by CLASSIFIER_PROFILES that may only add ids starting with "user/". Provider selection that could move a
# key stays in code: only these providers are ever chosen automatically, in this order, when their key is set.
AUTO_PROVIDERS = ("openrouter", "typesafe")
# The route chain for OpenAI work, in order, as model profile ids. A run moves down only when a step's credit or plan
# is used up (`Exhausted`); a step whose credential is absent is skipped. Every step answers with Luna, so thresholds
# and caches stay valid across a move. A ChatGPT sign-in step would go first if OpenAI documents plan use for
# Decisions (context/plan-openai-provider-2026-10-08.md, Phase 3A).
ROUTE_PROFILES = {"apikey": "openai/gpt-6-luna", "openrouter": "openrouter/gpt-6-luna"}
PROFILES_FILE = Path(__file__).resolve().parent / "profiles.json"
USER_PROFILES_MAX_BYTES = 256 * 1024
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}  # the only hosts plain http and --local-only accept
MAX_RETRY_AFTER = 10.0  # seconds; a longer retry-after ends the call with an error instead of being waited out
FALLBACK_OPTIONS = {"insufficient_context", "insufficient_evidence", "none_fit"}
MAX_OPTIONS = 250  # larger option sets must be pre-filtered or split in code
RETRY_STATUSES = {429, 500, 502, 503, 504, 529}  # a classification call has no side effects, so retrying is safe
RETRY_DELAYS = (0.5, 1.0)
ALLOWED_FIELDS = {"model", "state", "questions", "provider", "trace", "session_id", "user"}
RESHAPE_MAX_CHARS = 120
RESHAPE_FIELDS = {"task", "recipe", "offloaded", "kept_for_llm", "caller"}  # local-only notes, never sent
# Token limits come from profiles; tokens are estimated (4 ASCII characters each, 1.5 per other character), so a
# refusal near a limit is approximate.
CHARS_PER_TOKEN, NON_ASCII_TOKENS_PER_CHAR = 4, 1.5
SECRET_KEYS = r"password|passwd|pwd|secret|client[_-]?secret|api[_-]?key|access[_-]?token|auth[_-]?token|token"
# Secrets scrubbed from state before sending, most specific first. A value is replaced, never the text around it.
REDACTIONS = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("api_key", re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("bearer", re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]{16,}")),
    # key = value: a quoted value is taken whole; a bare one only when it has a digit or is 16+ characters,
    # so prose such as "token: limit" is left alone
    ("secret_value", re.compile(rf"(?i)(\b(?:{SECRET_KEYS})[\"']?\s*[:=]\s*)(?:\"[^\"\n]+\"|'[^'\n]+'|"
                                r"(?=[^\s\"',;&)]*\d)[^\s\"',;&)]{6,}|[^\s\"',;&)]{16,})")),
)
SECRET_KEY = re.compile(rf"(?i)(?:x-)?(?:{SECRET_KEYS})")  # an object field whose whole name is a secret key
# The interface other skills may rely on, documented in references/callers.md. Bump the major version on any
# change that could break a caller; callers skip their classifier step when the major version differs.
CONTRACT_VERSION = "1.12"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """A decision API never redirects. Following one would re-send the Authorization header to whatever host the
    redirect names, so a 3xx surfaces as an HTTP error instead."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


urllib.request.install_opener(urllib.request.build_opener(NoRedirect))
# Loopback calls skip any HTTP(S)_PROXY from the environment, so local-only data never leaves the machine.
LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)


def fail(message: str, code: int = 2) -> NoReturn:
    print(f"classifier-skill: {message}", file=sys.stderr)
    raise SystemExit(code)


class CallError(SystemExit):
    """A provider request failed after its retries (HTTP error, network, non-JSON reply). Exits 1 if uncaught;
    catchers print `message`, so a batch can keep the answers it already has."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(1)
        self.message, self.status = message, status


class Exhausted(CallError):
    """The provider's credit, quota, or plan allowance is used up (OpenRouter 402, OpenAI `insufficient_quota`).
    Retrying cannot help; a route chain moves to its next step instead."""


def is_exhausted(status: int, body: str) -> bool:
    """Credit or allowance used up: OpenRouter 402, OpenAI `insufficient_quota`, Cloudflare Workers AI error 3036
    (the account's daily free allocation is spent)."""
    return status == 402 or (status == 429 and ("insufficient_quota" in body
                                                or re.search(r'"code"\s*:\s*3036\b', body) is not None))


# system-one+envelope: the System One body inside Cloudflare's {"result", "success", "errors", "messages"}.
SHAPES = {"system-one", "system-one+envelope", "openai"}
MODEL_PLACEHOLDER = "model"  # an endpoint's {model} is filled from each request's model, not from params
PROVIDER_KEYS = {"name", "shape", "auth", "endpoint", "endpoint_env", "model_env", "host", "model", "fields",
                 "loopback_only", "label", "limits", "text", "params"}
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
PARAM_VALUE = re.compile(r"[A-Za-z0-9_-]+")  # every placeholder value, whatever its own pattern: one path segment
USER_ID = re.compile(r"user/[a-z0-9][a-z0-9._/-]*")
# A user provider's own credential must be named for this skill, so a profiles file cannot pick up an unrelated
# secret (GITHUB_TOKEN, AWS_SECRET_ACCESS_KEY) and send it to a host of its choosing.
USER_CREDENTIAL = {"env": re.compile(r"CLASSIFIER_[A-Z0-9_]+"), "keychain": re.compile(r"classifier-[a-z0-9-]+")}
MODEL_KEYS = {"provider", "model", "calibration", "reply_models", "match", "label", "limits", "price"}
LIMIT_KEYS = {"request_tokens", "state_question_tokens", "context_tokens", "questions", "options", "body_bytes"}
TEXT_RULES = {"instructions": {"text"}, "choice": {"text", "text_or_null"}, "noul": {"text", "text_or_null"},
              "score": {"text", "text_or_null"}}


def _check_keys(where: str, entry: Any, allowed: set[str], required: set[str]) -> None:
    if not isinstance(entry, dict):
        fail(f"{where} must be an object")
    keys = {k for k in entry if not str(k).startswith("_")}  # "_..." keys are comments
    if keys - allowed:
        fail(f"{where} has unknown key(s): {', '.join(sorted(keys - allowed))}")
    if required - keys:
        fail(f"{where} is missing: {', '.join(sorted(required - keys))}")


def _check_limits(where: str, limits: Any) -> None:
    _check_keys(f"{where} limits", limits, LIMIT_KEYS, set())
    if not all(type(v) is int and v > 0 for k, v in limits.items() if not str(k).startswith("_")):
        fail(f"{where} limits must be positive integers")


def _check_provider(name: str, spec: dict[str, Any]) -> None:
    where = f"profile provider {name!r}"
    _check_keys(where, spec, PROVIDER_KEYS, {"name", "shape", "auth"})
    if spec["shape"] not in SHAPES:
        fail(f"{where} shape must be one of: {', '.join(sorted(SHAPES))}")
    auth = spec["auth"]
    _check_keys(f"{where} auth", auth, {"env", "keychain", "optional"}, {"env"})
    if auth["env"] is None and not auth.get("optional"):
        fail(f"{where} auth needs an env variable unless it is optional")
    endpoint = spec.get("endpoint")
    if endpoint is None:
        if not spec.get("endpoint_env") or "host" in spec or spec.get("loopback_only"):
            fail(f"{where} needs an endpoint, or an endpoint_env with no host (the host is then the URL's own)")
    else:
        params = spec.get("params", {})
        if not isinstance(params, dict) or set(PLACEHOLDER.findall(endpoint)) - {MODEL_PLACEHOLDER} != set(params) \
                or MODEL_PLACEHOLDER in params \
                or any(not isinstance(p, dict) or set(p) != {"env", "pattern"} for p in params.values()):
            fail(f"{where} endpoint placeholders must match its params, each with an env and a pattern "
                 f"({{{MODEL_PLACEHOLDER}}} is filled from the request's model and is not a param)")
        for param in params.values():
            try:
                re.compile(param["pattern"])
            except (re.error, TypeError):
                fail(f"{where} param pattern {param['pattern']!r} is not a valid regular expression")
        parsed = urlparse(endpoint)
        if PLACEHOLDER.search(parsed.netloc):
            fail(f"{where} endpoint may use placeholders only in its path")
        if spec.get("loopback_only"):
            if parsed.scheme not in {"http", "https"} or parsed.hostname not in LOOPBACK_HOSTS or "host" in spec:
                fail(f"{where} is loopback_only, so its endpoint must be on localhost and it takes no host")
        elif parsed.scheme != "https" or not spec.get("host") or parsed.hostname != spec["host"]:
            fail(f"{where} endpoint must be https on its host")
    if "model" not in spec and "model_env" not in spec:
        fail(f"{where} needs a default model or a model_env")
    if "fields" in spec and not (isinstance(spec["fields"], list) and set(spec["fields"]) <= ALLOWED_FIELDS
                                 and {"model", "state", "questions"} <= set(spec["fields"])):
        fail(f"{where} fields must list model, state, questions and only fields from: "
             f"{', '.join(sorted(ALLOWED_FIELDS))}")
    if "limits" in spec:
        _check_limits(where, spec["limits"])
    if "text" in spec:
        _check_keys(f"{where} text", spec["text"], set(TEXT_RULES), set())
        if any(rule not in TEXT_RULES[kind] for kind, rule in spec["text"].items()):
            fail(f"{where} text rules must be text or text_or_null (instructions: text)")


def _check_model(model_id: str, spec: dict[str, Any], providers: dict[str, Any]) -> None:
    where = f"profile model {model_id!r}"
    _check_keys(where, spec, MODEL_KEYS, {"provider", "model", "calibration", "reply_models"})
    if spec["provider"] not in providers:
        fail(f"{where} names unknown provider {spec['provider']!r}")
    if spec["calibration"] not in {"calibrated", "uncalibrated"}:
        fail(f"{where} calibration must be calibrated or uncalibrated")
    if not (isinstance(spec["reply_models"], list) and spec["reply_models"]
            and all(isinstance(r, str) and r.strip() for r in spec["reply_models"])):
        fail(f"{where} reply_models must be a non-empty list of model ids")
    if spec.get("match", "exact") not in {"exact", "family"} or not isinstance(spec["model"], str):
        fail(f"{where} match must be exact or family, and model a string")
    if "limits" in spec:
        _check_limits(where, spec["limits"])
    price = spec.get("price")
    if price is not None and not (type(price) in (int, float) and math.isfinite(price) and price >= 0):
        fail(f"{where} price must be dollars per million input tokens, or null when unpublished")


def _read_user_profiles(path_text: str) -> dict[str, Any]:
    """The CLASSIFIER_PROFILES file: a regular file (a symlink must lead to one), at most 256 KiB, one JSON object."""
    path = Path(path_text).expanduser()
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            fail(f"CLASSIFIER_PROFILES {path_text!r} is not a regular file")
        if resolved.stat().st_size > USER_PROFILES_MAX_BYTES:
            fail(f"CLASSIFIER_PROFILES {path_text!r} is over {USER_PROFILES_MAX_BYTES // 1024} KiB")
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail(f"could not read CLASSIFIER_PROFILES {path_text!r}: {exc}")
    if not isinstance(data, dict):
        fail("CLASSIFIER_PROFILES must hold a JSON object")
    _check_keys("CLASSIFIER_PROFILES", data, {"providers", "models"}, set())
    return data


def load_profiles(user_path: str | None = None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], set[str]]:
    """(providers, models, default limits, user ids). Shipped profiles come from profiles.json. A user file may only
    add ids starting with "user/"; a user provider that names a shipped credential must keep that credential's host,
    so no profile can send a shipped key anywhere new."""
    try:
        shipped = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail(f"could not read {PROFILES_FILE}: {exc}")
    _check_keys(str(PROFILES_FILE), shipped, {"defaults", "providers", "models"}, {"defaults", "providers", "models"})
    _check_keys(f"{PROFILES_FILE} defaults", shipped["defaults"], {"limits"}, {"limits"})
    _check_limits(f"{PROFILES_FILE} defaults", shipped["defaults"]["limits"])
    if not {"request_tokens", "state_question_tokens"} <= set(shipped["defaults"]["limits"]):
        fail(f"{PROFILES_FILE} defaults need request_tokens and state_question_tokens")
    for section in ("providers", "models"):
        if not isinstance(shipped[section], dict):
            fail(f"{PROFILES_FILE} {section} must be an object")
    providers, models = dict(shipped["providers"]), dict(shipped["models"])
    for name, spec in providers.items():
        _check_provider(name, spec)
    user_ids: set[str] = set()
    if user_path:
        user = _read_user_profiles(user_path)
        hosts = {}  # shipped credential (env variable or Keychain item) -> the only host it may go to
        for spec in providers.values():
            for credential in (spec["auth"].get("env"), spec["auth"].get("keychain")):
                if credential:
                    hosts[credential] = spec.get("host")
        for section, target in (("providers", providers), ("models", models)):
            entries = user.get(section) or {}
            if not isinstance(entries, dict):
                fail(f"CLASSIFIER_PROFILES {section} must be an object")
            for entry_id, spec in entries.items():
                if not USER_ID.fullmatch(str(entry_id)) or entry_id in target:
                    fail(f"CLASSIFIER_PROFILES {section} id {entry_id!r} must be new, start with user/, and use "
                         "lowercase letters, digits, and . _ / -")
                target[entry_id] = spec
                user_ids.add(entry_id)
        for name in user.get("providers") or {}:
            spec = providers[name]
            _check_provider(name, spec)
            for kind in ("env", "keychain"):
                credential = spec["auth"].get(kind)
                if not credential:
                    continue
                if credential in hosts:
                    if hosts[credential] is None or spec.get("host") != hosts[credential]:
                        fail(f"profile provider {name!r} uses the shipped credential {credential!r} off its host")
                elif not USER_CREDENTIAL[kind].fullmatch(credential):
                    fail(f"profile provider {name!r} credential {credential!r} must be a shipped one on its own "
                         f"host, or a new one named {USER_CREDENTIAL[kind].pattern}")
    seen = set()
    for model_id, spec in models.items():
        _check_model(model_id, spec, providers)
        key = (spec["provider"], spec["model"], spec.get("match", "exact"))
        if key in seen:
            fail(f"profile model {model_id!r} repeats another profile's provider and model")
        seen.add(key)
    missing = sorted(set(ROUTE_PROFILES.values()) - set(models))
    if missing:
        fail(f"{PROFILES_FILE} is missing the route chain's model profile(s): {', '.join(missing)}")
    limits = {k: v for k, v in shipped["defaults"]["limits"].items() if not k.startswith("_")}
    return providers, models, limits, user_ids


def provider_view(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    """The runtime view of a provider profile used throughout this script."""
    view = {"name": spec["name"], "profile": spec}
    for key in ("endpoint", "host", "model", "model_env", "label", "loopback_only"):
        if key in spec:
            view[key] = spec[key]
    if spec["shape"] != "system-one":
        view["shape"] = spec["shape"]
    if spec["auth"].get("env"):
        view["key"] = spec["auth"]["env"]
    if spec["auth"].get("optional"):
        view["key_optional"] = True
    if name not in AUTO_PROVIDERS:
        view["explicit"] = True
    if "endpoint_env" in spec:
        view["endpoint_env" if "endpoint" in spec else "url_env"] = spec["endpoint_env"]
    if "fields" in spec:
        view["fields"] = set(spec["fields"])
    return view


_PROVIDER_PROFILES, MODELS, DEFAULT_LIMITS, USER_PROFILE_IDS = load_profiles(
    os.environ.get("CLASSIFIER_PROFILES", "").strip() or None)
PROVIDERS = {name: provider_view(name, spec) for name, spec in _PROVIDER_PROFILES.items()}
ROUTES = {name: (MODELS[profile_id]["provider"], MODELS[profile_id]["model"])
          for name, profile_id in ROUTE_PROFILES.items()}
LUNA_ON_OPENROUTER = ROUTES["openrouter"][1]


def model_profile(provider: str, model: Any) -> tuple[str, dict[str, Any]] | None:
    """The model profile for this provider and model: an exact match first, then a family match ("nimble" for
    "nimble:9b"). None when the model has no profile."""
    if not isinstance(model, str):
        return None
    family = model.split(":", 1)[0]
    for wanted, match in ((model, "exact"), (family, "family")):
        for profile_id, spec in MODELS.items():
            if spec["provider"] == provider and spec["model"] == wanted and spec.get("match", "exact") == match:
                return profile_id, spec
    return None


def note_unprofiled(provider: str, model: Any) -> None:
    """Say so when a model has no profile: it runs on its provider's limits and gets the strict review."""
    if model_profile(provider, model) is None:
        print(f"classifier-skill: no profile for {provider} model {model!r}; using {provider}'s limits, and its "
              "answers get the strict review (references/providers.md, Profiles)", file=sys.stderr)


def limits_for(provider: str, model: Any) -> dict[str, Any]:
    """Defaults, then the provider's limits, then the model profile's."""
    found = model_profile(provider, model)
    return {**DEFAULT_LIMITS, **PROVIDERS[provider]["profile"].get("limits", {}),
            **(found[1].get("limits", {}) if found else {})}


def reply_matches(spec: dict[str, Any], reply_model: Any) -> bool:
    """A reply's model id is one the profile lists, exactly or with a version or variant suffix (-, ., :)."""
    return isinstance(reply_model, str) and any(
        reply_model == r or reply_model.startswith(r) and reply_model[len(r)] in "-.:" for r in spec["reply_models"])


def is_strict(reply_model: Any, provider: str | None = None, sent_model: Any = None) -> bool:
    """Whether answers get the stricter review. With the provider and model that were asked for, only a calibrated
    profile whose known reply ids match the reply's model is reviewed normally; anything unknown is strict. Without
    them (older callers), any calibrated profile's reply ids decide."""
    if provider is None:
        return not any(spec["calibration"] == "calibrated" and reply_matches(spec, reply_model)
                       for spec in MODELS.values())
    found = model_profile(provider, sent_model)
    return not (found and found[1]["calibration"] == "calibrated" and reply_matches(found[1], reply_model))


def profile_log(provider: str, model: Any) -> dict[str, Any]:
    """Which profile definition a call used, for the call log: its id, where it came from, and a short hash."""
    found = model_profile(provider, model)
    ids = [provider] + ([found[0]] if found else [])
    def bare(spec: Any) -> Any:  # comments ("_..." keys) do not change the hash
        return {k: bare(v) for k, v in spec.items() if not str(k).startswith("_")} if isinstance(spec, dict) else spec
    blob = json.dumps({"provider": bare(PROVIDERS[provider]["profile"]), "model": bare(found[1]) if found else None},
                      sort_keys=True)
    return {"profile": found[0] if found else None,
            "profile_source": "user" if any(i in USER_PROFILE_IDS for i in ids) else "shipped",
            "profile_hash": hashlib.sha256(blob.encode()).hexdigest()[:12]}


def load_request(path: str) -> dict[str, Any]:
    try:
        if path == "-":
            value = json.load(sys.stdin)
        else:
            with Path(path).expanduser().open(encoding="utf-8") as handle:
                value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"could not read request JSON: {exc}")

    if not isinstance(value, dict):
        fail("request must be a JSON object")
    return value


def validate_question(question_id: str, question: Any) -> None:
    if not isinstance(question, dict):
        fail(f"question {question_id!r} must be an object")
    kind = question.get("type")
    if kind not in {"choice", "noul", "score"}:
        fail(f"question {question_id!r} has unsupported type {kind!r}")
    instructions = question.get("instructions")
    if not (isinstance(instructions, str) and instructions.strip()
            or isinstance(instructions, (dict, list)) and instructions):
        fail(f"question {question_id!r} needs non-empty instructions (a string or a structured object)")

    criteria = question.get("criteria")
    if kind == "choice":
        if not isinstance(criteria, dict) or len(criteria) < 2:
            fail(f"choice question {question_id!r} needs at least two criteria options")
        if len(criteria) > MAX_OPTIONS:
            fail(f"choice question {question_id!r} has {len(criteria)} options; pre-filter or split to {MAX_OPTIONS}")
        if not all(v is None or isinstance(v, str) and v.strip() or isinstance(v, dict) and v
                   for v in criteria.values()):
            fail(f"choice question {question_id!r} options need a description string, object, or null")
    elif kind == "score":
        if (not isinstance(criteria, list) or len(criteria) < 2
                or not all(isinstance(level, str) and level.strip() for level in criteria)):
            fail(f"score question {question_id!r} needs at least two non-empty string levels, lowest to highest")
    elif criteria is not None:
        if not isinstance(criteria, dict) or set(criteria) != {"true", "false"}:
            fail(f"noul question {question_id!r} criteria must have exactly the keys true and false")


def validate_state(state: Any, where: str = "request") -> None:
    if not isinstance(state, (str, dict, list)) or not state:
        fail(f"{where} state must be a non-empty string, object, or array")


def select_provider(requested: str | None) -> str:
    """--provider, then CLASSIFIER_PROVIDER, then whichever key is set (OpenRouter first when both are)."""
    name = requested or os.environ.get("CLASSIFIER_PROVIDER", "").strip().lower() or None
    if name:
        if name not in PROVIDERS:
            fail(f"unknown provider {name!r}; use one of: {', '.join(PROVIDERS)}")
        return name
    for candidate in AUTO_PROVIDERS:  # a fixed list in code, never the merged profiles
        if os.environ.get(PROVIDERS[candidate]["key"], "").strip():
            return candidate
    return "openrouter"


def detect_host() -> tuple[str, str]:
    """("codex" | "other", reason). CLASSIFIER_HOST decides when set. Otherwise Codex's CODEX_THREAD_ID counts only
    when Claude Code is not the one running (a Claude worker launched from Codex inherits that variable)."""
    configured = os.environ.get("CLASSIFIER_HOST", "").strip().lower()
    if configured:
        if configured not in {"codex", "other"}:
            fail(f"CLASSIFIER_HOST must be codex or other, not {configured!r}")
        return configured, "CLASSIFIER_HOST"
    if os.environ.get("CODEX_THREAD_ID", "").strip() and not os.environ.get("CLAUDECODE", "").strip():
        return "codex", "CODEX_THREAD_ID"
    return "other", "default"


def plan_route(requested: str | None, model_override: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Which provider(s) a run may use. An explicit provider (--provider or CLASSIFIER_PROVIDER) is used alone. Inside
    Codex, or when CLASSIFIER_ROUTE is set, the run uses the route chain (ROUTES) from CLASSIFIER_ROUTE's step on,
    skipping steps without a credential; OpenRouter's Jev is never used automatically there. Otherwise one provider, as
    before. A dry run keeps every step, since it sends nothing and needs no credential. Returns
    {"steps": [(provider, model or None)], "host", "host_reason", "route"}."""
    host, host_reason = detect_host()
    start = os.environ.get("CLASSIFIER_ROUTE", "").strip().lower()
    if start and start not in ROUTES:
        hint = "; the ChatGPT sign-in route is not built yet" if start == "chatgpt" else ""
        fail(f"CLASSIFIER_ROUTE must be one of: {', '.join(ROUTES)}{hint}")
    explicit = requested or os.environ.get("CLASSIFIER_PROVIDER", "").strip().lower() or None
    info = {"host": host, "host_reason": host_reason}
    if explicit or not (start or host == "codex"):
        return {**info, "route": None, "steps": [(select_provider(explicit), None)]}
    if model_override:
        fail("--model needs --provider when the route chain is in use (each step has its own pinned model)")
    names = list(ROUTES)[list(ROUTES).index(start or "apikey"):]
    usable = [name for name in names
              if dry_run or os.environ.get(PROVIDERS[ROUTES[name][0]]["key"], "").strip()]
    steps = [ROUTES[name] for name in usable]
    if not steps:
        keys = " or ".join(PROVIDERS[ROUTES[name][0]]["key"] for name in names)
        where = (" Inside Codex, variables whose names contain KEY are hidden from commands unless the Codex "
                 "configuration lets them through (references/providers.md, Codex).") if host == "codex" else ""
        fail(f"no credential for the OpenAI route: set {keys}, or choose a provider with --provider.{where}", code=1)
    return {**info, "route": usable[0], "steps": steps}


class Router:
    """Sends each request on the current step of a route plan and moves to the next step when a step is used up.
    The one send path for jev_decide.py and classify_items.py, so every request gets the same choice and fallback."""

    def __init__(self, plan: dict[str, Any], endpoint: str | None, timeout: float,
                 model_for: Any = lambda provider, default: default):
        self.plan, self.timeout, self.model_for = plan, timeout, model_for
        self.index, self.moves, self.sent_model = 0, [], None
        self.cost_estimated = 0.0  # from profile prices, for providers that report tokens but no cost
        self.endpoint = endpoint  # an explicit --endpoint applies to a single-step plan only
        if endpoint and len(plan["steps"]) > 1:
            fail("--endpoint needs --provider when the route chain is in use")
        placeholder = "{" + MODEL_PLACEHOLDER + "}"
        if endpoint and placeholder in PROVIDERS[self.provider]["endpoint"] and placeholder not in endpoint:
            print(f"classifier-skill: --endpoint has no {placeholder}, so every request goes to that URL whatever "
                  "--model says", file=sys.stderr)

    @property
    def provider(self) -> str:
        return self.plan["steps"][self.index][0]

    def current_endpoint(self) -> str:
        return self.endpoint or provider_endpoint(self.provider)

    def endpoints(self) -> list[tuple[str, str]]:
        return [(provider, self.endpoint or provider_endpoint(provider)) for provider, _ in self.plan["steps"]]

    def model(self, default: str) -> str:
        provider, pinned = self.plan["steps"][self.index]
        return self.model_for(provider, pinned or default)

    def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        while True:
            request = {**payload, "model": self.model(payload["model"])}
            self.sent_model = request["model"]
            try:
                response = call_jev(request, self.current_endpoint(), self.timeout, self.provider)
                self.cost_estimated += estimated_cost(self.provider, request["model"], response)
                return response
            except Exhausted as exc:
                if self.index + 1 >= len(self.plan["steps"]):
                    raise
                moved_from = self.provider
                self.index += 1
                self.moves.append({"from": moved_from, "to": self.provider, "reason": "exhausted"})
                print(f"classifier-skill: {PROVIDERS[moved_from]['name']} credit or plan is used up; moving to "
                      f"{PROVIDERS[self.provider]['name']}: {exc.message}", file=sys.stderr)

    def strict(self, reply_model: Any) -> bool:
        """The review rule for an answer to the request just sent, from the profile that was asked for."""
        return is_strict(reply_model, self.provider, self.sent_model)

    def log_fields(self, default_model: Any = None) -> dict[str, Any]:
        model = self.sent_model or self.model(default_model)
        return {"host": self.plan["host"], "host_reason": self.plan["host_reason"], "route": self.plan["route"],
                "provider_final": self.provider, "credential": "api_key" if PROVIDERS[self.provider].get("key")
                and os.environ.get(PROVIDERS[self.provider]["key"], "").strip() else "none",
                **profile_log(self.provider, model), **({"moves": self.moves} if self.moves else {}),
                "cost_estimated": round(self.cost_estimated, 8) or None}


def redact(value: Any) -> tuple[Any, int]:
    """Scrub secrets from every string in state; returns the scrubbed copy and how many values were replaced."""
    if isinstance(value, str):
        count = 0
        for kind, pattern in REDACTIONS:
            value, n = pattern.subn(lambda m: (m.group(1) if m.re.groups else "") + f"[REDACTED:{kind}]", value)
            count += n
        return value, count
    if isinstance(value, list):
        pairs = [redact(item) for item in value]
        return [v for v, _ in pairs], sum(n for _, n in pairs)
    if isinstance(value, dict):
        out, count = {}, 0
        for key, item in value.items():
            if item not in (None, "", [], {}) and not isinstance(item, bool) \
                    and SECRET_KEY.fullmatch(str(key).strip()):
                out[key], n = "[REDACTED:secret_value]", 1
            else:
                out[key], n = redact(item)
            count += n
        return out, count
    return value, 0


def estimated_tokens(value: Any) -> int:
    """About 4 ASCII characters per token; other scripts (CJK, emoji) are far denser, so count them heavier."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    ascii_chars = sum(ch.isascii() for ch in text)
    return math.ceil(ascii_chars / CHARS_PER_TOKEN + (len(text) - ascii_chars) * NON_ASCII_TOKENS_PER_CHAR)


def check_size(payload: dict[str, Any], where: str = "request", provider: str | None = None) -> None:
    """Refuse, before sending, a request over the model's token limits (its profile's, else the defaults). Sizes the
    payload as it will be sent."""
    limits = limits_for(provider, payload.get("model")) if provider else DEFAULT_LIMITS
    near = max(estimated_tokens({"state": payload["state"], "question": {qid: q}})
               for qid, q in payload["questions"].items())
    total = estimated_tokens(payload)
    if near > limits["state_question_tokens"] or total > limits["request_tokens"]:
        fail(f"{where} is about {near:,} tokens of state plus its longest question and {total:,} in all; the limits "
             f"are {limits['state_question_tokens']:,} and {limits['request_tokens']:,}. Trim state or split the "
             "questions")


TEXT_ALLOWED = {"text": "strings", "text_or_null": "strings or null"}


def check_provider_limits(payload: dict[str, Any], provider: str, where: str = "request",
                          overrides: dict[str, int] | None = None) -> None:
    """Refuse requests that exceed a provider's or model's profile limits before any data is sent: question and
    option counts, text-only instructions or descriptions, then (with state) body bytes and the context window.
    `overrides` is for --probe --options only: it raises one limit for one probe call."""
    spec = PROVIDERS[provider]
    label = spec.get("label", spec["name"])
    limits = {**limits_for(provider, payload.get("model")), **(overrides or {})}
    rules = spec["profile"].get("text", {})
    questions = payload["questions"]
    if "questions" in limits and len(questions) > limits["questions"]:
        fail(f"{where} has {len(questions)} questions; {label} allows {limits['questions']}")
    for question_id, question in questions.items():
        kind, criteria = question["type"], question.get("criteria")
        if kind in {"choice", "score"} and "options" in limits and len(criteria) > limits["options"]:
            fail(f"{where} question {question_id!r} has {len(criteria)} options; {label} allows {limits['options']}")
        if rules.get("instructions") and not isinstance(question["instructions"], str):
            fail(f"{where} question {question_id!r} has structured instructions; {label} takes only text, so "
                 "rewrite them as strings")
        rule = rules.get(kind)
        if rule and criteria is not None:
            values = criteria.values() if isinstance(criteria, dict) else criteria
            if not all(isinstance(v, str) or (v is None and rule == "text_or_null") for v in values):
                fail(f"{where} question {question_id!r} has structured {kind} descriptions; {label} allows only "
                     f"{TEXT_ALLOWED[rule]}")
    if "state" not in payload:
        return
    body_bytes = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))  # exactly as call_jev sends it
    if "body_bytes" in limits and body_bytes > limits["body_bytes"]:
        fail(f"{where} is {body_bytes:,} bytes; {label} allows {limits['body_bytes']:,}. "
             "Trim state or split the questions")
    if "context_tokens" in limits:
        total = estimated_tokens(payload)
        if total > limits["context_tokens"]:
            found = model_profile(provider, payload.get("model"))
            name = (found[1].get("label") if found else None) or payload.get("model")
            fail(f"{where} is about {total:,} tokens; {name}'s context limit is {limits['context_tokens']:,}. "
                 "Trim state or split the questions")


def decisions(answers: dict[str, Any], review: dict[str, list[str]], threshold: float) -> dict[str, str]:
    """act / skip / human per question at one threshold; any review reason sends the answer to a person."""
    out = {}
    for question_id, answer in (answers or {}).items():
        if question_id in review:
            out[question_id] = "human"
        elif answer.get("type") == "noul":
            p = answer.get("noul", 0.5)
            out[question_id] = "act" if p >= threshold else "skip" if p <= 1 - threshold else "human"
        else:
            out[question_id] = "act" if (answer.get("confidence") or 0) >= threshold else "human"
    return out


def normalize_request(raw: dict[str, Any], model_override: str | None, batch: bool = False,
                      default_model: str = PROVIDERS["openrouter"]["model"]) -> dict[str, Any]:
    unknown = sorted(set(raw) - ALLOWED_FIELDS - {"reshape"})
    if unknown:
        fail(f"unsupported top-level fields: {', '.join(unknown)}")

    if batch:
        if "state" in raw:
            fail("in batch mode the template must not contain state; each batch line is one state")
    elif "state" not in raw:
        fail("request needs a state")
    else:
        validate_state(raw["state"])

    questions = raw.get("questions")
    if not isinstance(questions, dict) or not questions:
        fail("request needs a non-empty questions object")
    for question_id, question in questions.items():
        validate_question(str(question_id), question)

    payload = dict(raw)
    payload["model"] = model_override or payload.get("model") or default_model
    if not isinstance(payload["model"], str) or not payload["model"].strip():
        fail("model must be a non-empty string (for provider compatible, set CLASSIFIER_COMPATIBLE_MODEL or pass --model)")
    return payload


def provider_endpoint(provider: str) -> str:
    """The provider's default endpoint; the compatible provider's comes from its environment variable."""
    spec = PROVIDERS[provider]
    if "url_env" not in spec:
        override = os.environ.get(spec["endpoint_env"]) if "endpoint_env" in spec else None
        return override or fill_params(provider, spec["endpoint"])
    url = os.environ.get(spec["url_env"], "").strip()
    if not url:
        fail(f"{spec['url_env']} is not set; provider {provider} needs the server's full request URL", code=1)
    return url


def check_endpoint(provider: str, endpoint: str) -> None:
    """Refuse any endpoint off the provider's host. Plain http is allowed only for a server on this machine."""
    spec = PROVIDERS[provider]
    parsed = urlparse(endpoint)
    try:
        parsed.port
    except ValueError:
        fail(f"refusing endpoint {safe_endpoint(endpoint)!r}: its port is not a number", code=1)
    if spec.get("loopback_only"):
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in LOOPBACK_HOSTS:
            fail(f"refusing Ollama endpoint {safe_endpoint(endpoint)!r}; provider ollama only allows localhost",
                 code=1)
        return
    host = spec.get("host") or urlparse(provider_endpoint(provider)).hostname
    local_http = parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS and "url_env" in spec
    if parsed.hostname != host or not (parsed.scheme == "https" or local_http):
        allowed = f"https://{host}" + (" (or http on localhost)" if "url_env" in spec else "")
        fail(f"refusing to send credentials to {safe_endpoint(endpoint)!r}; {provider} only allows {allowed}",
             code=1)


def fill_params(provider: str, endpoint: str) -> str:
    """Fill path placeholders such as {account_id} from their environment variables. Each value must match its
    pattern in full, so a value cannot add path segments, a query, or a host."""
    for name, param in PROVIDERS[provider]["profile"].get("params", {}).items():
        value = os.environ.get(param["env"], "")
        if not re.fullmatch(param["pattern"], value) or not PARAM_VALUE.fullmatch(value):
            fail(f"{param['env']} must be set to a value matching {param['pattern']} (letters, digits, _ and - "
                 f"only) for provider {provider}", code=1)
        endpoint = endpoint.replace("{" + name + "}", value)
    return endpoint


def fill_model(endpoint: str, model: Any) -> str:
    """Fill an endpoint's {model} from the request's model; the model must be one path segment."""
    placeholder = "{" + MODEL_PLACEHOLDER + "}"
    if placeholder not in endpoint:
        return endpoint
    if not isinstance(model, str) or not PARAM_VALUE.fullmatch(model):
        raise CallError(f"model {model!r} cannot go in this provider's URL (letters, digits, _ and - only)")
    return endpoint.replace(placeholder, model)


def is_local(endpoint: str) -> bool:
    return urlparse(endpoint).hostname in LOOPBACK_HOSTS


NOUL_DESCRIPTIONS = "\n\nTrue means: {true}\nFalse means: {false}"  # OpenAI predicates have no description fields


def to_openai_request(payload: dict[str, Any]) -> dict[str, Any]:
    """System One request -> OpenAI Decisions request. Question ids become names; check_provider_limits has already
    refused anything that cannot be said in text."""
    questions = []
    for question_id, question in payload["questions"].items():
        kind, criteria = question["type"], question.get("criteria")
        out = {"name": question_id, "instructions": question["instructions"]}
        if kind == "noul":
            out["type"] = "predicate"
            if criteria:
                out["instructions"] += NOUL_DESCRIPTIONS.format(**criteria)
        elif kind == "choice":
            out["type"], out["choices"] = "choice", [{"value": value, "description": description or value}
                                                     for value, description in criteria.items()]
        else:
            out["type"], out["levels"] = "score", [{"label": level, "description": level} for level in criteria]
        questions.append(out)
    state = payload["state"]
    return {"model": payload["model"], "questions": questions,
            "input": state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)}


def from_openai_response(raw: Any, requested_model: str, questions: dict[str, Any]) -> Any:
    """OpenAI Decisions response -> System One response. Anything malformed is passed on in a shape answer_errors
    rejects, never repaired: a score level whose label is not the level asked at that index, or an option or level
    listed twice. A refusal becomes {"type": "refusal"}, which review_flags sends to a person.
    requested_model is retained for caller compatibility, never substituted for missing response identity."""
    if not isinstance(raw, dict) or not isinstance(raw.get("answers"), list):
        return raw
    answers: dict[str, Any] = {}
    try:
        for item in raw["answers"]:
            name, kind = item["name"], item["type"]
            if name in answers:
                return {**raw, "answers": None}  # two answers to one question: unsafe to act on
            if kind == "refusal":
                answers[name] = {"type": "refusal"}
            elif kind == "predicate":
                answers[name] = {"type": "noul", "noul": item.get("probability")}
            elif kind == "choice":
                pairs = [(p["value"], p["probability"]) for p in item["probabilities"]]
                if len({value for value, _ in pairs}) != len(pairs):
                    return {**raw, "answers": None}
                answers[name] = {"type": "choice", "choice": item.get("choice"), "confidence": item.get("confidence"),
                                 "probabilities": dict(pairs)}
            elif kind == "score":
                levels = (questions.get(name) or {}).get("criteria") or []
                pairs = [(p["value"], p["probability"]) for p in item["probabilities"]]
                if len({value for value, _ in pairs}) != len(pairs) or any(
                        type(p["value"]) is not int or not 0 <= p["value"] < len(levels)
                        or p.get("label") != levels[p["value"]] for p in item["probabilities"]):
                    return {**raw, "answers": None}
                answers[name] = {"type": "score", "score": item.get("score"), "confidence": item.get("confidence"),
                                 "probabilities": {str(value): probability for value, probability in pairs}}
            else:
                answers[name] = {"type": kind}
    except (KeyError, TypeError, AttributeError):
        return {**raw, "answers": None}
    model = raw.get("model")
    return {"id": raw.get("id"), "model": model, "usage": raw.get("usage"), "answers": answers}


def call_jev(payload: dict[str, Any], endpoint: str, timeout: float, provider: str = "openrouter") -> dict[str, Any]:
    spec = PROVIDERS[provider]
    shape = spec.get("shape", "system-one")
    openai_shape = shape == "openai"
    body = to_openai_request(payload) if openai_shape else payload
    check_endpoint(provider, endpoint)
    endpoint = fill_model(endpoint, payload.get("model"))
    key_env = spec.get("key")
    api_key = os.environ.get(key_env, "").strip() if key_env else ""
    if not api_key and not spec.get("key_optional"):
        fail(f"{key_env} is not available for provider {provider}", code=1)
    service = spec["name"]
    headers = {"Content-Type": "application/json", "User-Agent": "classifier-skill/1.0"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    for attempt in range(len(RETRY_DELAYS) + 1):
        retry = attempt < len(RETRY_DELAYS)
        try:
            opener = LOCAL_OPENER.open if is_local(endpoint) else urllib.request.urlopen
            with opener(request, timeout=timeout) as response:
                result = json.load(response)
                if shape == "system-one+envelope":
                    return unwrap_envelope(result, service)
                return from_openai_response(result, payload["model"], payload["questions"]) if openai_shape else result
        except urllib.error.HTTPError as exc:
            asked = retry_after(exc)
            reply = exc.read().decode("utf-8", errors="replace") if exc.code in {402, 429} else None
            if reply is not None and is_exhausted(exc.code, reply):
                exc.close()
                raise Exhausted(f"{service} returned HTTP {exc.code}: {reply}") from exc
            if retry and exc.code in RETRY_STATUSES and (asked is None or asked <= MAX_RETRY_AFTER):
                exc.close()
                time.sleep(RETRY_DELAYS[attempt] if asked is None else asked)
                continue
            body = reply if reply is not None else exc.read().decode("utf-8", errors="replace")
            exc.close()
            wait = f" (asked to retry after {asked:.0f}s, longer than the {MAX_RETRY_AFTER:.0f}s this script waits)" \
                if asked is not None and asked > MAX_RETRY_AFTER and exc.code in RETRY_STATUSES else ""
            raise CallError(f"{service} returned HTTP {exc.code}{wait}: {body}{auth_hint(provider, exc.code)}",
                            exc.code) from exc
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            if retry:
                time.sleep(RETRY_DELAYS[attempt])
                continue
            raise CallError(f"{service} request failed: {exc}") from exc
        except ValueError as exc:  # JSONDecodeError, or bytes that are not valid text at all
            raise CallError(f"{service} returned non-JSON: {exc}") from exc
    raise AssertionError("unreachable")


def unwrap_envelope(raw: Any, service: str) -> Any:
    """The System One body from a {"result", "success", "errors"} envelope; a reply without success is an error,
    never an answer."""
    if isinstance(raw, dict) and raw.get("success") is True and isinstance(raw.get("result"), dict):
        return raw["result"]
    errors = raw.get("errors") if isinstance(raw, dict) else None
    # Labelled bad_request, like the HTTP 400 Cloudflare usually sends; an items run stops on it as on any other 400.
    raise CallError(f"{service} returned no result: {json.dumps(errors, ensure_ascii=False)[:500]}", 400)


def auth_hint(provider: str, status: int) -> str:
    """For 401/403 from a provider whose URL carries an account (or similar) id: a wrong id fails the same way as a
    wrong token, so name both."""
    params = PROVIDERS[provider]["profile"].get("params", {})
    key = PROVIDERS[provider].get("key")
    if status not in {401, 403} or not params or not key:
        return ""
    names = " and ".join(p["env"] for p in params.values())
    return f" (check that {key} is valid for this service and that {names} is that token's own)"


def retry_after(exc: urllib.error.HTTPError) -> float | None:
    """Seconds the server asked us to wait, from a Retry-After in seconds or as an HTTP date; None when absent or
    unreadable (then the default backoff applies)."""
    raw = str((exc.headers or {}).get("retry-after") or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return max(0.0, (when - datetime.datetime.now(datetime.timezone.utc)).total_seconds())


SCORE_TOLERANCE = 0.05  # `score` is the expected level, Σ level × probability; allow for provider rounding


def _unit(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def _distribution(probabilities: Any, keys: set[str]) -> bool:
    return (isinstance(probabilities, dict) and set(probabilities) == keys
            and all(_unit(p) for p in probabilities.values()) and abs(sum(probabilities.values()) - 1) < 0.02)


def answer_errors(answers: Any, questions: dict[str, Any]) -> list[str]:
    """Structural problems that make a response unsafe to act on. Empty means every asked question is well formed."""
    if not isinstance(answers, dict):
        return ["response has no answers object"]
    errors = [f"{question_id}: answer to a question that was not asked"
              for question_id in sorted(set(answers) - set(questions))]
    for question_id, question in questions.items():
        answer, kind = answers.get(question_id), question["type"]
        if isinstance(answer, dict) and answer == {"type": "refusal"}:
            continue  # a provider declined; review_flags sends it to a person
        if not isinstance(answer, dict) or answer.get("type") != kind:
            errors.append(f"{question_id}: missing or wrong-type answer")
        elif kind == "choice":
            options, probabilities = set(question["criteria"]), answer.get("probabilities")
            if (not isinstance(answer.get("choice"), str) or answer["choice"] not in options or not _distribution(probabilities, options)
                    or not _unit(answer.get("confidence"))
                    or probabilities[answer["choice"]] < max(probabilities.values()) - 1e-6):
                errors.append(f"{question_id}: choice answer is not a valid pick over the offered options")
        elif kind == "noul":
            if not _unit(answer.get("noul")):
                errors.append(f"{question_id}: noul is not a probability")
        else:
            levels, score = len(question["criteria"]), answer.get("score")
            if (not _distribution(answer.get("probabilities"), {str(i) for i in range(levels)})
                    or type(score) not in (int, float) or not 0 <= score <= levels - 1
                    or not _unit(answer.get("confidence"))
                    or abs(score - sum(int(k) * p for k, p in answer["probabilities"].items())) > SCORE_TOLERANCE):
                errors.append(f"{question_id}: score answer is outside the rubric or disagrees with its probabilities")
    return errors


def response_errors(response: Any, questions: dict[str, Any]) -> list[str]:
    """The one gate every caller uses before touching a response: its shape, its usage, and its answers."""
    if not isinstance(response, dict):
        return ["response is not a JSON object"]
    if not isinstance(response.get("usage"), (dict, type(None))):
        return ["response usage is not an object"]
    if not isinstance(response.get("model"), str):
        return ["response model is not a string"]
    if not response["model"].strip():
        return ["response model is empty"]
    return answer_errors(response.get("answers"), questions)


def response_cost(response: Any) -> float:
    """The reported cost in dollars; 0 when absent or unreadable (an odd cost is not a reason to drop an answer)."""
    usage = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        return 0.0
    try:
        cost = float(usage.get("cost") or 0)
        return cost if math.isfinite(cost) and cost >= 0 else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def estimated_cost(provider: str, model: Any, response: Any) -> float:
    """Dollars from the model profile's price and the reported input tokens, for a reply that reports tokens but no
    cost; 0 when the reply reports a cost, or the profile has no price."""
    usage = response.get("usage") if isinstance(response, dict) else None
    found = model_profile(provider, model)
    price = found[1].get("price") if found else None
    if price is None or not isinstance(usage, dict) or usage.get("cost") is not None:
        return 0.0
    return usage_tokens(response) * price / 1_000_000


def review_flags(answers: dict[str, Any], margin: float, model: Any = None,
                 strict: bool | None = None) -> dict[str, list[str]]:
    """Deterministic reasons to route an answer to a human; see the module docstring. A model whose profile is not
    calibrated, or that has no profile, gets a wider margin, a wider noul band, and a higher score bar. `strict`
    comes from the profile that was asked for (Router.strict); without it, the reply's model id decides."""
    strict = is_strict(model) if strict is None else strict
    margin, noul_band, score_bar = (margin + 0.1, 0.3, 0.6) if strict else (margin, 0.35, 0.5)
    flags: dict[str, list[str]] = {}
    for question_id, answer in (answers or {}).items():
        reasons = []
        kind = answer.get("type")
        if kind == "refusal":
            reasons.append("the model declined to answer")
        elif kind == "choice":
            if answer.get("choice") in FALLBACK_OPTIONS:
                reasons.append(f"fallback option {answer['choice']!r} chosen")
            ranked = sorted((answer.get("probabilities") or {}).values(), reverse=True)
            if len(ranked) > 1 and ranked[0] - ranked[1] < margin:
                reasons.append(f"runner-up within {ranked[0] - ranked[1]:.2f}")
        elif kind == "noul" and isinstance(answer.get("noul"), (int, float)) \
                and noul_band < answer["noul"] < 1 - noul_band:
            reasons.append(f"noul {answer['noul']:.2f} is near 0.5")
        elif kind == "score" and isinstance(answer.get("confidence"), (int, float)) \
                and answer["confidence"] < score_bar:
            reasons.append(f"score confidence {answer['confidence']:.2f} below {score_bar}")
        if reasons:
            flags[question_id] = reasons
    return flags


def known_recipes() -> set[str] | None:
    """Recipe names from references/recipes.md headings ("## 13. card-sort"); None if the file is missing."""
    path = Path(__file__).resolve().parent.parent / "references/recipes.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return {line.split(".", 1)[1].strip() for line in text.splitlines()
            if line.startswith("## ") and "." in line and line[3:].split(".", 1)[0].strip().isdigit()}


def caller_slug(name: str) -> str:
    """Normalize a calling skill's name so the log groups it once: "Code Owl" and "code_owl" become "code-owl"."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")[:40].strip("-")
    return slug or "unknown"


def take_reshape(payload: dict[str, Any]) -> dict[str, str]:
    """Remove the local `reshape` note from the payload and validate it; it is logged, never sent."""
    note = payload.pop("reshape", None)
    if note is None:
        return {}
    if not isinstance(note, dict):
        fail(f"reshape must be an object of non-empty strings with keys from: {', '.join(sorted(RESHAPE_FIELDS))}")
    unknown = sorted(set(note) - RESHAPE_FIELDS)
    if unknown:  # forward-compatible: a caller written for a newer contract still gets its answers
        print(f"classifier-skill: ignoring unknown reshape field(s): {', '.join(unknown)}", file=sys.stderr)
        note = {k: v for k, v in note.items() if k in RESHAPE_FIELDS}
    if not all(isinstance(v, str) and v.strip() for v in note.values()):
        fail(f"reshape must be an object of non-empty strings with keys from: {', '.join(sorted(RESHAPE_FIELDS))}")
    if "caller" in note:
        note["caller"] = caller_slug(note["caller"])
    # The note is logged, so keep it to short generic labels; long free text is where item content would leak.
    note = {k: v if len(v) <= RESHAPE_MAX_CHARS else v[:RESHAPE_MAX_CHARS - 1] + "…" for k, v in note.items()}
    recipes = known_recipes()
    recipe = note.get("recipe", "custom").strip()
    if recipe != "custom" and recipes is not None and recipe not in recipes:
        note["recipe_text"] = recipe  # keep the free text, but group it under custom in the log
        print(f"classifier-skill: recipe {recipe!r} is not in references/recipes.md; logged as custom. Use a "
              "recipe's name exactly, or \"custom\" for your own design.", file=sys.stderr)
        recipe = "custom"
    note["recipe"] = recipe
    return note


def log_path() -> Path | None:
    """CLASSIFIER_SKILL_LOG sets the call log (\"off\" disables); default is the user's XDG state directory."""
    configured = os.environ.get("CLASSIFIER_SKILL_LOG", "").strip()
    if configured.lower() == "off":
        return None
    if configured:
        return Path(configured).expanduser()
    state_home = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    return state_home / "classifier-skill/calls.jsonl"


def log_call(args: argparse.Namespace, note: dict[str, str], template: dict[str, Any], stats: dict[str, Any]) -> None:
    """Append one metadata line per invocation for re-tuning. Never records state or answers."""
    path = log_path()
    if path is None:
        return
    questions = template["questions"]
    shape = {k: v for k, v in template.items() if k != "state"}
    route = getattr(args, "route_log", None)
    route = route if isinstance(route, dict) else {}
    record = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "provider": args.provider, **route, **note, "reshape_noted": bool(note),
        "questions": {qid: q["type"] for qid, q in questions.items()},
        "options": {qid: len(q["criteria"]) for qid, q in questions.items() if q["type"] != "noul"},
        "request_sha256": hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()[:16],
        **stats,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as exc:
        print(f"classifier-skill: could not write call log {path}: {exc}", file=sys.stderr)


def failure_kind(exc: CallError) -> str:
    """A short, content-free label for the call log: "exhausted", the HTTP status, or "transport"."""
    if isinstance(exc, Exhausted):
        return "exhausted"
    status = getattr(exc.__cause__, "code", None) or exc.status
    return f"http_{status}" if isinstance(status, int) else "transport"


def usage_tokens(response: Any) -> int:
    usage = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        return 0
    try:
        return max(0, int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def run_batch(template: dict[str, Any], batch_path: str, args: argparse.Namespace,
              note: dict[str, str] | None = None) -> None:
    """Apply one template to every JSONL line (each line is a state). One output line per input line."""
    try:
        lines = Path(batch_path).expanduser().read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        fail(f"could not read batch file: {exc}")
    states, redacted = [], 0
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            state = json.loads(line)
        except json.JSONDecodeError as exc:
            fail(f"batch line {number} is not JSON: {exc}")
        validate_state(state, f"batch line {number}")
        if not args.no_redact:
            state, n = redact(state)
            redacted += n
        item_payload = {**template, "state": state}
        for provider, pinned in args.router.plan["steps"]:
            step_payload = {**item_payload, "model": pinned or item_payload["model"]}
            check_size(step_payload, f"batch line {number}", provider)
            check_provider_limits(step_payload, provider, f"batch line {number}")
        states.append((number, state))
    if not states:
        fail("batch file has no states")

    if redacted:
        print(f"classifier-skill: redacted {redacted} secret value(s) from state before sending", file=sys.stderr)
    total_cost, flagged, invalid, tokens, models = 0.0, 0, 0, 0, set()
    flags_by_question: dict[str, int] = {}
    started = time.monotonic()
    stopped: tuple[int, str, str] | None = None  # (line, message, kind) when a request fails after its retries
    done = 0
    for number, state in states:
        payload = {**template, "state": state}
        if args.dry_run:
            record: dict[str, Any] = {"line": number, "payload": payload}
        else:
            try:
                response = args.router.send(payload)
            except CallError as exc:
                stopped = (number, exc.message, failure_kind(exc))
                break
            total_cost += response_cost(response)
            tokens += usage_tokens(response)
            errors = response_errors(response, template["questions"])
            if errors:
                invalid += 1
                record = {"line": number, "state": state, "invalid": errors,
                          "model": response.get("model") if isinstance(response, dict) else None}
            else:
                models.add(response.get("model"))
                review = review_flags(response.get("answers"), args.margin, response.get("model"),
                                      args.router.strict(response.get("model")))
                flagged += bool(review)
                for question_id in review:
                    flags_by_question[question_id] = flags_by_question.get(question_id, 0) + 1
                record = {"line": number, "state": state, "answers": response.get("answers"),
                          "review": review, "model": response.get("model"), "usage": response.get("usage")}
                if args.threshold is not None:
                    record["decisions"] = decisions(response.get("answers"), review, args.threshold)
        print(json.dumps(record, ensure_ascii=False, sort_keys=True), flush=True)
        done += 1
    if not args.dry_run:
        args.provider, args.route_log = args.router.provider, args.router.log_fields()
        estimate = args.router.cost_estimated
        parts = ([f"cost ${total_cost:.6f}"] if total_cost else []) + \
            ([f"estimated ${estimate:.6f} from the profile price"] if estimate else [])
        cost = ", ".join(parts) or "cost not reported by provider"
        print(f"classifier-skill: {len(states)} states, {done - invalid} answered, {flagged} flagged for review, "
              f"{invalid} invalid, {cost}", file=sys.stderr)
        if stopped:
            print(f"classifier-skill: stopped at batch line {stopped[0]}; lines from there on were not sent: "
                  f"{stopped[1]}", file=sys.stderr)
        if flags_by_question:
            per_question = ", ".join(f"{q} {n}" for q, n in sorted(flags_by_question.items()))
            print(f"classifier-skill: flags per question: {per_question}", file=sys.stderr)
        log_call(args, note or {}, template, {
            "mode": "batch", "items": len(states), "flagged": flagged, "invalid": invalid,
            "flags_by_question": flags_by_question, "redacted": redacted,
            "input_tokens": tokens, "cost": round(total_cost, 8) or None,
            "model": sorted(m for m in models if m), "seconds": round(time.monotonic() - started, 2),
            **({"stopped_at_line": stopped[0], "answered": done, "failed": stopped[2]} if stopped else {})})
        if stopped:
            raise SystemExit(1)
        if invalid:
            raise SystemExit(3)


PROBE_STATE = "The sky is blue."
PROBE_QUESTION = {"type": "noul", "instructions": "Does the text say the sky is blue?"}
PROBE_OPTION_SIZES = (26, 64, 128)
PROBE_REFUSED_STATUSES = {400, 413, 422}  # the provider rejected the request itself: evidence of an option cap


def probe_target(target: str, model: str | None) -> tuple[str, str, str | None]:
    """(provider, model, model profile id) for --probe: a model profile id, or a provider name plus --model or its
    default model."""
    if target in MODELS:
        spec = MODELS[target]
        if spec.get("match", "exact") == "exact":
            if model and model != spec["model"]:
                fail(f"--probe {target} is the model {spec['model']!r}; drop --model or probe the provider instead")
            return spec["provider"], spec["model"], target
        default = PROVIDERS[spec["provider"]].get("model")
        chosen = model or default
        if not chosen or (model_profile(spec["provider"], chosen) or (None,))[0] != target:
            fail(f"--probe {target} covers {spec['model']}:<tag> models; pass --model with one")
        return spec["provider"], chosen, target
    if target not in PROVIDERS:
        fail(f"--probe takes a model profile id ({', '.join(MODELS)}) or a provider ({', '.join(PROVIDERS)})")
    spec = PROVIDERS[target]
    chosen = model or spec.get("model") or os.environ.get(spec.get("model_env", ""), "").strip()
    if not chosen:
        fail(f"--probe {target} needs --model")
    found = model_profile(target, chosen)
    return target, chosen, found[0] if found else None


def probe_reply(response: Any, questions: dict[str, Any], provider: str, model: str) -> dict[str, Any]:
    """What a probe learned from one reply: wrapper, model id, usage fields, cost, and answer errors."""
    keys = sorted(response) if isinstance(response, dict) else []
    wrapper = "none" if "answers" in keys else "result" if isinstance(
        response, dict) and isinstance(response.get("result"), dict) else "unknown"
    reply_model = response.get("model") if isinstance(response, dict) else None
    usage = response.get("usage") if isinstance(response, dict) else None
    return {"top_level_keys": keys, "wrapper": wrapper, "model": reply_model,
            "model_known": bool((found := model_profile(provider, model)) and reply_matches(found[1], reply_model)),
            "usage_fields": sorted(usage) if isinstance(usage, dict) else None,
            "cost_reported": isinstance(usage, dict) and "cost" in usage, "cost": response_cost(response),
            "errors": response_errors(response, questions)}


def probe_patch(provider: str, model: str, profile_id: str | None, reply_model: str | None,
                options: int | None) -> dict[str, Any]:
    """Fields to merge into one model entry, for the person to review: only reply ids and the option limit, never
    a provider's host, endpoint, or credentials. A model with no profile gets a new user/ entry. Empty unless a
    call answered (`reply_model` comes only from answered calls)."""
    if not reply_model:
        return {}
    changes: dict[str, Any] = {}
    if profile_id is None:
        profile_id = "user/" + re.sub(r"[^a-z0-9._-]+", "-", f"{provider}-{model}".lower()).strip("-")
        changes = {"provider": provider, "model": model, "calibration": "uncalibrated", "reply_models": [reply_model]}
    elif not reply_matches(MODELS[profile_id], reply_model):
        changes["reply_models"] = MODELS[profile_id]["reply_models"] + [reply_model]
    if options is not None:
        changes["limits"] = {**MODELS.get(profile_id, {}).get("limits", {}), "options": options,
                             "_options_probed": f"{datetime.date.today().isoformat()} with {model}"}
    return {"models": {profile_id: changes}} if changes else {}


def safe_endpoint(url: str) -> str:
    """The endpoint for a printed report: scheme, host, port, and path only, so a query-string key or user info
    never reaches stdout. (A filled path placeholder, such as an account id, is shown; it is not a credential.)"""
    parts = urlparse(url)
    try:
        port = parts.port
    except ValueError:
        port = None
    return f"{parts.scheme}://{parts.hostname or ''}{f':{port}' if port else ''}{parts.path}"


def run_probe(args: argparse.Namespace) -> None:
    """--probe: one tiny call (or, with --options, up to three option-cap calls) through the normal send path, so
    the endpoint check, key-to-host rule, https, and no-redirect rules all apply. Prints a report; writes nothing."""
    provider, model, profile_id = probe_target(args.probe, args.model)
    plan = {"steps": [(provider, model)], "host": "other", "host_reason": "probe", "route": None}
    router = Router(plan, args.endpoint, args.timeout)
    endpoint = router.current_endpoint()
    check_endpoint(provider, endpoint)
    if args.local_only and not is_local(endpoint):
        fail(f"--local-only: refusing to send to {safe_endpoint(endpoint)!r}, which is not on this machine", code=1)
    sizes = PROBE_OPTION_SIZES if args.options else (None,)
    requests = []
    for size in sizes:
        question = PROBE_QUESTION if size is None else {
            "type": "choice", "instructions": "Which option is listed first?",
            "criteria": {f"o{i}": f"option {i}" for i in range(1, size + 1)}}
        payload = {"model": model, "state": PROBE_STATE, "questions": {"probe": question}}
        check_size(payload, "probe", provider)
        check_provider_limits(payload, provider, "probe", {"options": size} if size else None)
        requests.append((size, payload))
    report: dict[str, Any] = {"probe": args.probe, "provider": provider, "model": model, "profile": profile_id,
                              "endpoint": safe_endpoint(endpoint)}
    if args.dry_run:
        report["requests"] = [to_openai_request(p) if PROVIDERS[provider].get("shape") == "openai" else p
                              for _, p in requests]
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return
    results, largest, reply_model, total_cost, inconclusive = {}, None, None, 0.0, False
    started = time.monotonic()
    for size, payload in requests:
        call_started = time.monotonic()
        try:
            response = router.send(payload)
        except CallError as exc:
            # Only a request the provider refused says anything about an option cap; credit, rate, server,
            # network, and redirect failures leave the cap unknown.
            outcome: dict[str, Any] = {"ok": False, "error": redact(exc.message)[0]}
            if size is None or exc.status not in PROBE_REFUSED_STATUSES:
                inconclusive = outcome["inconclusive"] = True
        else:
            outcome = {"ok": True, **probe_reply(response, payload["questions"], provider, model)}
            outcome["ok"] = not outcome["errors"]
            total_cost += outcome["cost"]
            if outcome["ok"]:
                reply_model = reply_model or outcome["model"]
        outcome["seconds"] = round(time.monotonic() - call_started, 2)
        results["single" if size is None else str(size)] = outcome
        if not outcome["ok"]:
            break
        largest = size
    report["calls"] = results
    report["cost"] = round(total_cost, 8)
    report["cost_estimated"] = round(router.cost_estimated, 8) or None
    if args.options:
        report["largest_options_ok"] = largest
    report["suggested_patch"] = probe_patch(provider, model, profile_id, reply_model,
                                            largest if args.options and not inconclusive else None)
    args.provider, args.route_log = router.provider, router.log_fields()
    log_call(args, {}, {"questions": {"probe": PROBE_QUESTION}}, {
        "mode": "probe", "items": len(results), "invalid": sum(not r["ok"] for r in results.values()),
        "cost": round(total_cost, 8) or None, "model": [reply_model] if reply_model else [],
        "seconds": round(time.monotonic() - started, 2)})
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    if inconclusive or not largest and not all(r["ok"] for r in results.values()):
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-file", default="-", help="JSON request file, or - for stdin")
    parser.add_argument("--provider", choices=sorted(PROVIDERS),
                        help="openrouter, typesafe, openai, ollama, or compatible (default: CLASSIFIER_PROVIDER; "
                             "inside Codex or with CLASSIFIER_ROUTE, the OpenAI route chain; else whichever Jev API "
                             "key is set; local providers are never chosen automatically)")
    parser.add_argument("--model", help="override the model (default: the provider's pinned Jev version, or "
                                        "nimble:9b for Ollama, or CLASSIFIER_COMPATIBLE_MODEL for compatible)")
    parser.add_argument("--local-only", action="store_true",
                        help="refuse to send unless the endpoint is on this machine (localhost)")
    parser.add_argument("--endpoint", help="override the provider's endpoint (must stay on that provider's host)")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true", help="validate and print the payload without sending it")
    parser.add_argument("--batch", metavar="STATES.jsonl",
                        help="apply the request (without state) to each JSONL line; one JSON result per line")
    parser.add_argument("--margin", type=float, default=0.2,
                        help="flag a choice for review when the top two probabilities are closer than this")
    parser.add_argument("--threshold", type=float, metavar="T",
                        help="add a decisions object: act / skip / human per question at confidence T (0.5-1)")
    parser.add_argument("--no-redact", action="store_true",
                        help="send state as given, without scrubbing secrets (tokens, keys, passwords) first")
    parser.add_argument("--probe", metavar="PROFILE",
                        help="send one tiny request to a model profile id or provider and report the reply's shape, "
                             "model id, usage fields, and cost, with a suggested profile patch; writes nothing")
    parser.add_argument("--options", action="store_true",
                        help="with --probe: find the option cap with up to three calls (26, 64, 128 options)")
    parser.add_argument("--contract-version", action="store_true",
                        help="print the caller contract version (references/callers.md) and exit")
    args = parser.parse_args()
    if args.contract_version:
        print(CONTRACT_VERSION)
        return

    if args.options and not args.probe:
        fail("--options needs --probe")
    if args.probe and args.provider:
        fail("--probe picks the provider; drop --provider")
    if args.probe:
        run_probe(args)
        return
    if args.threshold is not None and not 0.5 <= args.threshold <= 1:
        fail("--threshold must be between 0.5 and 1")
    plan = plan_route(args.provider, args.model, args.dry_run)
    args.router = Router(plan, args.endpoint, args.timeout)
    args.provider = args.router.provider
    args.route_log = args.router.log_fields()
    spec = PROVIDERS[args.provider]
    args.endpoint = args.router.current_endpoint()
    if spec.get("loopback_only"):
        check_endpoint(args.provider, args.endpoint)
    for _, endpoint in args.router.endpoints():
        if args.local_only and not is_local(endpoint):
            fail(f"--local-only: refusing to send to {endpoint!r}, which is not on this machine", code=1)
    default_model = plan["steps"][0][1] or spec.get("model") or os.environ.get(spec.get("model_env", ""), "").strip()
    payload = normalize_request(load_request(args.request_file), args.model, batch=bool(args.batch),
                                default_model=default_model)
    note = take_reshape(payload)
    redacted = 0
    if "state" in payload:
        if not args.no_redact:
            payload["state"], redacted = redact(payload["state"])
            if redacted:
                print(f"classifier-skill: redacted {redacted} secret value(s) from state before sending",
                      file=sys.stderr)
    for provider, pinned in plan["steps"]:
        step_payload = {**payload, "model": pinned or payload["model"]}
        note_unprofiled(provider, step_payload["model"])
        if "state" in payload:
            check_size(step_payload, provider=provider)
        check_provider_limits(step_payload, provider)
    accepted = set.intersection(*(set(PROVIDERS[p].get("fields", ALLOWED_FIELDS)) for p, _ in plan["steps"]))
    extras = sorted(set(payload) - accepted)
    if extras:
        fail(f"{', '.join(extras)} {'is' if len(extras) == 1 else 'are'} OpenRouter-only; "
             f"remove {'it' if len(extras) == 1 else 'them'} for provider {args.provider}")
    if args.batch:
        run_batch(payload, args.batch, args, note)
        return
    if args.dry_run:
        result = payload
    else:
        started = time.monotonic()
        try:
            result = args.router.send(payload)
        except CallError as exc:
            args.provider, args.route_log = args.router.provider, args.router.log_fields()
            log_call(args, note, payload, {  # a failed call is logged too, so the report shows failure rates
                "mode": "single", "items": 1, "flagged": 0, "invalid": 0, "redacted": redacted,
                "failed": failure_kind(exc), "seconds": round(time.monotonic() - started, 2)})
            fail(exc.message, code=1)
        args.provider, args.route_log = args.router.provider, args.router.log_fields()
        errors = response_errors(result, payload["questions"])
        review = {} if errors else review_flags(result.get("answers"), args.margin, result.get("model"),
                                                args.router.strict(result.get("model")))
        log_call(args, note, payload, {
            "mode": "single", "items": 1, "flagged": int(bool(review)), "invalid": int(bool(errors)),
            "redacted": redacted,
            "input_tokens": usage_tokens(result), "cost": response_cost(result) or None,
            "model": [result.get("model")] if isinstance(result, dict) else [], "seconds": round(time.monotonic() - started, 2)})
        if errors:
            json.dump({"invalid": errors, "response": result}, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
            fail("invalid classifier response; do not act on it", code=3)
        result["review"] = review
        if args.threshold is not None:
            result["decisions"] = decisions(result.get("answers"), review, args.threshold)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
