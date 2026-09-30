#!/usr/bin/env python3
"""Call a typed decision model through OpenRouter, TypeSafe, Ollama, or a compatible System One API.

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

# Each provider's key is only ever sent to that provider's host. Default models are pinned versions, not aliases,
# so answers stay comparable; pass --model to change.
PROVIDERS = {
    "openrouter": {"endpoint": "https://openrouter.ai/api/alpha/decisions", "host": "openrouter.ai",
                   "key": "OPENROUTER_API_KEY", "model": "typesafe/jev-1.13", "name": "OpenRouter"},
    "typesafe": {"endpoint": "https://api.typesafe.ai/v1/systemone", "host": "api.typesafe.ai",
                 "key": "TYPESAFE_API_KEY", "model": "jev-1.13.0", "name": "TypeSafe",
                 "fields": {"model", "state", "questions"}},  # System One accepts no routing extras
    "ollama": {"endpoint": "http://127.0.0.1:11434/v1/systemone", "model": "nimble:9b", "name": "Ollama",
               "key_optional": True, "explicit": True, "loopback_only": True,
               "fields": {"model", "state", "questions"}},
    # Any server that speaks the same request and answer shapes (references/providers.md): a self-hosted open
    # model, a Hugging Face Inference Endpoint, or another hosted API. Configured by environment, chosen only
    # explicitly, and its optional key is only ever sent to the configured URL's host.
    "compatible": {"url_env": "CLASSIFIER_COMPATIBLE_URL", "key": "CLASSIFIER_COMPATIBLE_KEY",
                   "model_env": "CLASSIFIER_COMPATIBLE_MODEL", "name": "compatible server",
                   "key_optional": True, "explicit": True, "fields": {"model", "state", "questions"}},
}
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}  # the only hosts plain http and --local-only accept
MAX_RETRY_AFTER = 10.0  # seconds; a longer retry-after ends the call with an error instead of being waited out
FALLBACK_OPTIONS = {"insufficient_context", "insufficient_evidence", "none_fit"}
MAX_OPTIONS = 250  # larger option sets must be pre-filtered or split in code
OLLAMA_MAX_QUESTIONS, OLLAMA_MAX_OPTIONS, OLLAMA_MAX_BODY_BYTES = 64, 26, 64 * 1024
NIMBLE_MAX_TOKENS = 8_192
RETRY_STATUSES = {429, 500, 502, 503, 504, 529}  # a classification call has no side effects, so retrying is safe
RETRY_DELAYS = (0.5, 1.0)
ALLOWED_FIELDS = {"model", "state", "questions", "provider", "trace", "session_id", "user"}
RESHAPE_FIELDS = {"task", "recipe", "offloaded", "kept_for_llm", "caller"}  # local-only notes, never sent
# jev-1.13 limits (TypeSafe models page, 2026-09-25): 64k tokens per request, 32k for state plus the longest
# question. Tokens are estimated (4 ASCII characters each, 1.5 per other character), so a refusal near the limit
# is approximate.
MAX_REQUEST_TOKENS, MAX_STATE_QUESTION_TOKENS, CHARS_PER_TOKEN, NON_ASCII_TOKENS_PER_CHAR = 64_000, 32_000, 4, 1.5
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
CONTRACT_VERSION = "1.6"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """A decision API never redirects. Following one would re-send the Authorization header to whatever host the
    redirect names, so a 3xx surfaces as an HTTP error instead."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


urllib.request.install_opener(urllib.request.build_opener(NoRedirect))


def fail(message: str, code: int = 2) -> NoReturn:
    print(f"classifier-skill: {message}", file=sys.stderr)
    raise SystemExit(code)


class CallError(SystemExit):
    """A provider request failed after its retries (HTTP error, network, non-JSON reply). Exits 1 if uncaught;
    catchers print `message`, so a batch can keep the answers it already has."""

    def __init__(self, message: str):
        super().__init__(1)
        self.message = message


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
    for candidate, spec in PROVIDERS.items():
        if not spec.get("explicit") and os.environ.get(spec["key"], "").strip():
            return candidate
    return "openrouter"


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
            if isinstance(item, str) and item and SECRET_KEY.fullmatch(str(key).strip()):
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


def check_size(payload: dict[str, Any], where: str = "request") -> None:
    """Refuse, before sending, a request over jev-1.13's context limits. Sizes the payload as it will be sent."""
    near = max(estimated_tokens({"state": payload["state"], "question": {qid: q}})
               for qid, q in payload["questions"].items())
    total = estimated_tokens(payload)
    if near > MAX_STATE_QUESTION_TOKENS or total > MAX_REQUEST_TOKENS:
        fail(f"{where} is about {near:,} tokens of state plus its longest question and {total:,} in all; the limits "
             f"are {MAX_STATE_QUESTION_TOKENS:,} and {MAX_REQUEST_TOKENS:,}. Trim state or split the questions")


def check_provider_limits(payload: dict[str, Any], provider: str, where: str = "request") -> None:
    """Refuse requests that exceed a provider's documented limits before any data is sent."""
    if provider != "ollama":
        return
    questions = payload["questions"]
    if len(questions) > OLLAMA_MAX_QUESTIONS:
        fail(f"{where} has {len(questions)} questions; Ollama System One allows {OLLAMA_MAX_QUESTIONS}")
    for question_id, question in questions.items():
        if question["type"] in {"choice", "score"} and len(question["criteria"]) > OLLAMA_MAX_OPTIONS:
            fail(f"{where} question {question_id!r} has {len(question['criteria'])} options; "
                 f"Ollama System One allows {OLLAMA_MAX_OPTIONS}")
        if question["type"] == "choice" and not all(
                description is None or isinstance(description, str)
                for description in question["criteria"].values()):
            fail(f"{where} question {question_id!r} has structured choice descriptions; "
                 "Ollama System One allows only strings or null")
        if question["type"] == "noul" and question.get("criteria") is not None and not all(
                isinstance(description, str) for description in question["criteria"].values()):
            fail(f"{where} question {question_id!r} has structured noul descriptions; "
                 "Ollama System One allows only strings")
    if "state" not in payload:
        return
    body_bytes = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))  # exactly as call_jev sends it
    if body_bytes > OLLAMA_MAX_BODY_BYTES:
        fail(f"{where} is {body_bytes:,} bytes; Ollama System One allows {OLLAMA_MAX_BODY_BYTES:,}. "
             "Trim state or split the questions")
    if payload.get("model", "").split(":", 1)[0] == "nimble":
        total = estimated_tokens(payload)
        if total > NIMBLE_MAX_TOKENS:
            fail(f"{where} is about {total:,} tokens; Nimble's context limit is {NIMBLE_MAX_TOKENS:,}. "
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
        override = os.environ.get("OPENROUTER_DECISIONS_URL") if provider == "openrouter" else None
        return override or spec["endpoint"]
    url = os.environ.get(spec["url_env"], "").strip()
    if not url:
        fail(f"{spec['url_env']} is not set; provider {provider} needs the server's full request URL", code=1)
    return url


def check_endpoint(provider: str, endpoint: str) -> None:
    """Refuse any endpoint off the provider's host. Plain http is allowed only for a server on this machine."""
    spec = PROVIDERS[provider]
    parsed = urlparse(endpoint)
    if spec.get("loopback_only"):
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in LOOPBACK_HOSTS:
            fail(f"refusing Ollama endpoint {endpoint!r}; provider ollama only allows localhost", code=1)
        return
    host = spec.get("host") or urlparse(provider_endpoint(provider)).hostname
    local_http = parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS and "url_env" in spec
    if parsed.hostname != host or not (parsed.scheme == "https" or local_http):
        allowed = f"https://{host}" + (" (or http on localhost)" if "url_env" in spec else "")
        fail(f"refusing to send credentials to {endpoint!r}; {provider} only allows {allowed}", code=1)


def is_local(endpoint: str) -> bool:
    return urlparse(endpoint).hostname in LOOPBACK_HOSTS


def call_jev(payload: dict[str, Any], endpoint: str, timeout: float, provider: str = "openrouter") -> dict[str, Any]:
    spec = PROVIDERS[provider]
    check_endpoint(provider, endpoint)
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
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    for attempt in range(len(RETRY_DELAYS) + 1):
        retry = attempt < len(RETRY_DELAYS)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            asked = retry_after(exc)
            if retry and exc.code in RETRY_STATUSES and (asked is None or asked <= MAX_RETRY_AFTER):
                exc.close()
                time.sleep(RETRY_DELAYS[attempt] if asked is None else asked)
                continue
            body = exc.read().decode("utf-8", errors="replace")
            exc.close()
            wait = f" (asked to retry after {asked:.0f}s, longer than the {MAX_RETRY_AFTER:.0f}s this script waits)" \
                if asked is not None and asked > MAX_RETRY_AFTER and exc.code in RETRY_STATUSES else ""
            raise CallError(f"{service} returned HTTP {exc.code}{wait}: {body}") from exc
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            if retry:
                time.sleep(RETRY_DELAYS[attempt])
                continue
            raise CallError(f"{service} request failed: {exc}") from exc
        except ValueError as exc:  # JSONDecodeError, or bytes that are not valid text at all
            raise CallError(f"{service} returned non-JSON: {exc}") from exc
    raise AssertionError("unreachable")


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
        if not isinstance(answer, dict) or answer.get("type") != kind:
            errors.append(f"{question_id}: missing or wrong-type answer")
        elif kind == "choice":
            options, probabilities = set(question["criteria"]), answer.get("probabilities")
            if (answer.get("choice") not in options or not _distribution(probabilities, options)
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
                    or not _unit(answer.get("confidence"))):
                errors.append(f"{question_id}: score answer is outside the rubric")
    return errors


def response_errors(response: Any, questions: dict[str, Any]) -> list[str]:
    """The one gate every caller uses before touching a response: its shape, its usage, and its answers."""
    if not isinstance(response, dict):
        return ["response is not a JSON object"]
    if not isinstance(response.get("usage"), (dict, type(None))):
        return ["response usage is not an object"]
    return answer_errors(response.get("answers"), questions)


def response_cost(response: dict[str, Any]) -> float:
    """The reported cost in dollars; 0 when absent or unreadable (an odd cost is not a reason to drop an answer)."""
    try:
        return float((response.get("usage") or {}).get("cost") or 0)
    except (TypeError, ValueError):
        return 0.0


def review_flags(answers: dict[str, Any], margin: float) -> dict[str, list[str]]:
    """Deterministic reasons to route an answer to a human; see the module docstring."""
    flags: dict[str, list[str]] = {}
    for question_id, answer in (answers or {}).items():
        reasons = []
        kind = answer.get("type")
        if kind == "choice":
            if answer.get("choice") in FALLBACK_OPTIONS:
                reasons.append(f"fallback option {answer['choice']!r} chosen")
            ranked = sorted((answer.get("probabilities") or {}).values(), reverse=True)
            if len(ranked) > 1 and ranked[0] - ranked[1] < margin:
                reasons.append(f"runner-up within {ranked[0] - ranked[1]:.2f}")
        elif kind == "noul" and isinstance(answer.get("noul"), (int, float)) and 0.35 < answer["noul"] < 0.65:
            reasons.append(f"noul {answer['noul']:.2f} is near 0.5")
        elif kind == "score" and isinstance(answer.get("confidence"), (int, float)) and answer["confidence"] < 0.5:
            reasons.append(f"score confidence {answer['confidence']:.2f} below 0.5")
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
    recipes = known_recipes()
    recipe = note.get("recipe", "custom").strip()
    if recipe != "custom" and recipes is not None and recipe not in recipes:
        note["recipe_text"] = recipe  # keep the free text, but group it under custom in the log
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
    record = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "provider": args.provider, **note, "reshape_noted": bool(note),
        "questions": {qid: q["type"] for qid, q in questions.items()},
        "options": {qid: len(q["criteria"]) for qid, q in questions.items() if q["type"] != "noul"},
        "request_sha256": hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()[:16],
        **stats,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as exc:
        print(f"classifier-skill: could not write call log {path}: {exc}", file=sys.stderr)


def failure_kind(exc: CallError) -> str:
    """A short, content-free label for the call log: the HTTP status, or "transport"."""
    status = getattr(exc.__cause__, "code", None)
    return f"http_{status}" if isinstance(status, int) else "transport"


def usage_tokens(response: dict[str, Any]) -> int:
    usage = response.get("usage") or {}
    try:
        return int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    except (TypeError, ValueError):
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
        check_size(item_payload, f"batch line {number}")
        check_provider_limits(item_payload, args.provider, f"batch line {number}")
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
                response = call_jev(payload, args.endpoint, args.timeout, args.provider)
            except CallError as exc:
                stopped = (number, exc.message, failure_kind(exc))
                break
            errors = response_errors(response, template["questions"])
            if errors:
                invalid += 1
                record = {"line": number, "state": state, "invalid": errors,
                          "model": response.get("model") if isinstance(response, dict) else None}
            else:
                total_cost += response_cost(response)
                tokens += usage_tokens(response)
                models.add(response.get("model"))
                review = review_flags(response.get("answers"), args.margin)
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
        cost = f"cost ${total_cost:.6f}" if total_cost else "cost not reported by provider"
        print(f"classifier-skill: {len(states)} states, {done} answered, {flagged} flagged for review, "
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-file", default="-", help="JSON request file, or - for stdin")
    parser.add_argument("--provider", choices=sorted(PROVIDERS),
                        help="openrouter, typesafe, ollama, or compatible (default: CLASSIFIER_PROVIDER, else "
                             "whichever Jev API key is set; local providers are never chosen automatically)")
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
    parser.add_argument("--contract-version", action="store_true",
                        help="print the caller contract version (references/callers.md) and exit")
    args = parser.parse_args()
    if args.contract_version:
        print(CONTRACT_VERSION)
        return

    if args.threshold is not None and not 0.5 <= args.threshold <= 1:
        fail("--threshold must be between 0.5 and 1")
    args.provider = select_provider(args.provider)
    spec = PROVIDERS[args.provider]
    args.endpoint = args.endpoint or provider_endpoint(args.provider)
    if spec.get("loopback_only"):
        check_endpoint(args.provider, args.endpoint)
    if args.local_only and not is_local(args.endpoint):
        fail(f"--local-only: refusing to send to {args.endpoint!r}, which is not on this machine", code=1)
    default_model = spec.get("model") or os.environ.get(spec.get("model_env", ""), "").strip()
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
        check_size(payload)
    check_provider_limits(payload, args.provider)
    extras = sorted(set(payload) - spec.get("fields", ALLOWED_FIELDS))
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
            result = call_jev(payload, args.endpoint, args.timeout, args.provider)
        except CallError as exc:
            log_call(args, note, payload, {  # a failed call is logged too, so the report shows failure rates
                "mode": "single", "items": 1, "flagged": 0, "invalid": 0, "redacted": redacted,
                "failed": failure_kind(exc), "seconds": round(time.monotonic() - started, 2)})
            fail(exc.message, code=1)
        errors = response_errors(result, payload["questions"])
        review = {} if errors else review_flags(result.get("answers"), args.margin)
        log_call(args, note, payload, {
            "mode": "single", "items": 1, "flagged": int(bool(review)), "invalid": int(bool(errors)),
            "redacted": redacted,
            "input_tokens": 0 if errors else usage_tokens(result), "cost": None if errors else response_cost(result) or None,
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
