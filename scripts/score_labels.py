#!/usr/bin/env python3
"""Score one question's answers against labels: accuracy per class, and coverage at each confidence threshold.

Answers come from a classify_items.py output file, or from any JSONL of {"id", "answer"} lines (for example a blind LLM
arm, whose answers count as fully confident). Labels are a JSONL file with an id field and a label field; they never go
to the classifier. A `score` question's label is the 0-based index of its rubric level (0 = lowest), compared with the
most likely level, not with the continuous `score`. With --target, a threshold is chosen on a tuning split and reported once on the held-out split, so
the reported accuracy is not the one the threshold was tuned on (the `calibrate` recipe). Prints one JSON report."""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

FALLBACK = {"none_fit", "insufficient_context", "insufficient_evidence"}
THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.9, 0.95)


def read_jsonl(path: str) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in Path(path).expanduser().read_text(encoding="utf-8").splitlines()
                if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        print(f"score_labels: could not read {path}: {exc}", file=sys.stderr)
        raise SystemExit(2)


def canonical(value: Any) -> str | None:
    """Labels and answers compare as strings: 2 == "2", True == "true"."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value)


def picks(lines: list[dict[str, Any]], question: str) -> dict[str, tuple[str | None, float]]:
    """id -> (answer, confidence). Unanswered is (None, 0); a plain answer line counts as confidence 1. An answer the
    engine flagged for review gets confidence 0, so no threshold counts it as automated."""
    out = {}
    for line in lines:
        item_id = str(line["id"])
        if item_id in out:
            print(f"score_labels: duplicate prediction id {item_id!r}", file=sys.stderr)
            raise SystemExit(2)
        if isinstance(line.get("answers"), dict) and question in line["answers"]:
            answer = line["answers"][question]
            if answer.get("type") == "noul":
                p = answer.get("noul", 0.5)
                pick, confidence = canonical(p >= 0.5), max(p, 1 - p)
            elif answer.get("type") == "score":
                # `score` is the expected level (1.98), never a label; the pick is the most likely level's index
                probabilities, level = answer.get("probabilities") or {}, answer.get("score")
                pick = (max(probabilities, key=probabilities.get) if probabilities
                        else canonical(round(level)) if isinstance(level, (int, float)) else None)
                confidence = answer.get("confidence") or 0
            else:
                pick, confidence = canonical(answer.get("choice")), answer.get("confidence") or 0
            out[item_id] = (pick, 0.0 if (line.get("review") or {}).get(question) else confidence)
        elif "answer" in line:
            out[item_id] = (canonical(line["answer"]), 1.0)
        else:
            out[item_id] = (None, 0.0)
    return out


def score(ids: list[str], labels: dict[str, str], got: dict[str, tuple[str | None, float]]) -> dict[str, Any]:
    rows = [(labels[i], *got.get(i, (None, 0.0))) for i in ids]
    answered = [(truth, pick, conf) for truth, pick, conf in rows if pick is not None]
    classes = sorted({truth for truth, _, _ in rows})
    per_class = {}
    for cls in classes:
        support = sum(t == cls for t, _, _ in rows)  # unanswered items count against recall
        predicted = [(t, p) for t, p, _ in answered if p == cls]
        correct = sum(t == cls and p == cls for t, p, _ in rows)
        per_class[cls] = {"support": support, "recall": round(correct / support, 3) if support else None,
                          "precision": round(sum(t == p for t, p in predicted) / len(predicted), 3) if predicted else None}
    at = {}
    for threshold in THRESHOLDS:
        kept = [(t, p) for t, p, c in answered if c >= threshold and p not in FALLBACK]
        right = sum(t == p for t, p in kept)
        at[str(threshold)] = {"coverage": round(len(kept) / len(rows), 3) if rows else 0,
                              "accuracy": round(right / len(kept), 3) if kept else None,
                              "correct": right, "kept": len(kept)}
    confusion: dict[str, dict[str, int]] = {}
    for truth, pick, _ in answered:
        confusion.setdefault(truth, {})[str(pick)] = confusion.setdefault(truth, {}).get(str(pick), 0) + 1
    return {"items": len(rows), "answered": len(answered),
            "accuracy": round(sum(t == p for t, p, _ in answered) / len(answered), 3) if answered else None,
            "per_class": per_class, "at_threshold": at, "confusion": confusion}


def choose(report: dict[str, Any], target: float) -> float | None:
    """The lowest threshold whose tuning accuracy meets the target, or None when none does."""
    for threshold in THRESHOLDS:
        row = report["at_threshold"][str(threshold)]
        if row["kept"] and row["correct"] / row["kept"] >= target:  # raw, not the rounded display value
            return threshold
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--answers", required=True, help="classify_items.py output, or {id, answer} JSONL")
    parser.add_argument("--labels", required=True, help="JSONL with the id and label fields")
    parser.add_argument("--question", required=True, help="question id to score")
    parser.add_argument("--label-field", default="label")
    parser.add_argument("--id-field", default="id")
    parser.add_argument("--target", type=float, help="accuracy target: choose a threshold on a tuning split")
    parser.add_argument("--holdout", type=float, default=0.5, help="share of items held out (with --target)")
    parser.add_argument("--seed", type=int, default=13)
    args = parser.parse_args()
    if not 0 < args.holdout < 1:
        parser.error("--holdout must be between 0 and 1 (exclusive): the share of items held out")
    if args.target is not None and not 0 < args.target <= 1:
        parser.error("--target must be an accuracy between 0 and 1")

    labels = {}
    for row in read_jsonl(args.labels):
        if row.get(args.label_field) in (None, ""):
            continue
        item_id = str(row[args.id_field])
        if item_id in labels:
            parser.error(f"duplicate label id {item_id!r}")
        labels[item_id] = canonical(row[args.label_field])
    got = picks(read_jsonl(args.answers), args.question)
    ids = sorted(labels)  # every labeled item counts; one with no answer line is unanswered
    missing = sorted(set(labels) - set(got))
    report: dict[str, Any] = {"question": args.question, "labeled": len(labels), "missing_from_answers": len(missing),
                              "all": score(ids, labels, got)}
    if args.target is not None:
        shuffled = ids[:]
        random.Random(args.seed).shuffle(shuffled)
        cut = int(len(shuffled) * (1 - args.holdout))
        tuning, held = score(shuffled[:cut], labels, got), score(shuffled[cut:], labels, got)
        threshold = choose(tuning, args.target)
        report["calibration"] = {
            "target": args.target, "tuning_items": cut, "holdout_items": len(shuffled) - cut, "threshold": threshold,
            "holdout_at_threshold": held["at_threshold"][str(threshold)] if threshold is not None else None,
            "note": "no threshold met the target on the tuning split" if threshold is None else
                    "held-out result, reported once; do not re-tune on it"}
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
