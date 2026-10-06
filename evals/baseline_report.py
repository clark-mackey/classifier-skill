#!/usr/bin/env python3
"""Compare with_skill and without_skill runs across every iteration in the eval workspace, from facts already on
disk (no model calls). Writes evals/BASELINE.md. Usage: python3 evals/baseline_report.py [WORKSPACE]

The outcome is mechanical, not a full assertion grade: a run is "on target" when it called the classifier (live or
dry run) on a should-call case, or made no call on a should-decline case. A without_skill run that reached the
classifier anyway (a local install, a public skill, a hand-written Decisions call) is contaminated and counted
apart, as is a with_skill run that never read the skill. Should-call and should-decline cases are reported apart."""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import grade_evals

SKILL = Path(__file__).resolve().parent.parent
DECLINE_IDS = {4, 9, 15}  # older cases that predate the `polarity` field
# From iteration 10 the runner hides every installed or source copy of the skill and shared temp dirs
# (iteration-10/benchmark.json); earlier baselines could read a copy installed on this machine.
SANDBOXED_FROM = 10
DECISION_HOST = re.compile(r"openrouter\.ai/api/alpha/decisions|api\.typesafe\.ai|/v1/systemone", re.I)
SENDS = re.compile(r"\bcurl\b[^\n]*(?:\s-d\b|--data|-X\s*POST|--json)|urlopen|requests\.post|httpx\.|fetch\(", re.I)


def expectations():
    cases = json.loads((SKILL / "evals/evals.json").read_text())["evals"]
    return {c["id"]: "local" if c.get("polarity") == "local_only"
            else "decline" if c.get("polarity") == "should_decline" or c["id"] in DECLINE_IDS else "call"
            for c in cases}


def era(iteration):
    number = re.sub(r"\D", "", iteration.split("-")[1]) if iteration.split("-")[1][:1].isdigit() else ""
    return "sandboxed" if not number or int(number) >= SANDBOXED_FROM else "pre-sandbox"


def outcome(run, expect):
    """Called: a classifier script ran (live or dry) or a command sent a request to a decision endpoint. Merely
    naming the skill, searching for it, or reading a URL does not count. A local-only case is on target when nothing
    went to a hosted provider: a local model call and a refusal both pass."""
    facts = grade_evals.facts(run)
    commands = [grade_evals.unwrap(e["item"].get("command", ""))
                for e in grade_evals.json_lines(run / "events.jsonl")
                if (e.get("item") or {}).get("type") == "command_execution"]
    sent = any(DECISION_HOST.search(c) and SENDS.search(c) for c in commands)
    called = facts["jev_live_call"] or facts["jev_dry_run"] or sent
    on_target = not (facts["hosted_call"] or sent) if expect == "local" else called == (expect == "call")
    return {"called": called, "skill_read": facts["skill_read"], "on_target": on_target, "expect": expect,
            "should_call": expect == "call"}


def collect(workspace, expected):
    """(iteration, model, case) -> variant -> list of run outcomes."""
    table = defaultdict(lambda: defaultdict(list))
    for run in sorted(workspace.glob("iteration-*/**/eval-*/*/run-*")):
        variant, case_dir = run.parent.name, run.parent.parent
        case = int(case_dir.name.split("-")[1])
        if variant not in ("with_skill", "without_skill") or case not in expected:
            continue
        if not (run / "events.jsonl").exists() or not (run / "timing.json").exists():
            continue  # a run killed before it finished leaves nothing to grade
        iteration = run.relative_to(workspace).parts[0]
        model = case_dir.parent.name if case_dir.parent.name != iteration else "(default)"
        table[(iteration, model, case)][variant].append(outcome(run, expected[case]))
    return table


def rate(runs, key="on_target"):
    return f"{sum(r[key] for r in runs)}/{len(runs)}" if runs else "–"


def summary_lines(pairs, label):
    """Should-call and should-decline cases reported apart: on a should-call case a baseline only scores by reaching
    the classifier without this skill, so a blended rate would count that contamination as success."""
    call = {k: v for k, v in pairs.items() if v["with_skill"][0]["should_call"]}
    decline = {k: v for k, v in pairs.items() if v["with_skill"][0]["expect"] == "decline"}
    local = {k: v for k, v in pairs.items() if v["with_skill"][0]["expect"] == "local"}
    runs = lambda group, variant: [r for v in group.values() for r in v[variant]]
    base_call = runs(call, "without_skill")
    return [
        f"### {label}",
        "",
        f"- Pairs: {len(pairs)} ({len(call)} should-call, {len(decline)} should-decline, {len(local)} local-only)",
        f"- Should-call, with skill: {rate(runs(call, 'with_skill'))} called the classifier",
        f"- Should-call, without skill: {rate(base_call, 'called')} reached the classifier anyway (a local install or "
        "public sources); these are contaminated, not clean no-skill results. The rest did not reach it, which is "
        "the expected no-skill outcome.",
        f"- Should-decline, with skill: {rate(runs(decline, 'with_skill'))} correctly made no call",
        f"- Should-decline, without skill: {rate(runs(decline, 'without_skill'))} made no call",
        f"- Local-only, with skill: {rate(runs(local, 'with_skill'))} kept the data off hosted providers",
        "",
    ]


def main():
    workspace = Path(sys.argv[1]) if len(sys.argv) > 1 else SKILL.parent / "classifier-skill-workspace"
    expected = expectations()
    table = collect(workspace, expected)
    paired = {k: v for k, v in table.items() if v["with_skill"] and v["without_skill"]}
    all_with = [r for v in table.values() for r in v["with_skill"]]
    order = lambda kv: (int(re.sub(r"\D", "", kv[0][0]) or 0), kv[0][1], kv[0][2])

    lines = [
        "# With-skill vs. without-skill baseline",
        "",
        f"Generated by `evals/baseline_report.py` from `{workspace.name}/`; no runs were repeated. A run called the "
        "classifier when a classifier script ran (live or dry run) or a command sent a request to a decision "
        "endpoint; naming or searching for the skill does not count. This is a mechanical proxy; per-assertion "
        "grades live in each iteration's `benchmark.json`.",
        "",
        "## Paired cases (same iteration, model, and case run both ways)",
        "",
        *summary_lines(paired, "All iterations"),
    ]
    for name in ("pre-sandbox", "sandboxed"):
        group = {k: v for k, v in paired.items() if era(k[0]) == name}
        if group:
            title = (f"Iterations before {SANDBOXED_FROM} (baselines could read a locally installed copy)"
                     if name == "pre-sandbox" else f"Iterations {SANDBOXED_FROM} and later (skill copies hidden)")
            lines += summary_lines(group, title)
    lines += [
        "| Iteration | Model | Case | Expect | With skill on target | Without skill on target | Baseline reached classifier |",
        "|---|---|---|---|---|---|---|",
    ]
    for (iteration, model, case), v in sorted(paired.items(), key=order):
        lines.append(f"| {iteration.removeprefix('iteration-')} | {model} | {case} | {expected[case]} | "
                     f"{rate(v['with_skill'])} | {rate(v['without_skill'])} | {rate(v['without_skill'], 'called')} |")
    should_call = [r for r in all_with if r["should_call"]]
    lines += [
        "",
        "## All with-skill runs",
        "",
        f"- Runs: {len(all_with)}; on target: {rate(all_with)}",
        f"- Should-call runs that never read SKILL.md or a reference (they answered without it): "
        f"{sum(not r['skill_read'] for r in should_call)}/{len(should_call)}",
        "",
        "Baselines need a host allowlist to be clean: with open network, a model without the skill can find a public "
        "Jev skill or call the Decisions API by hand.",
        "",
    ]
    out = SKILL / "evals/BASELINE.md"
    out.write_text("\n".join(lines))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
