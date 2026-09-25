#!/usr/bin/env python3
"""Call TypeSafe Jev, through OpenRouter or TypeSafe's own System One API, once or over a JSONL batch of states.

Every response is checked against the questions asked before it is printed; a malformed answer exits 3 (or, in
batch mode, marks that line "invalid") so callers never act on it. Every response also gains a top-level "review"
object: question id -> reasons a human should check the answer (fallback option chosen, close runner-up, noul near
0.5, low score confidence). Empty means no flags."""

from __future__ import annotations

import argparse
import datetime
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
}
MAX_RETRY_AFTER = 10.0  # seconds; a longer retry-after is reported rather than waited out
FALLBACK_OPTIONS = {"insufficient_context", "insufficient_evidence", "none_fit"}
MAX_OPTIONS = 250  # larger option sets must be pre-filtered or split in code
RETRY_STATUSES = {429, 500, 502, 503, 504, 529}  # a classification call has no side effects, so retrying is safe
RETRY_DELAYS = (0.5, 1.0)
ALLOWED_FIELDS = {"model", "state", "questions", "provider", "trace", "session_id", "user"}
RESHAPE_FIELDS = {"task", "recipe", "offloaded", "kept_for_llm", "caller"}  # local-only notes, never sent
# The interface other skills may rely on, documented in references/callers.md. Bump the major version on any
# change that could break a caller; callers skip their classifier step when the major version differs.
CONTRACT_VERSION = "1.0"


def fail(message: str, code: int = 2) -> NoReturn:
    print(f"classifier-skill: {message}", file=sys.stderr)
    raise SystemExit(code)


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
        if not all(isinstance(v, str) and v.strip() or isinstance(v, dict) and v for v in criteria.values()):
            fail(f"choice question {question_id!r} options need a description string or object")
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
        if os.environ.get(spec["key"], "").strip():
            return candidate
    return "openrouter"


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
        fail("model must be a non-empty string")
    return payload


def call_jev(payload: dict[str, Any], endpoint: str, timeout: float, provider: str = "openrouter") -> dict[str, Any]:
    spec = PROVIDERS[provider]
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or parsed.hostname != spec["host"]:
        fail(f"refusing to send credentials to {endpoint!r}; {provider} only allows https://{spec['host']}", code=1)
    api_key = os.environ.get(spec["key"], "").strip()
    if not api_key:
        fail(f"{spec['key']} is not available for provider {provider}", code=1)
    service = spec["name"]

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "classifier-skill/1.0",
        },
        method="POST",
    )
    for attempt in range(len(RETRY_DELAYS) + 1):
        retry = attempt < len(RETRY_DELAYS)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if retry and exc.code in RETRY_STATUSES:
                time.sleep(retry_delay(exc, RETRY_DELAYS[attempt]))
                continue
            body = exc.read().decode("utf-8", errors="replace")
            fail(f"{service} returned HTTP {exc.code}: {body}", code=1)
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            if retry:
                time.sleep(RETRY_DELAYS[attempt])
                continue
            fail(f"{service} request failed: {exc}", code=1)
        except json.JSONDecodeError as exc:
            fail(f"{service} returned non-JSON: {exc}", code=1)
    raise AssertionError("unreachable")


def retry_delay(exc: urllib.error.HTTPError, default: float) -> float:
    """Honor a short numeric retry-after header; otherwise use the default backoff."""
    try:
        value = float((exc.headers or {}).get("retry-after", ""))
    except (TypeError, ValueError):
        return default
    return value if 0 <= value <= MAX_RETRY_AFTER else default


def _unit(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def _distribution(probabilities: Any, keys: set[str]) -> bool:
    return (isinstance(probabilities, dict) and set(probabilities) == keys
            and all(_unit(p) for p in probabilities.values()) and abs(sum(probabilities.values()) - 1) < 0.02)


def answer_errors(answers: Any, questions: dict[str, Any]) -> list[str]:
    """Structural problems that make a response unsafe to act on. Empty means every asked question is well formed."""
    if not isinstance(answers, dict):
        return ["response has no answers object"]
    errors = []
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


def usage_tokens(response: dict[str, Any]) -> int:
    usage = response.get("usage") or {}
    return int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)


def run_batch(template: dict[str, Any], batch_path: str, args: argparse.Namespace,
              note: dict[str, str] | None = None) -> None:
    """Apply one template to every JSONL line (each line is a state). One output line per input line."""
    try:
        lines = Path(batch_path).expanduser().read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        fail(f"could not read batch file: {exc}")
    states = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            state = json.loads(line)
        except json.JSONDecodeError as exc:
            fail(f"batch line {number} is not JSON: {exc}")
        validate_state(state, f"batch line {number}")
        states.append((number, state))
    if not states:
        fail("batch file has no states")

    total_cost, flagged, invalid, tokens, models = 0.0, 0, 0, 0, set()
    started = time.monotonic()
    for number, state in states:
        payload = {**template, "state": state}
        if args.dry_run:
            record: dict[str, Any] = {"line": number, "payload": payload}
        else:
            response = call_jev(payload, args.endpoint, args.timeout, args.provider)
            total_cost += float((response.get("usage") or {}).get("cost") or 0)
            tokens += usage_tokens(response)
            models.add(response.get("model"))
            errors = answer_errors(response.get("answers"), template["questions"])
            if errors:
                invalid += 1
                record = {"line": number, "state": state, "invalid": errors, "model": response.get("model")}
            else:
                review = review_flags(response.get("answers"), args.margin)
                flagged += bool(review)
                record = {"line": number, "state": state, "answers": response.get("answers"),
                          "review": review, "model": response.get("model"), "usage": response.get("usage")}
        print(json.dumps(record, ensure_ascii=False, sort_keys=True), flush=True)
    if not args.dry_run:
        cost = f"cost ${total_cost:.6f}" if total_cost else "cost not reported by provider"
        print(f"classifier-skill: {len(states)} states, {flagged} flagged for review, {invalid} invalid, {cost}",
              file=sys.stderr)
        log_call(args, note or {}, template, {
            "mode": "batch", "items": len(states), "flagged": flagged, "invalid": invalid,
            "input_tokens": tokens, "cost": round(total_cost, 8) or None,
            "model": sorted(m for m in models if m), "seconds": round(time.monotonic() - started, 2)})
        if invalid:
            raise SystemExit(3)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-file", default="-", help="JSON request file, or - for stdin")
    parser.add_argument("--provider", choices=sorted(PROVIDERS),
                        help="openrouter or typesafe (default: CLASSIFIER_PROVIDER, else whichever API key is set)")
    parser.add_argument("--model", help="override the model (default: the provider's pinned Jev version)")
    parser.add_argument("--endpoint", help="override the provider's endpoint (must stay on that provider's host)")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true", help="validate and print the payload without sending it")
    parser.add_argument("--batch", metavar="STATES.jsonl",
                        help="apply the request (without state) to each JSONL line; one JSON result per line")
    parser.add_argument("--margin", type=float, default=0.2,
                        help="flag a choice for review when the top two probabilities are closer than this")
    parser.add_argument("--contract-version", action="store_true",
                        help="print the caller contract version (references/callers.md) and exit")
    args = parser.parse_args()
    if args.contract_version:
        print(CONTRACT_VERSION)
        return

    args.provider = select_provider(args.provider)
    spec = PROVIDERS[args.provider]
    if not args.endpoint:
        override = os.environ.get("OPENROUTER_DECISIONS_URL") if args.provider == "openrouter" else None
        args.endpoint = override or spec["endpoint"]
    payload = normalize_request(load_request(args.request_file), args.model, batch=bool(args.batch),
                                default_model=spec["model"])
    note = take_reshape(payload)
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
        result = call_jev(payload, args.endpoint, args.timeout, args.provider)
        errors = answer_errors(result.get("answers"), payload["questions"])
        review = {} if errors else review_flags(result.get("answers"), args.margin)
        log_call(args, note, payload, {
            "mode": "single", "items": 1, "flagged": int(bool(review)), "invalid": int(bool(errors)),
            "input_tokens": usage_tokens(result), "cost": (result.get("usage") or {}).get("cost"),
            "model": [result.get("model")], "seconds": round(time.monotonic() - started, 2)})
        if errors:
            json.dump({"invalid": errors, "response": result}, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
            fail("invalid classifier response; do not act on it", code=3)
        result["review"] = review
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
