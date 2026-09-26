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


if __name__ == "__main__":
    unittest.main()


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
                       "Score every ad against the checklist."):
            with self.subTest(prompt=prompt):
                updated = self.fire(prompt, tool="Task")
                self.assertTrue(updated["prompt"].startswith(prompt))
                self.assertIn("[classifier-nudge]", updated["prompt"])
                self.assertEqual(updated["subagent_type"], "general-purpose")

    def test_stays_silent(self):
        cases = ("Fix the flaky login test.", "Score the page.", "Leaf worker: classify these 40 keywords.",
                 "Use classifier-skill to sort these 30 pages.", "Rank these 5 pages. [classifier-nudge] already")
        for prompt in cases:
            with self.subTest(prompt=prompt):
                self.assertIsNone(self.fire(prompt))
        self.assertIsNone(self.fire("Classify these 40 keywords.", tool="Bash"))
        self.assertIsNone(self.fire("Classify these 40 keywords.", env={"CLASSIFIER_NUDGE": "off"}))

    def test_fails_open_on_bad_input(self):
        result = subprocess.run([sys.executable, str(self.HOOK)], input="not json", capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (0, ""))
