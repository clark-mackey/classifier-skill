#!/usr/bin/env python3
"""Judge a list of items with a question sheet: items JSONL in, one stamped line per item out, plus a summary.

This is the engine behind the sheet procedure (references/sheets.md) and contract 1.11 (references/callers.md). A caller supplies
data only: a sheet (questions, which item fields go on each card, thresholds, data rule) and the items. Everything else
stays in here: cards, redaction, size limits, provider choice, retries, answer validation, and dispositions.

Every input item comes out exactly once, in input order, as either `answered` (answers plus a disposition per question:
answered, skip, or human) or `unanswered` with a reason code. The summary file is written last; a missing summary, or
`complete` false, means the run did not finish and the caller should judge the original list itself. The exit code is 0
whenever the summary was written, 2 for a bad sheet or items file, so a caller has one path: read the summary."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RECIPES = HERE.parent / "recipes"
_spec = importlib.util.spec_from_file_location("jev_decide", HERE / "jev_decide.py")
jev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jev)

DEFAULT_MIN_ITEMS, DEFAULT_THRESHOLD = 20, 0.8
SHEET_FIELDS = {"sheet", "version", "contract", "recipe", "data", "min_items", "recurring", "model", "fields",
                "context", "questions", "consumes", "margin"}
QUESTION_EXTRAS = {"threshold"}  # sheet-only keys stripped before a question is sent
NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
RECIPE_REF = re.compile(r"^([a-z0-9][a-z0-9-]*)@([1-9][0-9]*)$")
EXPECTED = {"below_min_items"}  # reasons that are a planned bypass, not a degraded run
ITEM_SCOPED = {"too_large"}  # a failed request that concerns only its own item; the run keeps sending
AUTH_STATUSES, BAD_REQUEST_STATUSES = {401, 403}, {400, 404, 422}


def load_recipe(ref: str) -> dict[str, Any]:
    """A generic, versioned question set shipped in recipes/<name>@<N>.json. Recipes hold no caller's rules."""
    if not isinstance(ref, str) or not RECIPE_REF.match(ref):
        jev.fail(f"recipe {ref!r} must look like name@N")
    path = RECIPES / f"{ref}.json"
    try:
        recipe = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        jev.fail(f"could not read recipe {ref}: {exc}")
    if not isinstance(recipe.get("questions"), dict) or not recipe["questions"]:
        jev.fail(f"recipe {ref} has no questions")
    return recipe


def load_sheet(path: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, float]]:
    """Validate a sheet; return it, the questions to send (recipe merged with the sheet's), and thresholds."""
    try:
        sheet = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        jev.fail(f"could not read sheet: {exc}")
    if not isinstance(sheet, dict):
        jev.fail("sheet must be a JSON object")
    unknown = sorted(set(sheet) - SHEET_FIELDS)
    if unknown:
        jev.fail(f"sheet has unknown fields: {', '.join(unknown)}")
    if not isinstance(sheet.get("sheet"), str) or not NAME.match(sheet["sheet"]):
        jev.fail("sheet needs a `sheet` name of lowercase letters, digits, and hyphens")
    if type(sheet.get("version")) is not int or sheet["version"] < 1:
        jev.fail("sheet needs an integer `version` of 1 or more")
    major = str(sheet.get("contract", "")).split(".")[0]
    if major != jev.CONTRACT_VERSION.split(".")[0]:
        jev.fail(f"sheet was written for contract {sheet.get('contract')!r}; this engine speaks {jev.CONTRACT_VERSION}")
    if sheet.get("data") not in {"cloud_ok", "local_only"}:
        jev.fail("sheet `data` must be cloud_ok or local_only")
    fields = sheet.get("fields")
    if (not isinstance(fields, dict) or not isinstance(fields.get("card"), list) or not fields["card"]
            or not all(isinstance(f, str) and f for f in fields["card"])):
        jev.fail("sheet `fields` needs a non-empty `card` list of item field names (and optionally `id`)")
    if not isinstance(fields.get("id", "id"), str):
        jev.fail("sheet `fields.id` must be a field name")
    min_items = sheet.get("min_items", DEFAULT_MIN_ITEMS)
    if type(min_items) is not int or min_items < 1:
        jev.fail("sheet `min_items` must be a positive integer")
    if not isinstance(sheet.get("recurring", False), bool):
        jev.fail("sheet `recurring` must be true or false")
    margin = sheet.get("margin", 0.2)
    if type(margin) not in (int, float) or not 0 <= margin <= 1:
        jev.fail("sheet `margin` must be a number from 0 to 1")
    pinned = sheet.get("model")
    if pinned is not None and not (isinstance(pinned, str) and pinned.strip() or isinstance(pinned, dict) and pinned
                                   and all(k in jev.PROVIDERS and isinstance(v, str) and v.strip()
                                           for k, v in pinned.items())):
        jev.fail(f"sheet `model` must be a model id, a profile id ({', '.join(jev.MODELS)}), or an object of "
                 f"provider -> model id ({', '.join(jev.PROVIDERS)})")
    if "context" in sheet and not (isinstance(sheet["context"], str) and sheet["context"].strip()):
        jev.fail("sheet `context` must be a non-empty string")

    questions: dict[str, dict[str, Any]] = {}
    if "recipe" in sheet:
        questions = {qid: dict(q) for qid, q in load_recipe(sheet["recipe"])["questions"].items()}
    own = sheet.get("questions", {})
    if not isinstance(own, dict):
        jev.fail("sheet `questions` must be an object")
    for qid, question in own.items():
        if not isinstance(question, dict):
            jev.fail(f"sheet question {qid!r} must be an object")
        questions[qid] = {**questions.get(qid, {}), **question}  # the sheet overrides a recipe question by id
    if not questions:
        jev.fail("sheet needs `questions`, a `recipe`, or both")

    thresholds, send = {}, {}
    for qid, question in questions.items():
        threshold = question.get("threshold", DEFAULT_THRESHOLD)
        if type(threshold) not in (int, float) or not 0.5 <= threshold <= 1:
            jev.fail(f"question {qid!r} threshold must be between 0.5 and 1")
        thresholds[qid] = float(threshold)
        send[qid] = {k: v for k, v in question.items() if k not in QUESTION_EXTRAS}
        jev.validate_question(qid, send[qid])
    return sheet, send, thresholds


def load_items(path: str, fields: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """(id, card) per non-blank line. Only the sheet's card fields are kept, so nothing else is ever sent. A card
    with none of those fields is kept empty and comes out unanswered (`empty_card`) rather than failing the list."""
    id_field = fields.get("id", "id")
    try:
        lines = Path(path).expanduser().read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        jev.fail(f"could not read items file: {exc}")
    items, seen = [], set()
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            jev.fail(f"items line {number} is not JSON: {exc}")
        if not isinstance(item, dict):
            jev.fail(f"items line {number} must be a JSON object")
        item_id = item.get(id_field)
        if item_id is None or not str(item_id).strip():
            jev.fail(f"items line {number} has no {id_field!r}")
        item_id = str(item_id)
        if item_id in seen:
            jev.fail(f"items line {number} repeats id {item_id!r}; ids must be unique")
        seen.add(item_id)
        items.append((item_id, {f: item[f] for f in fields["card"] if f in item and item[f] not in (None, "")}))
    return items


def dispositions(answers: dict[str, Any], review: dict[str, list[str]],
                 thresholds: dict[str, float]) -> dict[str, str]:
    """answered / skip / human per question. A flag, a fallback option, or low confidence sends it to a person."""
    out = {}
    for qid, answer in answers.items():
        threshold = thresholds.get(qid, DEFAULT_THRESHOLD)
        if qid in review:
            out[qid] = "human"
        elif answer.get("type") == "noul":
            p = answer.get("noul", 0.5)
            out[qid] = "answered" if p >= threshold else "skip" if p <= 1 - threshold else "human"
        else:
            confident = (answer.get("confidence") or 0) >= threshold
            fallback = answer.get("type") == "choice" and answer.get("choice") in jev.FALLBACK_OPTIONS
            out[qid] = "answered" if confident and not fallback else "human"
    return out


def sheet_model(sheet: dict[str, Any], provider: str) -> str | None:
    """The sheet's pinned model for this provider. Model ids differ by provider, so a pin is an object keyed by
    provider, a model profile id (profiles.json; it applies only on its own provider), or any other string, which is
    an OpenRouter id and applies only there."""
    pinned = sheet.get("model")
    if isinstance(pinned, dict):
        return pinned.get(provider)
    if isinstance(pinned, str) and pinned in jev.MODELS:
        profile = jev.MODELS[pinned]
        if profile["provider"] == provider:
            return profile["model"]
        print(f"classifier-skill: sheet model {pinned!r} is a {profile['provider']} profile; using the {provider} "
              "default instead", file=sys.stderr)
        return None
    if pinned and provider != "openrouter":
        print(f"classifier-skill: sheet model {pinned!r} is an OpenRouter id; using the {provider} default instead "
              f"(pin per provider with {{\"{provider}\": ...}})", file=sys.stderr)
        return None
    return pinned


def call_reason(exc: jev.CallError) -> str:
    if isinstance(exc, jev.Exhausted):
        return "exhausted"
    status = getattr(exc.__cause__, "code", None)
    if status == 413:
        return "too_large"
    if status in AUTH_STATUSES:
        return "no_key"
    return "bad_request" if status in BAD_REQUEST_STATUSES else "transport"


def blocked_reason(args: argparse.Namespace, sheet: dict[str, Any]) -> str | None:
    """A reason no item can be sent at all, found before anything is sent; None when calls may proceed."""
    if args.dry_run:
        return None
    for provider, endpoint in args.router.endpoints():  # every step a route chain may move to
        if sheet["data"] == "local_only" and not jev.is_local(endpoint):
            print(f"classifier-skill: sheet is local_only; refusing to send to {endpoint!r}", file=sys.stderr)
            return "refused_host"
        try:
            jev.check_endpoint(provider, endpoint)
        except SystemExit:
            return "refused_host"
    spec = jev.PROVIDERS[args.provider]
    key_env = spec.get("key")
    if not spec.get("key_optional") and not os.environ.get(key_env or "", "").strip():
        print(f"classifier-skill: {key_env} is not set", file=sys.stderr)
        return "no_key"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", help="question sheet JSON")
    parser.add_argument("--items", help="items JSONL, one object per line")
    parser.add_argument("--out", help="output JSONL: one stamped line per item, in input order")
    parser.add_argument("--summary", help="summary JSON, written last")
    parser.add_argument("--caller", help="calling skill's name, for the call log")
    parser.add_argument("--context", metavar="FILE",
                        help="text file of facts sent with every card, replacing the sheet's `context` (one generic "
                             "sheet, one context file per account)")
    parser.add_argument("--provider", choices=sorted(jev.PROVIDERS))
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds per request")
    parser.add_argument("--dry-run", action="store_true", help="write the payloads instead of sending them")
    parser.add_argument("--no-redact", action="store_true")
    parser.add_argument("--contract-version", action="store_true")
    args = parser.parse_args()
    if args.contract_version:
        print(jev.CONTRACT_VERSION)
        return
    missing = [flag for flag in ("sheet", "items", "out", "summary") if not getattr(args, flag)]
    if missing:
        jev.fail(f"missing --{', --'.join(missing)}")

    out_path, summary_path = Path(args.out).expanduser(), Path(args.summary).expanduser()
    partial = summary_path.with_name(summary_path.name + ".tmp")
    # checked before anything is deleted or opened for writing, so a mistyped path can never destroy an input
    outputs = [p.resolve() for p in (out_path, summary_path, partial)]
    inputs = {Path(p).expanduser().resolve() for p in (args.sheet, args.items, args.context) if p}
    if len(set(outputs)) != len(outputs):
        jev.fail("--out and --summary must be different files (and --out must not be the summary's .tmp)")
    if inputs & set(outputs):
        jev.fail("--out and --summary must not be the sheet, items, or context file")
    for stale in (summary_path, partial):  # a summary left by an earlier run must never describe this one
        try:
            stale.unlink(missing_ok=True)
        except OSError as exc:
            jev.fail(f"could not clear old summary {stale}: {exc}")
    sheet, questions, thresholds = load_sheet(args.sheet)
    if args.context:
        try:
            sheet["context"] = Path(args.context).expanduser().read_text(encoding="utf-8").strip()
        except OSError as exc:
            jev.fail(f"could not read context file: {exc}")
        if not sheet["context"]:
            jev.fail("context file is empty")
    items = load_items(args.items, sheet["fields"])
    plan = jev.plan_route(args.provider, dry_run=args.dry_run)
    # The route chain answers with one model family on every step, so a sheet's model pin applies only to a run on
    # one named provider; it would otherwise put Jev on the chain's OpenRouter step.
    if plan["route"] and sheet.get("model"):
        print("classifier-skill: ignoring the sheet's model pin on the route chain; each step uses its own pinned "
              "model (name a provider to use the pin)", file=sys.stderr)
    args.router = jev.Router(plan, None, args.timeout)
    args.provider = args.router.provider
    spec = jev.PROVIDERS[args.provider]
    args.endpoint = args.router.current_endpoint()
    model = ((None if plan["route"] else sheet_model(sheet, args.provider)) or plan["steps"][0][1]
             or spec.get("model") or os.environ.get(spec.get("model_env", ""), "").strip())
    template = jev.normalize_request({"questions": questions}, None, batch=True, default_model=model)
    for provider, pinned in plan["steps"]:
        jev.note_unprofiled(provider, pinned or template["model"])
    for provider, _ in plan["steps"]:
        jev.check_provider_limits(template, provider)
    accepted = set.intersection(*(set(jev.PROVIDERS[p].get("fields", jev.ALLOWED_FIELDS)) for p, _ in plan["steps"]))
    for field in sorted(set(template) - accepted):
        template.pop(field)
    versions = {"sheet": f"{sheet['sheet']}@{sheet['version']}", "recipe": sheet.get("recipe"),
                "model": model, "contract": jev.CONTRACT_VERSION,
                "context": hashlib.sha256(sheet["context"].encode()).hexdigest()[:12] if "context" in sheet else None}

    started, run_id = time.monotonic(), uuid.uuid4().hex[:12]
    counts = {"answered": 0, "human": {}, "skip": {}, "unanswered": {}, "written": 0}
    cost, tokens, redacted, flagged, models = 0.0, 0, 0, 0, set()
    # A dry run always shows its payloads, so a small sample can be reviewed before the real run.
    bypass = (not args.dry_run and len(items) < sheet.get("min_items", DEFAULT_MIN_ITEMS)
              and not sheet.get("recurring", False))
    stopped = "below_min_items" if bypass else blocked_reason(args, sheet)

    with out_path.open("w", encoding="utf-8") as out:
        def emit(record: dict[str, Any], answered_by: Any = None) -> None:
            # each answered line carries the model id the provider reported (drift and route moves show per line);
            # other lines carry the model the current step would request
            stamped = {**versions, "model": answered_by if isinstance(answered_by, str) and answered_by
                       else args.router.model(model)}
            out.write(json.dumps({**record, "versions": stamped}, ensure_ascii=False, sort_keys=True) + "\n")
            out.flush()
            counts["written"] += 1

        def unanswered(item_id: str, reason: str) -> None:
            counts["unanswered"][reason] = counts["unanswered"].get(reason, 0) + 1
            emit({"id": item_id, "status": "unanswered", "reason": reason})

        for item_id, card in items:
            if stopped:  # the run cannot send (bypass, no key, refused host) or a run-wide failure already happened
                unanswered(item_id, stopped)
                continue
            if not card:
                unanswered(item_id, "empty_card")
                continue
            state = {"context": sheet["context"], "item": card} if "context" in sheet else {"item": card}
            if not args.no_redact:
                state, n = jev.redact(state)
                redacted += n
            payload = {**template, "state": state}
            try:
                for provider, pinned in plan["steps"]:
                    step_payload = {**payload, "model": pinned or payload["model"]}
                    jev.check_size(step_payload, f"item {item_id!r}", provider)
                    jev.check_provider_limits(step_payload, provider, f"item {item_id!r}")
            except SystemExit:
                unanswered(item_id, "too_large")
                continue
            if args.dry_run:
                emit({"id": item_id, "status": "dry_run", "payload": payload})
                continue
            try:
                response = args.router.send(payload)
            except jev.CallError as exc:
                print(f"classifier-skill: item {item_id!r}: {exc.message}", file=sys.stderr)
                reason = call_reason(exc)
                unanswered(item_id, reason)
                if reason not in ITEM_SCOPED:  # auth, bad request, transport: the rest would fail the same way
                    stopped = "not_sent"
                continue
            cost += jev.response_cost(response)
            tokens += jev.usage_tokens(response)
            if jev.response_errors(response, questions):
                unanswered(item_id, "invalid_answer")
                continue
            answers = response["answers"]
            if isinstance(response.get("model"), str):
                models.add(response["model"])
            review = jev.review_flags(answers, float(sheet.get("margin", 0.2)), response.get("model"),
                                      args.router.strict(response.get("model")))
            marks = dispositions(answers, review, thresholds)
            counts["answered"] += 1
            flagged += bool(review)
            for qid, mark in marks.items():
                if mark in ("human", "skip"):
                    counts[mark][qid] = counts[mark].get(qid, 0) + 1
            emit({"id": item_id, "status": "answered", "answers": answers, "dispositions": marks, "review": review},
                 response.get("model"))

    failed = {r: n for r, n in counts["unanswered"].items() if r not in EXPECTED}
    args.provider, args.route_log = args.router.provider, args.router.log_fields()
    summary = {
        # every path through the loop emits exactly one line, so this is a self-check: false only if a future change
        # drops an item, and a caller must then distrust the output file
        "run_id": run_id, "complete": counts["written"] == len(items), "dry_run": args.dry_run,
        "provider": args.provider, "route": plan["route"], "route_moves": args.router.moves, **versions,
        "items_in": len(items), "items_out": counts["written"], "answered": counts["answered"],
        "human_by_question": counts["human"], "skip_by_question": counts["skip"],
        "unanswered": counts["unanswered"], "bypass": bypass, "degraded": bool(failed),
        "models_reported": sorted(m for m in models if m), "redacted": redacted,
        "cost": round(cost, 8) or None, "seconds": round(time.monotonic() - started, 2),
    }
    partial.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    partial.replace(summary_path)  # the summary appears whole or not at all

    human = sum(counts["human"].values())
    reasons = ", ".join(f"{r} {n}" for r, n in sorted(counts["unanswered"].items())) or "none"
    print(f"Classifier: {counts['answered']}/{human}/{sum(counts['unanswered'].values())} ({reasons})",
          file=sys.stderr)
    if not args.dry_run and not bypass:
        jev.log_call(args, {"caller": jev.caller_slug(args.caller or sheet["sheet"]), "task": versions["sheet"],
                            "recipe": sheet.get("recipe") or "custom"}, template, {
            "mode": "items", "items": len(items), "flagged": flagged, "human": human, "human_by_question": counts["human"], "invalid": failed.get("invalid_answer", 0),
            "unanswered": counts["unanswered"], "redacted": redacted, "input_tokens": tokens,
            "cost": round(cost, 8) or None, "model": sorted(m for m in models if m),
            "seconds": summary["seconds"]})


if __name__ == "__main__":
    main()
