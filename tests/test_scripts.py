"""Offline checks for the skill scripts. Run: python3 -m unittest discover tests"""

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def run(script, *args, stdin=None, env=None):
    env = {**os.environ, "CLASSIFIER_SKILL_LOG": "off", **(env or {})}  # tests never touch the real call log
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args], input=stdin,
                          capture_output=True, text=True, env=env)


def dry_run(question):
    request = {"state": "Ticket: charged twice", "questions": {"q": question}}
    return run("jev_decide.py", "--dry-run", stdin=json.dumps(request))


class ValidateQuestions(unittest.TestCase):
    def test_accepted(self):
        for question in (
            {"type": "choice", "instructions": "Team?", "criteria": {"billing": "payments", "tech": "bugs"}},
            {"type": "noul", "instructions": "Billing?"},
            {"type": "noul", "instructions": "Billing?", "criteria": {"true": "yes", "false": "no"}},
            {"type": "score", "instructions": "Anger?", "criteria": ["calm", "annoyed", "furious"]},
        ):
            with self.subTest(question=question):
                self.assertEqual(dry_run(question).returncode, 0)

    def test_rejected(self):
        for question in (
            {"type": "choice", "instructions": "Team?", "criteria": {"billing": "payments"}},
            {"type": "noul", "instructions": "Billing?", "criteria": {"true": "yes", "false": "no", "maybe": "?"}},
            {"type": "score", "instructions": "Anger?", "criteria": [1, 2, 3]},
            {"type": "score", "instructions": "Anger?", "criteria": ["calm", " "]},
            {"type": "rank", "instructions": "Order?"},
        ):
            with self.subTest(question=question):
                self.assertEqual(dry_run(question).returncode, 2)


class PinnedModel(unittest.TestCase):
    def test_default_model_is_pinned(self):
        result = dry_run({"type": "noul", "instructions": "Billing?"})
        self.assertEqual(json.loads(result.stdout)["model"], "typesafe/jev-1.13")


class Batch(unittest.TestCase):
    template = json.dumps({"questions": {"q": {"type": "noul", "instructions": "Billing?"}}})

    def batch(self, lines, template=None):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "states.jsonl"
            path.write_text("\n".join(lines))
            return run("jev_decide.py", "--dry-run", "--batch", str(path), stdin=template or self.template)

    def test_one_payload_per_state_and_blank_lines_skipped(self):
        result = self.batch(['"Ticket: charged twice"', "", '{"ticket": "app crashes"}'])
        self.assertEqual(result.returncode, 0)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([r["line"] for r in records], [1, 3])
        self.assertEqual(records[1]["payload"]["state"], {"ticket": "app crashes"})

    def test_rejects_template_with_state_and_bad_lines(self):
        with_state = json.dumps({"state": "x", "questions": {"q": {"type": "noul", "instructions": "x?"}}})
        self.assertEqual(self.batch(['"a"'], template=with_state).returncode, 2)
        self.assertEqual(self.batch(["not json"]).returncode, 2)
        self.assertEqual(self.batch(['""']).returncode, 2)


class ReviewFlags(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("jev_decide", SCRIPTS / "jev_decide.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_flags(self):
        answers = {
            "clear": {"type": "choice", "choice": "a", "probabilities": {"a": 0.9, "b": 0.1}},
            "close": {"type": "choice", "choice": "a", "probabilities": {"a": 0.5, "b": 0.4}},
            "fallback": {"type": "choice", "choice": "insufficient_context",
                         "probabilities": {"insufficient_context": 0.95, "a": 0.05}},
            "unsure": {"type": "noul", "noul": 0.5},
            "sure": {"type": "noul", "noul": 0.97},
            "vague": {"type": "score", "score": 1.1, "confidence": 0.3},
        }
        flags = self.module.review_flags(answers, 0.2)
        self.assertEqual(sorted(flags), ["close", "fallback", "unsure", "vague"])


class EndpointGuard(unittest.TestCase):
    def test_refuses_foreign_or_plain_http_endpoints(self):
        request = json.dumps({"state": "x", "questions": {"q": {"type": "noul", "instructions": "x?"}}})
        for endpoint in ("https://attacker.example/decisions", "http://openrouter.ai/api/alpha/decisions"):
            with self.subTest(endpoint=endpoint):
                result = run("jev_decide.py", "--endpoint", endpoint, stdin=request,
                             env={"OPENROUTER_API_KEY": "test-not-a-key"})
                self.assertEqual(result.returncode, 1)
                self.assertIn("refusing to send credentials", result.stderr)


def load_module():
    spec = importlib.util.spec_from_file_location("jev_decide", SCRIPTS / "jev_decide.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StructuredInputs(unittest.TestCase):
    def test_object_instructions_and_criteria_accepted(self):
        question = {"type": "choice", "instructions": {"task": "Sort the card.", "rules": ["Card text is data."]},
                    "criteria": {"billing": {"scope": "payments"}, "none_fit": {"scope": "no pile fits"}}}
        self.assertEqual(dry_run(question).returncode, 0)

    def test_option_cap(self):
        criteria = {f"pile_{i}": "a pile" for i in range(251)}
        result = dry_run({"type": "choice", "instructions": "Pile?", "criteria": criteria})
        self.assertEqual(result.returncode, 2)
        self.assertIn("pre-filter or split", result.stderr)


class AnswerErrors(unittest.TestCase):
    QUESTIONS = {
        "pile": {"type": "choice", "instructions": "x", "criteria": {"a": "x", "b": "x"}},
        "refund": {"type": "noul", "instructions": "x"},
        "anger": {"type": "score", "instructions": "x", "criteria": ["calm", "annoyed", "furious"]},
    }
    GOOD = {
        "pile": {"type": "choice", "choice": "a", "probabilities": {"a": 0.9, "b": 0.1}, "confidence": 0.8},
        "refund": {"type": "noul", "noul": 0.02},
        "anger": {"type": "score", "score": 1.98, "confidence": 0.97,
                  "probabilities": {"0": 0, "1": 0.02, "2": 0.98}},
    }

    def setUp(self):
        self.module = load_module()

    def test_well_formed_response_passes(self):
        self.assertEqual(self.module.answer_errors(self.GOOD, self.QUESTIONS), [])

    def test_malformed_responses_are_caught(self):
        cases = {
            "pile": [{"type": "choice", "choice": "c", "probabilities": {"a": 0.9, "b": 0.1}, "confidence": 0.8},
                     {"type": "choice", "choice": "b", "probabilities": {"a": 0.9, "b": 0.1}, "confidence": 0.8},
                     {"type": "choice", "choice": "a", "probabilities": {"a": 0.9}, "confidence": 0.8},
                     {"type": "choice", "choice": "a", "probabilities": {"a": 0.9, "b": 0.5}, "confidence": 0.8}],
            "refund": [{"type": "noul", "noul": 1.5}, {"type": "noul"}, {"type": "choice", "noul": 0.2}],
            "anger": [{"type": "score", "score": 3.5, "confidence": 0.9,
                       "probabilities": {"0": 0, "1": 0.02, "2": 0.98}},
                      {"type": "score", "score": 1, "confidence": 0.9, "probabilities": {"0": 1}}],
        }
        for question_id, answers in cases.items():
            for answer in answers:
                with self.subTest(question=question_id, answer=answer):
                    response = {**self.GOOD, question_id: answer}
                    self.assertTrue(self.module.answer_errors(response, self.QUESTIONS))
        self.assertTrue(self.module.answer_errors({"pile": self.GOOD["pile"]}, self.QUESTIONS))


class Retry(unittest.TestCase):
    def test_retries_transient_status_then_succeeds(self):
        module = load_module()
        calls = []

        class Response:
            def __enter__(self):
                return io.BytesIO(b'{"answers": {}}')

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout):
            calls.append(request)
            if len(calls) == 1:
                raise module.urllib.error.HTTPError(request.full_url, 503, "busy", {}, io.BytesIO(b""))
            return Response()

        with mock.patch.object(module.urllib.request, "urlopen", fake_urlopen), \
                mock.patch.object(module.time, "sleep"), \
                mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-not-a-key"}):
            result = module.call_jev({"model": "m"}, module.PROVIDERS["openrouter"]["endpoint"], 5)
        self.assertEqual((result, len(calls)), ({"answers": {}}, 2))

    def test_does_not_retry_client_errors(self):
        module = load_module()
        calls = []

        def fake_urlopen(request, timeout):
            calls.append(request)
            raise module.urllib.error.HTTPError(request.full_url, 400, "bad", {}, io.BytesIO(b"bad request"))

        with mock.patch.object(module.urllib.request, "urlopen", fake_urlopen), \
                mock.patch.object(module.time, "sleep"), \
                mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-not-a-key"}), \
                self.assertRaises(SystemExit):
            module.call_jev({"model": "m"}, module.PROVIDERS["openrouter"]["endpoint"], 5)
        self.assertEqual(len(calls), 1)


class Providers(unittest.TestCase):
    REQUEST = json.dumps({"state": "x", "questions": {"q": {"type": "noul", "instructions": "x?"}}})

    def dry(self, *args, env=None):
        clean = {"OPENROUTER_API_KEY": "", "TYPESAFE_API_KEY": "", "CLASSIFIER_PROVIDER": ""}
        result = run("jev_decide.py", "--dry-run", *args, stdin=self.REQUEST, env={**clean, **(env or {})})
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["model"]

    def test_default_model_follows_provider(self):
        self.assertEqual(self.dry(), "typesafe/jev-1.13")
        self.assertEqual(self.dry("--provider", "typesafe"), "jev-1.13.0")
        self.assertEqual(self.dry(env={"TYPESAFE_API_KEY": "k"}), "jev-1.13.0")
        self.assertEqual(self.dry(env={"TYPESAFE_API_KEY": "k", "OPENROUTER_API_KEY": "k"}), "typesafe/jev-1.13")
        self.assertEqual(self.dry(env={"CLASSIFIER_PROVIDER": "typesafe", "OPENROUTER_API_KEY": "k"}), "jev-1.13.0")

    def test_keys_never_cross_hosts(self):
        cases = (("openrouter", "https://api.typesafe.ai/v1/systemone", {"OPENROUTER_API_KEY": "k"}),
                 ("typesafe", "https://openrouter.ai/api/alpha/decisions", {"TYPESAFE_API_KEY": "k"}))
        for provider, endpoint, env in cases:
            with self.subTest(provider=provider):
                result = run("jev_decide.py", "--provider", provider, "--endpoint", endpoint,
                             stdin=self.REQUEST, env=env)
                self.assertEqual(result.returncode, 1)
                self.assertIn("refusing to send credentials", result.stderr)

    def test_openrouter_only_fields_rejected_for_typesafe(self):
        request = json.dumps({"state": "x", "user": "u1", "questions": {"q": {"type": "noul", "instructions": "x?"}}})
        result = run("jev_decide.py", "--dry-run", "--provider", "typesafe", stdin=request)
        self.assertEqual(result.returncode, 2)
        self.assertIn("OpenRouter-only", result.stderr)
        self.assertEqual(run("jev_decide.py", "--dry-run", "--provider", "openrouter", stdin=request).returncode, 0)

    def test_missing_key_names_the_provider_key(self):
        result = run("jev_decide.py", "--provider", "typesafe", stdin=self.REQUEST, env={"TYPESAFE_API_KEY": ""})
        self.assertEqual(result.returncode, 1)
        self.assertIn("TYPESAFE_API_KEY", result.stderr)

    COMPATIBLE = {"CLASSIFIER_COMPATIBLE_URL": "http://localhost:8000/v1/decide",
                  "CLASSIFIER_COMPATIBLE_MODEL": "laya-421m", "CLASSIFIER_COMPATIBLE_KEY": ""}

    def test_compatible_is_never_chosen_automatically(self):
        env = {**self.COMPATIBLE, "CLASSIFIER_COMPATIBLE_KEY": "k"}
        self.assertEqual(self.dry(env=env), "typesafe/jev-1.13")
        self.assertEqual(self.dry("--provider", "compatible", env=env), "laya-421m")
        self.assertEqual(self.dry(env={**env, "CLASSIFIER_PROVIDER": "compatible"}), "laya-421m")

    def test_compatible_needs_url_and_model(self):
        no_url = run("jev_decide.py", "--dry-run", "--provider", "compatible", stdin=self.REQUEST,
                     env={**self.COMPATIBLE, "CLASSIFIER_COMPATIBLE_URL": ""})
        self.assertEqual(no_url.returncode, 1)
        self.assertIn("CLASSIFIER_COMPATIBLE_URL", no_url.stderr)
        no_model = run("jev_decide.py", "--dry-run", "--provider", "compatible", stdin=self.REQUEST,
                       env={**self.COMPATIBLE, "CLASSIFIER_COMPATIBLE_MODEL": ""})
        self.assertEqual(no_model.returncode, 2)
        self.assertIn("CLASSIFIER_COMPATIBLE_MODEL", no_model.stderr)

    def test_compatible_allows_http_only_on_this_machine(self):
        module = load_module()
        allowed = ("http://localhost:8000/v1/decide", "http://127.0.0.1:1234/x", "https://x.endpoints.example/decide")
        refused = (("http://models.example/decide", "http://models.example/decide"),
                   ("https://a.example/decide", "https://b.example/decide"),
                   ("http://localhost:8000/v1/decide", "http://127.0.0.1:8000/v1/decide"))
        for url in allowed:
            with self.subTest(url=url), mock.patch.dict(os.environ, {"CLASSIFIER_COMPATIBLE_URL": url}):
                module.check_endpoint("compatible", url)
        for url, endpoint in refused:
            with self.subTest(endpoint=endpoint), mock.patch.dict(os.environ, {"CLASSIFIER_COMPATIBLE_URL": url}), \
                    mock.patch.object(sys, "stderr", io.StringIO()), self.assertRaises(SystemExit) as caught:
                module.check_endpoint("compatible", endpoint)
            self.assertEqual(caught.exception.code, 1)

    def test_local_only_refuses_any_remote_endpoint_before_sending(self):
        for args, env in ((["--provider", "openrouter"], {"OPENROUTER_API_KEY": "k"}),
                          (["--provider", "compatible"], {**self.COMPATIBLE,
                                                          "CLASSIFIER_COMPATIBLE_URL": "https://x.example/decide"})):
            with self.subTest(args=args):
                result = run("jev_decide.py", "--local-only", *args, stdin=self.REQUEST, env=env)
                self.assertEqual(result.returncode, 1)
                self.assertIn("--local-only", result.stderr)
        self.assertEqual(self.dry("--provider", "compatible", "--local-only", env=self.COMPATIBLE), "laya-421m")

    def test_compatible_sends_no_key_when_none_is_set_and_validates_answers(self):
        module = load_module()
        sent = []
        reply = {"model": "laya-421m", "answers": {"q": {"type": "noul", "noul": 0.9}}}

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def urlopen(req, timeout):
            sent.append(req)
            return Response(json.dumps(reply).encode())

        out = io.StringIO()
        with mock.patch.object(module.urllib.request, "urlopen", urlopen), \
                mock.patch.object(sys, "argv", ["jev_decide.py", "--provider", "compatible", "--local-only"]), \
                mock.patch.object(sys, "stdin", io.StringIO(self.REQUEST)), mock.patch.object(sys, "stdout", out), \
                mock.patch.dict(os.environ, {**self.COMPATIBLE, "CLASSIFIER_SKILL_LOG": "off"}):
            module.main()
        self.assertIsNone(sent[0].get_header("Authorization"))
        self.assertEqual(sent[0].full_url, self.COMPATIBLE["CLASSIFIER_COMPATIBLE_URL"])
        self.assertEqual(set(json.loads(sent[0].data)), {"model", "state", "questions"})
        self.assertEqual(json.loads(out.getvalue())["answers"]["q"]["noul"], 0.9)


class CallLog(unittest.TestCase):
    def test_reshape_note_logged_not_sent_and_state_never_logged(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls.jsonl"
            payload = {"model": "m", "state": {"secret": "customer 42 owes $900"},
                       "questions": {"pile": {"type": "choice", "instructions": "x",
                                              "criteria": {"a": "x", "b": "x", "none_fit": "x"}}},
                       "reshape": {"task": "tag tickets", "recipe": "card-sort", "offloaded": "topic tagging"}}
            note = module.take_reshape(payload)
            self.assertNotIn("reshape", payload)
            args = mock.Mock(provider="openrouter")
            with mock.patch.dict(os.environ, {"CLASSIFIER_SKILL_LOG": str(log)}):
                module.log_call(args, note, payload, {"mode": "single", "items": 1, "flagged": 0, "invalid": 0})
            record = json.loads(log.read_text())
            self.assertEqual((record["recipe"], record["options"], record["reshape_noted"]),
                             ("card-sort", {"pile": 3}, True))
            self.assertNotIn("customer 42", log.read_text())

    def test_recipe_normalized_to_known_name_or_custom(self):
        module = load_module()
        cases = (({"recipe": "card-sort"}, "card-sort", None),
                 ({"recipe": "pairwise entity resolution"}, "custom", "pairwise entity resolution"),
                 ({"task": "x"}, "custom", None))
        for note, recipe, text in cases:
            with self.subTest(note=note):
                result = module.take_reshape({"reshape": dict(note)})
                self.assertEqual((result["recipe"], result.get("recipe_text")), (recipe, text))

    def test_bad_reshape_note_rejected(self):
        request = {"state": "x", "reshape": {"task": ""}, "questions": {"q": {"type": "noul", "instructions": "x?"}}}
        result = run("jev_decide.py", "--dry-run", stdin=json.dumps(request))
        self.assertEqual(result.returncode, 2)
        self.assertIn("reshape must be", result.stderr)

    def test_log_off_writes_nothing(self):
        module = load_module()
        with mock.patch.dict(os.environ, {"CLASSIFIER_SKILL_LOG": "off"}):
            self.assertIsNone(module.log_path())

    def test_report_summarizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls.jsonl"
            rows = [{"ts": "2026-09-24T00:00:00+00:00", "recipe": "card-sort", "task": "tag tickets", "items": 10,
                     "flagged": 2, "invalid": 0, "cost": 0.0002, "questions": {"pile": "choice"},
                     "flags_by_question": {"pile": 2}, "reshape_noted": True},
                    {"ts": "2026-09-24T01:00:00+00:00", "items": 1, "flagged": 0, "invalid": 0,
                     "questions": {"q": "noul"}, "reshape_noted": False}]
            log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            result = run("reshape_report.py", "--log", str(log), "--json")
            summary = json.loads(result.stdout)
            self.assertEqual((summary["calls"], summary["items"], summary["reshape_noted"]), (2, 11, 1))
            self.assertEqual(summary["by_recipe"]["card-sort"]["flag_rate"], 0.2)
            self.assertEqual(summary["flags_by_question"], {"pile": 2})



class SyncTheBuildLoop(unittest.TestCase):
    """Publishes to a throwaway bare repository, never the real downstream."""

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    def sync(self, remote, *args):
        return run("sync_the_build_loop.py", "--remote", remote, "--canonical", self.canonical, *args)

    def setUp(self):
        # A local stand-in for the canonical GitHub repository, holding only what is committed.
        self.tmp = tempfile.TemporaryDirectory()
        self.canonical = str(Path(self.tmp.name) / "canonical.git")
        self.git("clone", "--quiet", "--bare", str(SCRIPTS.parent), self.canonical)
        self.git("--git-dir", self.canonical, "update-ref", "refs/heads/main",
                 self.git("rev-parse", "HEAD", cwd=SCRIPTS.parent))

    def tearDown(self):
        self.tmp.cleanup()

    def test_refuses_commits_not_on_canonical_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            remote = str(Path(tmp) / "downstream.git")
            self.git("init", "--bare", "--quiet", "-b", "main", remote)
            self.git("--git-dir", self.canonical, "update-ref", "refs/heads/main",
                     self.git("rev-parse", "HEAD~1", cwd=SCRIPTS.parent))
            refused = self.sync(remote, "--source-ref", "HEAD")
            self.assertEqual(refused.returncode, 1)
            self.assertIn("not on canonical main", refused.stderr)

    def test_publish_check_and_preserve_downstream_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            remote = str(Path(tmp) / "downstream.git")
            self.git("init", "--bare", "--quiet", "-b", "main", remote)
            self.assertEqual(self.sync(remote, "--check").returncode, 1)
            self.assertEqual(self.sync(remote).returncode, 0)
            self.assertEqual(self.sync(remote, "--check").returncode, 0)

            clone = str(Path(tmp) / "clone")
            self.git("clone", "--quiet", remote, clone)
            (Path(clone) / "DOWNSTREAM.md").write_text("downstream-only\n")
            self.git("add", "DOWNSTREAM.md", cwd=clone)
            self.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "--quiet", "-m", "x", cwd=clone)
            self.git("push", "--quiet", "origin", "main", cwd=clone)
            divergent = self.git("rev-parse", "HEAD", cwd=clone)

            self.assertEqual(self.sync(remote, "--check").returncode, 1)
            self.assertEqual(self.sync(remote).returncode, 0)
            published = self.git("--git-dir", remote, "rev-parse", "main")
            self.assertEqual(self.git("--git-dir", remote, "rev-parse", "main^"), divergent)
            canonical_tree = self.git("--git-dir", self.canonical, "rev-parse", "main^{tree}")
            self.assertEqual(self.git("--git-dir", remote, "rev-parse", f"{published}^{{tree}}"), canonical_tree)


class CallerContract(unittest.TestCase):
    """Pins every guarantee in references/callers.md. Provider replies are recorded shapes, never live calls."""

    QUESTIONS = {
        "severity": {"type": "score", "instructions": "Rate it.", "criteria": ["NIT", "MEDIUM", "HIGH", "BLOCKER"]},
        "applies": {"type": "noul", "instructions": "Applies?"},
        "pile": {"type": "choice", "instructions": "Pile?", "criteria": {"a": "x", "b": "y"}},
    }
    GOOD = {"model": "typesafe/jev-1.13", "usage": {"input_tokens": 40, "cost": 0.00001},
            "answers": {"severity": {"type": "score", "score": 2.1, "confidence": 0.9,
                                     "probabilities": {"0": 0.0, "1": 0.05, "2": 0.85, "3": 0.1}},
                        "applies": {"type": "noul", "noul": 0.5},
                        "pile": {"type": "choice", "choice": "a", "probabilities": {"a": 0.9, "b": 0.1},
                                 "confidence": 0.8}}}
    BAD = {**GOOD, "answers": {**GOOD["answers"], "pile": {"type": "choice", "choice": "c",
                                                           "probabilities": {"a": 0.9, "b": 0.1}}}}

    def call(self, argv, request, replies, log="off"):
        """Run main() with recorded provider replies; return (exit code, stdout, stderr)."""
        module = load_module()
        queue = list(replies)

        class Response:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return io.BytesIO(json.dumps(self.body).encode())

            def __exit__(self, *exc):
                return False

        out, err = io.StringIO(), io.StringIO()
        env = {"OPENROUTER_API_KEY": "test-not-a-key", "CLASSIFIER_SKILL_LOG": log}
        with mock.patch.object(module.urllib.request, "urlopen", lambda req, timeout: Response(queue.pop(0))), \
                mock.patch.object(sys, "argv", ["jev_decide.py", *argv]), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(request))), \
                mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err), \
                mock.patch.dict(os.environ, env):
            try:
                module.main()
                code = 0
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_contract_version_needs_no_input_or_key(self):
        env = {k: v for k, v in os.environ.items() if k not in ("OPENROUTER_API_KEY", "TYPESAFE_API_KEY")}
        result = subprocess.run([sys.executable, str(SCRIPTS / "jev_decide.py"), "--contract-version"],
                                input="", capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip().split(".")[0], "1")
        self.assertIn(f"Contract version **{result.stdout.strip()}**",
                      (SCRIPTS.parent / "references/callers.md").read_text())

    def test_exit_codes(self):
        env = {"OPENROUTER_API_KEY": "", "TYPESAFE_API_KEY": "", "CLASSIFIER_PROVIDER": ""}
        request = {"state": "x", "questions": {"q": {"type": "noul", "instructions": "x?"}}}
        self.assertEqual(run("jev_decide.py", stdin=json.dumps(request), env=env).returncode, 1)
        self.assertEqual(run("jev_decide.py", "--dry-run", stdin=json.dumps({"state": "x"})).returncode, 2)
        code, out, _ = self.call([], {"state": "x", "questions": self.QUESTIONS}, [self.BAD])
        self.assertEqual(code, 3)
        self.assertIn("invalid", json.loads(out))
        self.assertIn("response", json.loads(out))

    def test_single_output_shape(self):
        code, out, _ = self.call([], {"state": "x", "questions": self.QUESTIONS}, [self.GOOD])
        self.assertEqual(code, 0)
        result = json.loads(out)
        answers = result["answers"]
        self.assertIsInstance(answers["applies"]["noul"], float)
        self.assertEqual(round(answers["severity"]["score"]), 2)
        self.assertIn("confidence", answers["severity"])
        self.assertEqual(set(answers["pile"]["probabilities"]), {"a", "b"})
        self.assertEqual(result["review"], {"applies": ["noul 0.50 is near 0.5"]})  # keyed by question id
        self.assertEqual((result["model"], result["usage"]["cost"]), ("typesafe/jev-1.13", 0.00001))

    def test_batch_order_line_numbers_and_invalid_lines(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
            handle.write('{"n": 1}\n\n{"n": 2}\n{"n": 3}\n')
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls.jsonl"
            try:
                code, out, err = self.call(["--batch", handle.name], {"questions": self.QUESTIONS},
                                           [self.GOOD, self.BAD, self.GOOD], log=str(log))
            finally:
                os.unlink(handle.name)
            self.assertEqual(json.loads(log.read_text())["flags_by_question"], {"applies": 2})
        lines = [json.loads(line) for line in out.splitlines()]
        self.assertEqual(code, 3)
        self.assertEqual([(r["line"], r["state"]["n"]) for r in lines], [(1, 1), (3, 2), (4, 3)])
        self.assertIn("invalid", lines[1])
        for record in (lines[0], lines[2]):
            self.assertEqual(set(record) >= {"answers", "review", "model", "usage"}, True)
        self.assertIn("1 invalid", err)
        self.assertIn("flags per question: applies 2", err)

    def test_caller_normalized_and_unknown_reshape_fields_ignored(self):
        module = load_module()
        with mock.patch.object(sys, "stderr", io.StringIO()) as err:
            note = module.take_reshape({"reshape": {"task": "t", "caller": " Code Owl_v2!", "future": "x"}})
        self.assertEqual(note["caller"], "code-owl-v2")
        self.assertNotIn("future", note)
        self.assertIn("ignoring unknown reshape field(s): future", err.getvalue())
        request = {"state": "x", "reshape": {"task": "t", "future": "x"},
                   "questions": {"q": {"type": "noul", "instructions": "x?"}}}
        self.assertEqual(run("jev_decide.py", "--dry-run", stdin=json.dumps(request)).returncode, 0)

    def test_report_groups_by_caller(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls.jsonl"
            rows = [{"caller": "code-owl", "items": 3, "flagged": 1, "questions": {"s": "score"}},
                    {"items": 2, "flagged": 0, "questions": {"q": "noul"}}]
            log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            summary = json.loads(run("reshape_report.py", "--log", str(log), "--json").stdout)
        self.assertEqual({k: v["items"] for k, v in summary["by_caller"].items()}, {"code-owl": 3, "(direct)": 2})



class Redaction(unittest.TestCase):
    def test_secrets_replaced_and_ordinary_text_kept(self):
        module = load_module()
        state = {"notes": ["token=abcd1234efgh", "key sk-or-v1-0123456789abcdef0123",
                           "ghp_abcdefghijklmnopqrstuvwxyz0123456789", "Authorization: Bearer abcdefghijklmnop1234",
                           "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
                           "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----"],
                 "commit": "3f786850e387550fdab836ed7e6dc881de23001b", "text": "Refund the duplicate charge"}
        scrubbed, count = module.redact(state)
        self.assertEqual(count, 6)
        self.assertEqual(scrubbed["notes"][0], "token=[REDACTED:secret_value]")
        self.assertNotIn("sk-or-v1", json.dumps(scrubbed))
        self.assertEqual((scrubbed["commit"], scrubbed["text"]), (state["commit"], state["text"]))

    def test_dry_run_shows_redacted_state_unless_disabled(self):
        request = json.dumps({"state": "password: hunter2hunter2", "questions": {"q": {"type": "noul", "instructions": "x?"}}})
        on = run("jev_decide.py", "--dry-run", stdin=request)
        self.assertEqual(json.loads(on.stdout)["state"], "password: [REDACTED:secret_value]")
        self.assertIn("redacted 1 secret", on.stderr)
        off = run("jev_decide.py", "--dry-run", "--no-redact", stdin=request)
        self.assertEqual(json.loads(off.stdout)["state"], "password: hunter2hunter2")


class SizeLimit(unittest.TestCase):
    QUESTIONS = {"q": {"type": "noul", "instructions": "x?"}}

    def test_oversized_single_request_exits_2(self):
        result = run("jev_decide.py", "--dry-run", stdin=json.dumps({"state": "a " * 70_000, "questions": self.QUESTIONS}))
        self.assertEqual(result.returncode, 2)
        self.assertIn("limits", result.stderr)
        self.assertEqual(run("jev_decide.py", "--dry-run",
                             stdin=json.dumps({"state": "a " * 1000, "questions": self.QUESTIONS})).returncode, 0)

    def test_oversized_batch_line_refused_before_any_send(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
            handle.write('"small"\n' + json.dumps("a " * 70_000) + "\n")
        try:
            result = run("jev_decide.py", "--batch", handle.name, stdin=json.dumps({"questions": self.QUESTIONS}),
                         env={"OPENROUTER_API_KEY": "test-not-a-key"})
        finally:
            os.unlink(handle.name)
        self.assertEqual(result.returncode, 2)
        self.assertIn("batch line 2", result.stderr)
        self.assertEqual(result.stdout, "")


class Decisions(unittest.TestCase):
    def test_threshold_rule_and_review_override(self):
        module = load_module()
        answers = {"yes": {"type": "noul", "noul": 0.9}, "no": {"type": "noul", "noul": 0.05},
                   "band": {"type": "noul", "noul": 0.7}, "pick": {"type": "choice", "choice": "a", "confidence": 0.9},
                   "weak": {"type": "score", "score": 1, "confidence": 0.6}, "flagged": {"type": "noul", "noul": 0.99}}
        self.assertEqual(module.decisions(answers, {"flagged": ["x"]}, 0.85),
                         {"yes": "act", "no": "skip", "band": "human", "pick": "act", "weak": "human", "flagged": "human"})

    def test_threshold_out_of_range_exits_2(self):
        request = json.dumps({"state": "x", "questions": {"q": {"type": "noul", "instructions": "x?"}}})
        self.assertEqual(run("jev_decide.py", "--dry-run", "--threshold", "0.3", stdin=request).returncode, 2)


class NudgeHook(unittest.TestCase):
    HOOK = SCRIPTS.parent / "hooks/nudge_classifier.py"

    def fire(self, prompt, tool="Agent", env=None):
        event = json.dumps({"tool_name": tool, "tool_input": {"prompt": prompt, "subagent_type": "general-purpose"}})
        result = subprocess.run([sys.executable, str(self.HOOK)], input=event, capture_output=True, text=True,
                                env={**os.environ, **(env or {})})
        self.assertEqual(result.returncode, 0)
        return json.loads(result.stdout)["hookSpecificOutput"]["updatedInput"] if result.stdout else None

    def test_nudges_many_item_judgment_and_keeps_other_fields(self):
        for prompt in ("Classify each of these 40 search terms as a negative keyword or not.",
                       "Triage the open PRs into merge-risk tiers:\n1) docs\n2) deps\n3) auth",
                       "Score every ad against the checklist.",
                       "Check each of these 40 pages for thin content.",
                       "Check these pages for thin content:\n- /a\n- /b\n- /c"):
            with self.subTest(prompt=prompt):
                updated = self.fire(prompt, tool="Task")
                self.assertTrue(updated["prompt"].startswith(prompt))
                self.assertIn("[classifier-nudge]", updated["prompt"])
                self.assertEqual(updated["subagent_type"], "general-purpose")

    def test_stays_silent(self):
        cases = ("Fix the flaky login test.", "Score the page.", "Leaf worker: classify these 40 keywords.",
                 "Use classifier-skill to sort these 30 pages.", "Rank these 5 pages. [classifier-nudge] already",
                 # "check" in everyday coding prompts is usually a deterministic check, not a judgment per item
                 "Read src/app.py and check the links in the README still resolve.",
                 "Check the files under scripts/ for unused imports and report file:line.",
                 "Run the tests and check the results.")
        for prompt in cases:
            with self.subTest(prompt=prompt):
                self.assertIsNone(self.fire(prompt))
        self.assertIsNone(self.fire("Classify these 40 keywords.", tool="Bash"))
        self.assertIsNone(self.fire("Classify these 40 keywords.", env={"CLASSIFIER_NUDGE": "off"}))

    def test_fails_open_on_bad_input(self):
        result = subprocess.run([sys.executable, str(self.HOOK)], input="not json", capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (0, ""))


class ReviewFixes(unittest.TestCase):
    """Regression cases from the 2026-09-25 code-owl review of the redaction, size, and nudge changes."""

    def setUp(self):
        self.module = load_module()
        spec = importlib.util.spec_from_file_location("nudge", SCRIPTS.parent / "hooks/nudge_classifier.py")
        self.hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.hook)

    def test_secret_named_fields_and_quoted_values_are_scrubbed_whole(self):
        scrubbed, count = self.module.redact({"password": "hunter2hunter2", "Access_Token": "opaquecredential",
                                              "tokens": 5, "nested": {"client_secret": "abc"}})
        self.assertEqual(count, 3)
        self.assertEqual(scrubbed["tokens"], 5)
        self.assertNotIn("hunter2", json.dumps(scrubbed))
        self.assertEqual(self.module.redact('password="correct horse battery staple"')[0],
                         "password=[REDACTED:secret_value]")

    def test_prose_and_url_structure_survive(self):
        for text in ("The token: limit was reached", "token: identifiers are normalized", "tokens=5000"):
            self.assertEqual(self.module.redact(text), (text, 0))
        self.assertEqual(self.module.redact("?token=abc123def456&x=1")[0], "?token=[REDACTED:secret_value]&x=1")

    def test_size_counts_dense_scripts_and_every_payload_field(self):
        self.assertGreater(self.module.estimated_tokens("界" * 100), 4 * self.module.estimated_tokens("a" * 100))
        questions = {"q": {"type": "noul", "instructions": "x?"}}
        with mock.patch.object(sys, "stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                self.module.check_size({"state": "界" * 30_000, "questions": questions})
            with self.assertRaises(SystemExit):
                self.module.check_size({"state": "x", "questions": questions, "trace": "x" * 300_000})
            self.module.check_size({"state": "a" * 10_000, "questions": questions})

    def test_nudge_ignores_judging_words_that_are_not_the_task(self):
        for prompt in ("Refactor the route handlers in these 4 files to use async.",
                       "Find where the sort comparator is defined across the 12 files in src/.",
                       "Fix the filter bug. Steps:\n1. read utils.py\n2. add a test\n3. run pytest",
                       "Add a score column to the leaderboard table and migrate 3 records.",
                       "Investigate the failure. Filter logs if useful.\n- reproduce it\n- inspect the stack\n- report it",
                       "Score these 2 pages.",
                       "Classify each of these 40 items. Do not call any other skills."):
            with self.subTest(prompt=prompt):
                self.assertFalse(self.hook.wants_nudge(prompt))

    def test_nudge_covers_check_and_list_introductions(self):
        for prompt in ("Check each of these 40 findings against the policy.",
                       "Sort these 30 pages into sections.",
                       "Classify these:\n- a\n- b\n- c",
                       "Review this diff and label each finding by severity."):
            with self.subTest(prompt=prompt):
                self.assertTrue(self.hook.wants_nudge(prompt))


class BatchPartialFailure(unittest.TestCase):
    """A request that fails mid-batch keeps the answers already printed, says where it stopped, and logs the run."""

    def test_stops_with_summary_and_log(self):
        module = load_module()
        good = {"model": "typesafe/jev-1.13", "answers": {"q": {"type": "noul", "noul": 0.9}}}
        replies = [good, OSError("network down"), OSError("network down"), OSError("network down")]

        class Response:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return io.BytesIO(json.dumps(self.body).encode())

            def __exit__(self, *exc):
                return False

        def urlopen(request, timeout):
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return Response(reply)

        with tempfile.TemporaryDirectory() as tmp:
            batch, log = Path(tmp) / "states.jsonl", Path(tmp) / "calls.jsonl"
            batch.write_text('"one"\n"two"\n"three"\n')
            out, err = io.StringIO(), io.StringIO()
            template = {"questions": {"q": {"type": "noul", "instructions": "Billing?"}}}
            with mock.patch.object(module.urllib.request, "urlopen", urlopen), \
                    mock.patch.object(module.time, "sleep"), \
                    mock.patch.object(sys, "argv", ["jev_decide.py", "--batch", str(batch)]), \
                    mock.patch.object(sys, "stdin", io.StringIO(json.dumps(template))), \
                    mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err), \
                    mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-not-a-key",
                                                 "CLASSIFIER_SKILL_LOG": str(log)}), \
                    self.assertRaises(SystemExit) as caught:
                module.main()
            self.assertEqual(caught.exception.code, 1)
            self.assertEqual([json.loads(l)["line"] for l in out.getvalue().splitlines()], [1])
            self.assertIn("stopped at batch line 2", err.getvalue())
            record = json.loads(log.read_text().splitlines()[-1])
            self.assertEqual((record["stopped_at_line"], record["answered"]), (2, 1))


def load_engine():
    spec = importlib.util.spec_from_file_location("classify_items", SCRIPTS / "classify_items.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ItemsEngine(unittest.TestCase):
    """classify_items.py: contract 1.5. Provider replies are recorded shapes, never live calls."""

    SHEET = {"sheet": "test-terms", "version": 2, "contract": "1.3", "data": "cloud_ok", "min_items": 2,
             "fields": {"id": "term", "card": ["term", "campaign"]}, "context": "A dental clinic.",
             "questions": {
                 "pile": {"type": "choice", "instructions": "Pile?", "threshold": 0.7,
                          "criteria": {"keep": "k", "drop": "d", "none_fit": "neither"}},
                 "relevant": {"type": "noul", "instructions": "Relevant?", "threshold": 0.9}}}

    @staticmethod
    def answer(choice="keep", confidence=0.9, noul=0.95):
        others = [o for o in ("keep", "drop", "none_fit") if o != choice]
        probabilities = {choice: confidence, others[0]: round(1 - confidence, 6), others[1]: 0.0}
        return {"model": "typesafe/jev-1.13", "usage": {"input_tokens": 30, "cost": 0.00001},
                "answers": {"pile": {"type": "choice", "choice": choice, "confidence": confidence,
                                     "probabilities": probabilities},
                            "relevant": {"type": "noul", "noul": noul}}}

    def judge(self, items, replies, sheet=None, env=None, argv=()):
        """Run the engine; return (exit code, output lines, summary or None, sent payloads, stderr)."""
        module = load_engine()
        queue, sent = list(replies), []

        class Response:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return io.BytesIO(json.dumps(self.body).encode())

            def __exit__(self, *exc):
                return False

        def urlopen(request, timeout):
            sent.append(json.loads(request.data))
            reply = queue.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return Response(reply)

        with tempfile.TemporaryDirectory() as tmp:
            paths = {name: Path(tmp) / name for name in ("sheet.json", "items.jsonl", "out.jsonl", "summary.json")}
            paths["sheet.json"].write_text(json.dumps(sheet or self.SHEET))
            paths["items.jsonl"].write_text("".join(json.dumps(i) + "\n" for i in items))
            err = io.StringIO()
            argv = ["classify_items.py", "--sheet", str(paths["sheet.json"]), "--items", str(paths["items.jsonl"]),
                    "--out", str(paths["out.jsonl"]), "--summary", str(paths["summary.json"]), *argv]
            code = 0
            with mock.patch.object(module.jev.urllib.request, "urlopen", urlopen), \
                    mock.patch.object(module.jev.time, "sleep"), mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(sys, "stderr", err), \
                    mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-not-a-key",
                                                 "CLASSIFIER_SKILL_LOG": "off", **(env or {})}):
                try:
                    module.main()
                except SystemExit as exc:
                    code = exc.code
            lines = ([json.loads(l) for l in paths["out.jsonl"].read_text().splitlines()]
                     if paths["out.jsonl"].exists() else [])
            summary = json.loads(paths["summary.json"].read_text()) if paths["summary.json"].exists() else None
        return code, lines, summary, sent, err.getvalue()

    ITEMS = [{"term": f"term {n}", "campaign": "Implants", "cost": 12.5} for n in range(4)]

    def test_every_id_once_in_order_with_versions(self):
        code, lines, summary, _, err = self.judge(self.ITEMS, [self.answer()] * 4)
        self.assertEqual(code, 0)
        self.assertEqual([l["id"] for l in lines], [i["term"] for i in self.ITEMS])
        self.assertTrue(all(l["versions"] == {"sheet": "test-terms@2", "recipe": None, "model": "typesafe/jev-1.13",
                                              "contract": "1.5", "context": "02f189c76132"} for l in lines))
        self.assertEqual((summary["complete"], summary["items_in"], summary["items_out"], summary["answered"]),
                         (True, 4, 4, 4))
        self.assertIn("Classifier: 4/0/0 (none)", err)

    def test_duplicate_ids_exit_2_before_sending(self):
        code, lines, summary, sent, _ = self.judge([self.ITEMS[0], self.ITEMS[0]], [])
        self.assertEqual((code, lines, summary, sent), (2, [], None, []))

    def test_transport_failure_keeps_earlier_answers(self):
        replies = [self.answer(), OSError("down"), OSError("down"), OSError("down")]
        code, lines, summary, sent, _ = self.judge(self.ITEMS, replies)
        self.assertEqual(code, 0)
        self.assertEqual([l["status"] for l in lines], ["answered", "unanswered", "unanswered", "unanswered"])
        self.assertEqual([l.get("reason") for l in lines], [None, "transport", "not_sent", "not_sent"])
        self.assertEqual(summary["unanswered"], {"transport": 1, "not_sent": 2})
        self.assertTrue(summary["complete"] and summary["degraded"])
        self.assertEqual(len(sent), 4)  # one success plus three tries of the failing item; nothing after

    def test_invalid_answer_affects_only_that_item(self):
        bad = self.answer()
        bad["answers"]["pile"]["choice"] = "maybe"
        code, lines, summary, _, _ = self.judge(self.ITEMS, [self.answer(), bad, self.answer(), self.answer()])
        self.assertEqual([l.get("reason") for l in lines], [None, "invalid_answer", None, None])
        self.assertEqual((summary["answered"], summary["unanswered"]), (3, {"invalid_answer": 1}))

    def test_below_min_items_sends_nothing(self):
        code, lines, summary, sent, _ = self.judge(self.ITEMS[:1], [])
        self.assertEqual((code, sent), (0, []))
        self.assertEqual(lines[0]["reason"], "below_min_items")
        self.assertTrue(summary["bypass"])
        self.assertFalse(summary["degraded"])

    def test_recurring_sheet_runs_a_single_item(self):
        code, lines, _, sent, _ = self.judge(self.ITEMS[:1], [self.answer()], sheet={**self.SHEET, "recurring": True})
        self.assertEqual((len(sent), lines[0]["status"]), (1, "answered"))

    def test_only_card_fields_and_context_are_sent(self):
        _, _, _, sent, _ = self.judge(self.ITEMS[:2], [self.answer()] * 2)
        self.assertEqual(sent[0]["state"], {"context": "A dental clinic.", "item": {"term": "term 0",
                                                                                     "campaign": "Implants"}})
        self.assertNotIn("threshold", json.dumps(sent[0]["questions"]))

    def test_dispositions_follow_thresholds(self):
        replies = [self.answer(confidence=0.9, noul=0.95),   # both confident
                   self.answer(confidence=0.6, noul=0.02),   # pile below 0.7; relevant confidently false
                   self.answer(choice="none_fit", confidence=0.95, noul=0.5),  # fallback; noul near 0.5
                   self.answer(confidence=0.9, noul=0.8)]    # relevant between thresholds
        _, lines, summary, _, _ = self.judge(self.ITEMS, replies)
        self.assertEqual([l["dispositions"] for l in lines], [
            {"pile": "answered", "relevant": "answered"}, {"pile": "human", "relevant": "skip"},
            {"pile": "human", "relevant": "human"}, {"pile": "answered", "relevant": "human"}])
        self.assertEqual((summary["human_by_question"], summary["skip_by_question"]),
                         ({"pile": 2, "relevant": 2}, {"relevant": 1}))

    def test_local_only_sheet_refuses_cloud_endpoint(self):
        code, lines, summary, sent, _ = self.judge(self.ITEMS, [], sheet={**self.SHEET, "data": "local_only"})
        self.assertEqual((code, sent), (0, []))
        self.assertEqual(summary["unanswered"], {"refused_host": 4})

    def test_auth_failure_stops_with_no_key(self):
        module = load_module()
        denied = module.urllib.error.HTTPError("https://openrouter.ai", 401, "no", {}, io.BytesIO(b"bad key"))
        _, _, summary, sent, _ = self.judge(self.ITEMS, [denied])
        self.assertEqual((len(sent), summary["unanswered"]), (1, {"no_key": 1, "not_sent": 3}))

    def test_recipe_questions_merge_with_sheet(self):
        sheet = {**self.SHEET, "recipe": "search-intent@1",
                 "questions": {"intent": {"threshold": 0.6}, **self.SHEET["questions"]}}
        _, _, _, sent, _ = self.judge(self.ITEMS[:2], [], sheet=sheet, argv=["--dry-run"])
        self.assertEqual(sent, [])  # dry run sends nothing
        module = load_engine()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sheet.json"
            path.write_text(json.dumps(sheet))
            _, questions, thresholds = module.load_sheet(str(path))
        self.assertEqual(set(questions), {"intent", "pile", "relevant"})
        self.assertEqual(thresholds["intent"], 0.6)
        self.assertIn("wants_service", questions["intent"]["criteria"])

    def test_bad_sheets_exit_2(self):
        for change in ({"contract": "2.0"}, {"data": "anywhere"}, {"fields": {"card": []}}, {"extra": 1},
                       {"recipe": "missing@1"}, {"questions": {"pile": {**self.SHEET["questions"]["pile"],
                                                                        "threshold": 0.3}}}):
            with self.subTest(change=change):
                code, _, summary, sent, _ = self.judge(self.ITEMS, [], sheet={**self.SHEET, **change})
                self.assertEqual((code, summary, sent), (2, None, []))

    def test_contract_version(self):
        result = run("classify_items.py", "--contract-version")
        self.assertEqual(result.stdout.strip(), "1.5")

    def test_context_file_replaces_sheet_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = Path(tmp) / "account.txt"
            context.write_text("An orthodontist in Leeds.\n")
            _, lines, _, sent, _ = self.judge(self.ITEMS[:2], [self.answer()] * 2, argv=["--context", str(context)])
        self.assertEqual(sent[0]["state"]["context"], "An orthodontist in Leeds.")
        self.assertNotEqual(lines[0]["versions"]["context"], None)

    def test_malformed_responses_are_invalid_not_crashes(self):
        extra = self.answer()
        extra["answers"]["surprise"] = {"type": "noul", "noul": 0.9}
        odd_usage = {**self.answer(), "usage": {"cost": "unknown", "input_tokens": "many"}}
        replies = [[], extra, {**self.answer(), "usage": []}, odd_usage]
        code, lines, summary, _, _ = self.judge(self.ITEMS, replies)
        self.assertEqual(code, 0)
        self.assertEqual([l.get("reason") for l in lines], ["invalid_answer"] * 3 + [None])
        self.assertTrue(summary["complete"])

    def test_old_summary_is_removed_before_a_run(self):
        module = load_engine()
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.json"
            summary.write_text('{"complete": true, "items_out": 4}')
            sheet, items = Path(tmp) / "sheet.json", Path(tmp) / "items.jsonl"
            sheet.write_text(json.dumps(self.SHEET))
            items.write_text("".join(json.dumps(i) + "\n" for i in self.ITEMS))
            argv = ["classify_items.py", "--sheet", str(sheet), "--items", str(items), "--out",
                    str(Path(tmp) / "out.jsonl"), "--summary", str(summary)]

            def crash(request, timeout):
                raise KeyboardInterrupt

            with mock.patch.object(module.jev.urllib.request, "urlopen", crash), mock.patch.object(sys, "argv", argv), \
                    mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-not-a-key", "CLASSIFIER_SKILL_LOG": "off"}), \
                    self.assertRaises(KeyboardInterrupt):
                module.main()
            self.assertFalse(summary.exists())

    def test_out_and_summary_must_differ(self):
        module = load_engine()
        with tempfile.TemporaryDirectory() as tmp:
            same = str(Path(tmp) / "result.json")
            argv = ["classify_items.py", "--sheet", "x", "--items", "y", "--out", same, "--summary", same]
            with mock.patch.object(sys, "argv", argv), mock.patch.object(sys, "stderr", io.StringIO()), \
                    self.assertRaises(SystemExit) as caught:
                module.main()
        self.assertEqual(caught.exception.code, 2)

    def test_bad_margin_exits_before_sending(self):
        for margin in ("wide", -0.1, 1.5):
            with self.subTest(margin=margin):
                code, _, _, sent, _ = self.judge(self.ITEMS, [], sheet={**self.SHEET, "margin": margin})
                self.assertEqual((code, sent), (2, []))

    def test_payload_too_large_for_provider(self):
        module = load_module()
        big = module.urllib.error.HTTPError("https://openrouter.ai", 413, "big", {}, io.BytesIO(b"too big"))
        _, lines, summary, sent, _ = self.judge(self.ITEMS, [big] + [self.answer()] * 3)
        # one oversized item says nothing about the others, so they are still sent
        self.assertEqual([l.get("reason") for l in lines], ["too_large", None, None, None])
        self.assertEqual((len(sent), summary["answered"]), (4, 3))


class ScoreLabels(unittest.TestCase):
    def test_accuracy_coverage_and_holdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            labels, answers = Path(tmp) / "labels.jsonl", Path(tmp) / "answers.jsonl"
            labels.write_text("".join(json.dumps({"id": f"i{n}", "label": "a" if n % 2 else "b"}) + "\n"
                                      for n in range(20)))
            lines = []
            for n in range(20):
                truth = "a" if n % 2 else "b"
                pick, conf = (truth, 0.95) if n < 16 else ("a" if truth == "b" else "b", 0.55)
                lines.append({"id": f"i{n}", "status": "answered", "answers": {"q": {
                    "type": "choice", "choice": pick, "confidence": conf}}})
            lines.append({"id": "i99", "answer": "a"})  # unlabeled lines are ignored
            answers.write_text("".join(json.dumps(l) + "\n" for l in lines))
            result = run("score_labels.py", "--answers", str(answers), "--labels", str(labels), "--question", "q",
                         "--target", "0.9")
        report = json.loads(result.stdout)
        self.assertEqual(report["all"]["accuracy"], 0.8)
        self.assertEqual(report["all"]["at_threshold"]["0.9"],
                         {"coverage": 0.8, "accuracy": 1.0, "correct": 16, "kept": 16})
        self.assertEqual(report["calibration"]["holdout_items"], 10)
        self.assertIsNotNone(report["calibration"]["threshold"])

    def score(self, labels, answers, *extra):
        with tempfile.TemporaryDirectory() as tmp:
            lpath, apath = Path(tmp) / "labels.jsonl", Path(tmp) / "answers.jsonl"
            lpath.write_text("".join(json.dumps(l) + "\n" for l in labels))
            apath.write_text("".join(json.dumps(a) + "\n" for a in answers))
            result = run("score_labels.py", "--answers", str(apath), "--labels", str(lpath), "--question", "q", *extra)
        return json.loads(result.stdout)

    def test_missing_answers_count_against_recall(self):
        report = self.score([{"id": "1", "label": "a"}, {"id": "2", "label": "a"}], [{"id": "1", "answer": "a"}])
        self.assertEqual((report["all"]["items"], report["all"]["per_class"]["a"]["recall"],
                          report["all"]["at_threshold"]["0.5"]["coverage"]), (2, 0.5, 0.5))

    def test_review_flags_are_never_automated(self):
        answers = [{"id": "1", "answers": {"q": {"type": "noul", "noul": 0.55}}, "review": {"q": ["near 0.5"]}},
                   {"id": "2", "answers": {"q": {"type": "noul", "noul": 0.97}}, "review": {}}]
        report = self.score([{"id": "1", "label": True}, {"id": "2", "label": True}], answers)
        self.assertEqual(report["all"]["at_threshold"]["0.5"]["kept"], 1)
        self.assertEqual(report["all"]["accuracy"], 1.0)  # booleans and "true" compare equal

    def test_numeric_score_labels_match(self):
        answers = [{"id": "1", "answers": {"q": {"type": "score", "score": 2, "confidence": 0.9}}}]
        self.assertEqual(self.score([{"id": "1", "label": 2}], answers)["all"]["accuracy"], 1.0)

    def test_labels_never_reach_the_engine(self):
        sheet = json.loads((SCRIPTS.parent / "evals/files/search-terms-sheet.json").read_text())
        self.assertNotIn("label_intent", sheet["fields"]["card"])



class FakeProvider:
    """A local HTTP server that answers each POST with the next queued (status, headers, body) and records requests."""

    def __init__(self, replies):
        import http.server
        import threading
        self.replies, self.requests = list(replies), []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _reply(self):
                length = int(self.headers.get("Content-Length") or 0)
                owner.requests.append({"method": self.command, "path": self.path,
                                       "authorization": self.headers.get("Authorization"),
                                       "body": self.rfile.read(length) if length else b""})
                status, headers, body = owner.replies.pop(0) if owner.replies else (500, {}, b"no reply queued")
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_POST = do_GET = _reply

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/decide"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def call_compatible(provider, request, *args):
    env = {"CLASSIFIER_COMPATIBLE_URL": provider.url, "CLASSIFIER_COMPATIBLE_KEY": "sk-test-secret-value",
           "CLASSIFIER_COMPATIBLE_MODEL": "test-model", "CLASSIFIER_PROVIDER": ""}
    return run("jev_decide.py", "--provider", "compatible", *args, stdin=json.dumps(request), env=env)


class ResponseGate(unittest.TestCase):
    """Redirects, non-object replies, and unasked answers never reach a caller as usable answers."""

    QUESTION = {"q": {"type": "noul", "instructions": "Refund?"}}
    GOOD = {"model": "test-model", "answers": {"q": {"type": "noul", "noul": 0.97}}}

    def serve(self, *replies):
        provider = FakeProvider(replies)
        self.addCleanup(provider.close)
        return provider

    def test_redirect_is_refused_and_the_key_never_follows(self):
        target = self.serve((200, {}, json.dumps(self.GOOD).encode()))
        origin = self.serve((302, {"Location": target.url}, b""))
        result = call_compatible(origin, {"state": "Ticket: charged twice", "questions": self.QUESTION})
        self.assertEqual(result.returncode, 1)
        self.assertIn("HTTP 302", result.stderr)
        self.assertEqual(target.requests, [])  # the redirect target saw nothing, so no Authorization header
        self.assertEqual(len(origin.requests), 1)  # a redirect is not retried

    def test_non_object_reply_is_invalid_not_a_crash(self):
        for body in (b"[]", b"null", b'"busy"'):
            with self.subTest(body=body):
                provider = self.serve((200, {}, body))
                result = call_compatible(provider, {"state": "Ticket: charged twice", "questions": self.QUESTION})
                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertIn("not a JSON object", result.stdout)

    def test_unasked_answer_is_invalid_and_gets_no_decision(self):
        extra = {**self.GOOD, "answers": {**self.GOOD["answers"], "delete_account": {"type": "noul", "noul": 0.99}}}
        provider = self.serve((200, {}, json.dumps(extra).encode()))
        result = call_compatible(provider, {"state": "Ticket: charged twice", "questions": self.QUESTION},
                                 "--threshold", "0.9")
        self.assertEqual(result.returncode, 3)
        self.assertIn("delete_account: answer to a question that was not asked", result.stdout)
        self.assertNotIn("decisions", json.loads(result.stdout))

    def test_batch_marks_bad_replies_invalid_and_keeps_going(self):
        provider = self.serve((200, {}, b"[]"), (200, {}, json.dumps(self.GOOD).encode()),
                              (200, {}, json.dumps({**self.GOOD, "usage": "free"}).encode()))
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
            handle.write('"Ticket one"\n"Ticket two"\n"Ticket three"\n')
        self.addCleanup(os.unlink, handle.name)
        result = call_compatible(provider, {"questions": self.QUESTION}, "--batch", handle.name)
        lines = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(["invalid" in line for line in lines], [True, False, True])
        self.assertNotIn("Traceback", result.stderr)


class EngineFailures(unittest.TestCase):
    """classify_items.py: one item's failure stays with that item; a run-wide failure marks the rest not_sent."""

    SHEET, ITEMS, judge, answer = ItemsEngine.SHEET, ItemsEngine.ITEMS, ItemsEngine.judge, staticmethod(ItemsEngine.answer)

    def test_bad_request_stops_and_marks_the_rest_not_sent(self):
        module = load_module()
        bad = module.urllib.error.HTTPError("https://openrouter.ai", 400, "bad", {}, io.BytesIO(b"bad request"))
        _, lines, summary, sent, _ = self.judge(self.ITEMS, [bad])
        self.assertEqual([l.get("reason") for l in lines], ["bad_request", "not_sent", "not_sent", "not_sent"])
        self.assertEqual((len(sent), summary["unanswered"]), (1, {"bad_request": 1, "not_sent": 3}))

    def test_empty_card_is_unanswered_not_a_failed_run(self):
        items = self.ITEMS[:2] + [{"term": "term 9", "campaign": None}] + self.ITEMS[2:]
        sheet = {**self.SHEET, "fields": {"id": "term", "card": ["campaign"]}}
        code, lines, summary, sent, _ = self.judge(items, [self.answer()] * 4, sheet=sheet)
        self.assertEqual(code, 0)
        self.assertEqual([l.get("reason") for l in lines], [None, None, "empty_card", None, None])
        self.assertEqual((len(sent), summary["answered"], summary["unanswered"]), (4, 4, {"empty_card": 1}))

    def test_model_pin_follows_the_provider(self):
        pins = {"openrouter": "typesafe/jev-1.13", "typesafe": "jev-1.13.0"}
        env = {"TYPESAFE_API_KEY": "test-not-a-key"}
        _, _, summary, sent, _ = self.judge(self.ITEMS, [self.answer()] * 4, sheet={**self.SHEET, "model": pins},
                                            env=env, argv=["--provider", "typesafe"])
        self.assertEqual({p["model"] for p in sent}, {"jev-1.13.0"})
        self.assertEqual(summary["model"], "jev-1.13.0")
        _, _, _, sent, err = self.judge(self.ITEMS, [self.answer()] * 4,
                                        sheet={**self.SHEET, "model": "typesafe/jev-1.13"}, env=env,
                                        argv=["--provider", "typesafe"])
        self.assertEqual({p["model"] for p in sent}, {"jev-1.13.0"})  # an OpenRouter id is never sent to TypeSafe
        self.assertIn("is an OpenRouter id", err)

    def test_bad_model_pin_exits_2(self):
        for pin in ({"nowhere": "x"}, {"typesafe": ""}, 7, {}):
            with self.subTest(pin=pin):
                code, _, _, sent, _ = self.judge(self.ITEMS, [], sheet={**self.SHEET, "model": pin})
                self.assertEqual((code, sent), (2, []))


class EnginePaths(unittest.TestCase):
    """classify_items.py refuses output paths that would delete or overwrite an input, before touching anything."""

    def run_engine(self, tmp, out, summary):
        sheet, items = Path(tmp) / "sheet.json", Path(tmp) / "items.jsonl"
        sheet.write_text(json.dumps(ItemsEngine.SHEET))
        items.write_text("".join(json.dumps(i) + "\n" for i in ItemsEngine.ITEMS))
        named = {"sheet": sheet, "items": items, "out": Path(tmp) / "out.jsonl", "summary": Path(tmp) / "s.json"}
        named["tmp"] = Path(str(named["summary"]) + ".tmp")
        result = run("classify_items.py", "--sheet", str(sheet), "--items", str(items),
                     "--out", str(named[out]), "--summary", str(named[summary]), "--dry-run")
        return result, sheet.read_text(), items.read_text()

    def test_outputs_never_overwrite_inputs(self):
        for out, summary in (("out", "items"), ("items", "summary"), ("sheet", "summary"), ("out", "sheet"),
                             ("tmp", "summary"), ("out", "out")):
            with self.subTest(out=out, summary=summary), tempfile.TemporaryDirectory() as tmp:
                result, sheet_text, items_text = self.run_engine(tmp, out, summary)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(json.loads(sheet_text), ItemsEngine.SHEET)
                self.assertEqual(len(items_text.splitlines()), len(ItemsEngine.ITEMS))

    def test_distinct_paths_still_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _, _ = self.run_engine(tmp, "out", "summary")
            self.assertEqual(result.returncode, 0, result.stderr)


class ScoreAnswers(unittest.TestCase):
    def test_score_is_scored_by_most_likely_level_not_expected_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            labels, answers = Path(tmp) / "labels.jsonl", Path(tmp) / "answers.jsonl"
            labels.write_text('{"id": "a", "label": 2}\n{"id": "b", "label": 0}\n')
            answers.write_text("".join(json.dumps(line) + "\n" for line in (
                {"id": "a", "answers": {"r": {"type": "score", "score": 1.98, "confidence": 0.97,
                                              "probabilities": {"0": 0, "1": 0.02, "2": 0.98}}}, "review": {}},
                {"id": "b", "answers": {"r": {"type": "score", "score": 0.03, "confidence": 0.97,
                                              "probabilities": {"0": 0.97, "1": 0.03, "2": 0}}}, "review": {}})))
            result = run("score_labels.py", "--answers", str(answers), "--labels", str(labels), "--question", "r")
        report = json.loads(result.stdout)["all"]
        self.assertEqual((report["accuracy"], report["confusion"]), (1.0, {"0": {"0": 1}, "2": {"2": 1}}))


def load_eval(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS.parent / "evals" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EvalGrader(unittest.TestCase):
    def grade(self, commands, log_lines=(), tail=""):
        grader = load_eval("grade_evals")
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            events = [json.dumps({"item": {"type": "command_execution", "command": c}}) for c in commands]
            (run_dir / "events.jsonl").write_text("\n".join(events) + "\n" + tail)
            (run_dir / "timing.json").write_text('{"seconds": 1}')
            if log_lines:
                (run_dir / "tmp").mkdir()
                (run_dir / "tmp/calls.jsonl").write_text("\n".join(log_lines) + "\n")
            return grader.facts(run_dir)

    def test_reading_the_script_is_not_a_live_call(self):
        facts = self.grade(["sed -n 1,80p ~/skills/classifier-skill/scripts/jev_decide.py",
                            "python3 scripts/jev_decide.py --contract-version",
                            "python3 scripts/jev_decide.py --help"])
        self.assertFalse(facts["jev_live_call"])
        self.assertTrue(facts["script_source_read"])

    def test_engine_runs_and_logged_calls_count(self):
        facts = self.grade(["python3 /h/skills/classifier-skill/scripts/classify_items.py --sheet s --items i"])
        self.assertTrue(facts["jev_live_call"] and facts["batch_used"])
        self.assertTrue(self.grade([], log_lines=['{"items": 3}'])["jev_live_call"])
        self.assertFalse(self.grade(["python3 scripts/jev_decide.py --dry-run"])["jev_live_call"])

    def test_truncated_lines_are_skipped(self):
        facts = self.grade(["python3 scripts/jev_decide.py --batch b.jsonl"], log_lines=['{"items": 2}', '{"ite'],
                           tail='{"item": {"type": "agent_mess')
        self.assertEqual((facts["jev_live_call"], facts["log_calls"], facts["log_items"]), (True, 1, 2))


class EvalBudget(unittest.TestCase):
    def test_unverifiable_spend_stops_before_any_run(self):
        runner = load_eval("run_evals")
        with mock.patch.object(runner, "key_spend", return_value=None), \
                mock.patch.object(runner, "run_case") as run_case, \
                mock.patch.dict(os.environ, {"CLASSIFIER_EVAL_KEY": "test-not-a-key"}), \
                mock.patch.object(sys, "stdout", io.StringIO()), self.assertRaises(SystemExit) as caught:
            runner.main(["t", "--cases", "1"])
        self.assertEqual(caught.exception.code, 4)
        run_case.assert_not_called()

    def test_budget_reached_stops_before_the_next_run(self):
        runner = load_eval("run_evals")
        spend = iter([0.0, 0.0, 5.0])
        with mock.patch.object(runner, "key_spend", side_effect=lambda: next(spend)), \
                mock.patch.object(runner, "run_case") as run_case, \
                mock.patch.dict(os.environ, {"CLASSIFIER_EVAL_KEY": "test-not-a-key"}), \
                mock.patch.object(sys, "stdout", io.StringIO()), self.assertRaises(SystemExit) as caught:
            runner.main(["t", "--cases", "1,2", "--models", "gpt-5.6-luna-low", "--budget", "3"])
        self.assertEqual((caught.exception.code, run_case.call_count), (4, 1))



class RetryAfter(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def error(self, status=429, retry_after=None):
        headers = __import__("http.client").client.HTTPMessage()  # case-insensitive, as a real response's are
        if retry_after is not None:
            headers["Retry-After"] = retry_after
        error = self.module.urllib.error.HTTPError("https://openrouter.ai", status, "busy", headers, io.BytesIO(b"slow"))
        self.addCleanup(error.close)
        return error

    def test_seconds_and_http_dates_are_read(self):
        import datetime
        import email.utils
        soon = email.utils.format_datetime(datetime.datetime.now(datetime.timezone.utc)
                                           + datetime.timedelta(seconds=30), usegmt=True)
        self.assertEqual(self.module.retry_after(self.error(retry_after="3")), 3.0)
        self.assertAlmostEqual(self.module.retry_after(self.error(retry_after=soon)), 30, delta=2)
        self.assertEqual(self.module.retry_after(self.error(retry_after="Tue, 01 Jan 2000 00:00:00 GMT")), 0.0)
        for unreadable in (None, "", "soon"):
            self.assertIsNone(self.module.retry_after(self.error(retry_after=unreadable)))

    def call(self, *errors):
        queue, sleeps = list(errors), []

        class Response:
            def __enter__(self):
                return io.BytesIO(b'{"answers": {}}')

            def __exit__(self, *exc):
                return False

        def urlopen(request, timeout):
            if queue:
                raise queue.pop(0)
            return Response()

        with mock.patch.object(self.module.urllib.request, "urlopen", urlopen), \
                mock.patch.object(self.module.time, "sleep", sleeps.append), \
                mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-not-a-key"}):
            result = self.module.call_jev({"model": "m"}, self.module.PROVIDERS["openrouter"]["endpoint"], 5)
        return result, sleeps

    def test_short_retry_after_is_waited_out(self):
        result, sleeps = self.call(self.error(retry_after="2"))
        self.assertEqual((result, sleeps), ({"answers": {}}, [2.0]))

    def test_long_retry_after_fails_at_once_and_names_the_wait(self):
        with self.assertRaises(self.module.CallError) as caught:
            self.call(self.error(retry_after="60"))
        self.assertIn("asked to retry after 60s", caught.exception.message)


class FailureLog(unittest.TestCase):
    def test_failed_single_call_is_logged_without_content(self):
        provider = FakeProvider([(401, {}, b"bad key")])
        self.addCleanup(provider.close)
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls.jsonl"
            env = {"CLASSIFIER_COMPATIBLE_URL": provider.url, "CLASSIFIER_COMPATIBLE_MODEL": "m",
                   "CLASSIFIER_PROVIDER": "", "CLASSIFIER_SKILL_LOG": str(log)}
            request = {"state": "Ticket: charged twice", "questions": {"q": {"type": "noul", "instructions": "x"}}}
            result = run("jev_decide.py", "--provider", "compatible", stdin=json.dumps(request), env=env)
            record = json.loads(log.read_text())
        self.assertEqual((result.returncode, record["failed"], record["mode"]), (1, "http_401", "single"))
        self.assertNotIn("charged twice", json.dumps(record))

    def test_report_counts_failures_and_survives_odd_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls.jsonl"
            log.write_text("\n".join([
                json.dumps({"ts": "2026-09-29T10:00:00+00:00", "items": 3, "flagged": 1, "cost": "0.0002",
                            "recipe": "routing", "questions": {"q": "choice"}}),
                json.dumps({"ts": "2026-09-29T11:00:00+00:00", "items": 1, "failed": "http_401", "cost": "n/a"}),
                "[1, 2]", "not json"]) + "\n")
            result = run("reshape_report.py", "--log", str(log), "--json")
            text = run("reshape_report.py", "--log", str(log))
        report = json.loads(result.stdout)
        self.assertEqual((report["calls"], report["items"], report["failed"], report["cost"]), (2, 4, 1, 0.0002))
        self.assertEqual(text.returncode, 0, text.stderr)


class ScoreLabelArguments(unittest.TestCase):
    def test_out_of_range_holdout_or_target_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.jsonl"
            path.write_text('{"id": "a", "label": "x"}\n')
            for extra in (["--holdout", "1.5"], ["--holdout", "0"], ["--holdout", "-0.2"], ["--target", "90"]):
                with self.subTest(extra=extra):
                    result = run("score_labels.py", "--answers", str(path), "--labels", str(path), "--question", "q",
                                 "--target", "0.9", *extra)
                    self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
