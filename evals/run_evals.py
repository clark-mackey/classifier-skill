#!/usr/bin/env python3
"""Run classifier-skill evals in Codex with and without the skill, each run in a throwaway CODEX_HOME.

Usage: python3 evals/run_evals.py ITERATION [--models gpt-5.6-luna-low] [--cases 1,2] [--variants with_skill,without_skill]
       [--runs 1] [--budget 3] [--timeout 300]
Defaults keep cost down: with-skill runs only, one run per case, and a spend budget checked against the OpenRouter
key before each run (CLASSIFIER_EVAL_BUDGET). Baselines are opt-in; with open network they can still reach Jev.
Outputs go to $CLASSIFIER_EVAL_WORKSPACE (default: ../classifier-skill-workspace next to the skill folder); keep them out of git.
Each run gets an empty working dir and a fresh CODEX_HOME holding only the model's provider config
(plus a symlink to ~/.codex/auth.json for OpenAI models) and, for with_skill runs, the skill.
No global AGENTS.md, user skills, or MCP servers load, and a macOS sandbox hides every installed or
source copy of the skill from reads, so baselines stay clean even when a model searches the disk.
"""
import argparse
import json
import urllib.request
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
HERE = Path(os.environ.get("CLASSIFIER_EVAL_WORKSPACE", SKILL.parent / "classifier-skill-workspace"))
CODEX = Path.home() / ".codex"
OPENROUTER_CONFIG = ('model_provider = "openrouter"\n\n[model_providers.openrouter]\nname = "OpenRouter"\n'
                     'base_url = "https://openrouter.ai/api/v1"\nenv_key = "OPENROUTER_API_KEY"\nwire_api = "responses"\n')
MODELS = {
    "gpt-5.6-luna-low": {"model": "gpt-5.6-luna", "auth": CODEX / "auth.json",
                         "extra": ["-c", "model_reasoning_effort=low"]},
    "deepseek-v4.1-flash-or": {"model": "deepseek/deepseek-v4.1-flash", "config_text": OPENROUTER_CONFIG},
    "gemini-3.8-flash-or": {"model": "google/gemini-3.8-flash", "config_text": OPENROUTER_CONFIG},
    "gpt-6-luna-or": {"model": "openai/gpt-6-luna", "config_text": OPENROUTER_CONFIG},
}

parser = argparse.ArgumentParser()
parser.add_argument("iteration")
parser.add_argument("--models", default=",".join(MODELS))
parser.add_argument("--cases", help="comma-separated eval ids (default: all)")
parser.add_argument("--variants", default="with_skill", help="add without_skill for baselines (costly, often invalid)")
parser.add_argument("--runs", type=int, default=1, help="runs per case and variant")
parser.add_argument("--budget", type=float, default=float(os.environ.get("CLASSIFIER_EVAL_BUDGET", "3")),
                    help="stop before a run once this round's OpenRouter spend reaches this many dollars")
parser.add_argument("--timeout", type=int, default=300, help="seconds before a run is killed")
options = parser.parse_args()
iteration = options.iteration
MODELS = {label: MODELS[label] for label in options.models.split(",")}
wanted = {int(i) for i in options.cases.split(",")} if options.cases else None
cases = [c for c in json.loads((SKILL / "evals/evals.json").read_text())["evals"] if not wanted or c["id"] in wanted]
# The key given to eval runs: CLASSIFIER_EVAL_KEY, else a macOS keychain entry (CLASSIFIER_EVAL_KEYCHAIN,
# default openrouter-eval-key). Runs can read it, so keep this a separate key with a low credit limit.
key = os.environ.get("CLASSIFIER_EVAL_KEY", "").strip() or subprocess.run(
    ["/usr/bin/security", "find-generic-password", "-s", os.environ.get("CLASSIFIER_EVAL_KEYCHAIN", "openrouter-eval-key"),
     "-w"], capture_output=True, text=True, check=True).stdout.strip()


def key_spend():
    """Dollars this key has spent so far, from OpenRouter's key endpoint; None when unavailable."""
    request = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return float(json.load(response)["data"]["usage"])
    except Exception as exc:  # the guard must never crash the round; it reports and keeps checking
        print(f"budget check failed: {type(exc).__name__}", flush=True)
        return None


def seatbelt(home, cwd, out, tmp):
    """macOS sandbox profile: hide every installed or source copy of the skill, allow writes only to run dirs.

    A fresh CODEX_HOME hides the skill from Codex's skill list, but a model can still find it on disk.
    Codex cannot nest its own sandbox inside this one, so it runs with --sandbox danger-full-access here."""
    user = Path.home()
    # Shared temp dirs are hidden too: earlier runs leave request and batch files there that a baseline can read.
    # Each run gets its own TMPDIR instead.
    shared_tmp = [Path(tempfile.gettempdir()), Path("/private/tmp")]
    hidden = [user / ".claude", user / ".agents", user / ".codex/skills", SKILL.parent, user / ".config",
              user / ".Trash", user / "Library/Keychains",
              # shell startup files export the user's secrets; runs use an empty ZDOTDIR instead
              *(user / rc for rc in (".zshrc", ".zprofile", ".zshenv", ".zlogin", ".bashrc", ".bash_profile",
                                    ".profile"))]
    parked = str(user / ".codex/skills").replace(".", "\\.")
    writable = [home, cwd, out, tmp, *shared_tmp, Path("/dev")]
    readable = [out, home, cwd, tmp]  # the run's own dirs, re-allowed after the shared-temp deny
    q = lambda path: '"' + str(Path(path).resolve()) + '"'
    # Shared temp stays usable (mktemp, here-docs, and the python3 xcrun cache need it), but entries named like
    # earlier eval leftovers are unreadable. Baselines stay unreliable; see the usage note.
    leftovers = "".join(f'(deny file-read-data (regex #"^{re.escape(str(Path(t).resolve()))}/[^/]*(jev|classif|typesafe)"))'
                        for t in shared_tmp)
    return ("(version 1)(allow default)"
            f"(deny file-read* {' '.join(f'(subpath {q(h)})' for h in hidden)} (regex #\"^{parked}\"))"
            + leftovers +
            # a deny on one operation outranks an allow on file-read*, so re-allow the same operation explicitly
            f"(allow file-read* file-read-data {' '.join(f'(subpath {q(r)})' for r in readable)})"
            f"(deny file-write*)(allow file-write* {' '.join(f'(subpath {q(w)})' for w in writable)})"
            # no keychain secrets via the `security` CLI; blocking the keychain service instead breaks TLS verification
            '(deny process-exec (literal "/usr/bin/security"))')


def codex_home(spec, with_skill):
    home = Path(tempfile.mkdtemp(prefix="jev-codex-home-"))
    if "config" in spec:
        shutil.copy(spec["config"], home / "config.toml")
    if "config_text" in spec:
        (home / "config.toml").write_text(spec["config_text"])
    if "auth" in spec:
        (home / "auth.json").symlink_to(spec["auth"])
    if with_skill:
        # a real copy, not a symlink, so `find` locates it if the model guesses the wrong path
        shutil.copytree(SKILL, home / "skills/classifier-skill",
                        ignore=shutil.ignore_patterns(".git", ".codegraph", "__pycache__", "evals", "tests"))
    return home


def run_case(label, spec, case, variant, n):
    out = HERE / f"iteration-{iteration}/{label}/eval-{case['id']}/{variant}/run-{n}"
    out.mkdir(parents=True, exist_ok=True)
    home, cwd = codex_home(spec, variant == "with_skill"), Path(tempfile.mkdtemp(prefix="jev-eval-"))
    tmp = Path(tempfile.mkdtemp(prefix="jev-eval-tmp-"))
    for name in case.get("files", []):  # eval inputs, relative to evals/, copied flat into the run's working dir
        shutil.copy(SKILL / "evals" / name, cwd)
    command = ["sandbox-exec", "-p", seatbelt(home, cwd, out, tmp), "codex", "--ask-for-approval", "never", "exec", "-m", spec["model"], *spec.get("extra", []),
               "--ephemeral", "--skip-git-repo-check", "--json", "--sandbox", "danger-full-access",
               "-c", 'shell_environment_policy.include_only=["PATH","HOME","TMPDIR","ZDOTDIR","CLASSIFIER_SKILL_LOG","OPENROUTER_API_KEY"]',
               "--cd", str(cwd), "-o", str(out / "final.txt"), "-"]
    start = time.time()
    with (out / "events.jsonl").open("w") as events, (out / "stderr.log").open("w") as err:
        try:
            # own process group, so a timeout kills Codex's child processes too instead of orphaning them
            proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=events, stderr=err, text=True, cwd=cwd,
                                    start_new_session=True, env={"PATH": os.environ["PATH"], "HOME": str(Path.home()), "TMPDIR": str(tmp), "ZDOTDIR": str(tmp),
                                       "CLASSIFIER_SKILL_LOG": str(tmp / "calls.jsonl"),  # graded as reshape evidence
                                       "CODEX_HOME": str(home), "OPENROUTER_API_KEY": key,
                                       # no git credentials, so a run cannot clone the private skill repo
                                       "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                                       "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_COUNT": "1",
                                       "GIT_CONFIG_KEY_0": "credential.helper", "GIT_CONFIG_VALUE_0": ""})
            proc.communicate(case["prompt"], timeout=options.timeout)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            code = "timeout"
    shutil.rmtree(home, ignore_errors=True)
    shutil.copytree(cwd, out / "workdir", dirs_exist_ok=True)  # keep anything the run wrote, for grading
    shutil.copytree(tmp, out / "tmp", dirs_exist_ok=True)
    shutil.rmtree(cwd, ignore_errors=True)
    shutil.rmtree(tmp, ignore_errors=True)
    (out / "timing.json").write_text(json.dumps({"model": spec["model"], "exit": code,
                                                 "seconds": round(time.time() - start, 1)}))
    print(f"{label} eval-{case['id']} {variant} run-{n}: exit {code}, {time.time() - start:.0f}s", flush=True)


start_spend = key_spend()
spent = 0.0
for label, spec in MODELS.items():
    for variant in options.variants.split(","):
        for case in cases:
            for n in range(1, options.runs + 1):
                now = key_spend()
                if start_spend is not None and now is not None:
                    spent = now - start_spend
                    if spent >= options.budget:
                        print(f"stopped: ${spent:.2f} spent this round, budget ${options.budget:.2f}", flush=True)
                        sys.exit(4)
                run_case(label, spec, case, variant, n)
final = key_spend()
if start_spend is not None and final is not None:
    spent = final - start_spend
print(f"done: ${spent:.2f} spent this round on the eval key", flush=True)
