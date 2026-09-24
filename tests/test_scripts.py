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
                     "reshape_noted": True},
                    {"ts": "2026-09-24T01:00:00+00:00", "items": 1, "flagged": 0, "invalid": 0,
                     "questions": {"q": "noul"}, "reshape_noted": False}]
            log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            result = run("reshape_report.py", "--log", str(log), "--json")
            summary = json.loads(result.stdout)
            self.assertEqual((summary["calls"], summary["items"], summary["reshape_noted"]), (2, 11, 1))
            self.assertEqual(summary["by_recipe"]["card-sort"]["flag_rate"], 0.2)


if __name__ == "__main__":
    unittest.main()


class SyncTheBuildLoop(unittest.TestCase):
    """Publishes to a throwaway bare repository, never the real downstream."""

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    def sync(self, remote, *args):
        head = self.git("rev-parse", "HEAD", cwd=SCRIPTS.parent)
        return run("sync_the_build_loop.py", "--remote", remote, "--source-ref", head, *args)

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
            canonical_tree = self.git("rev-parse", "HEAD^{tree}", cwd=SCRIPTS.parent)
            self.assertEqual(self.git("--git-dir", remote, "rev-parse", f"{published}^{{tree}}"), canonical_tree)
