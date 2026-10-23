"""Deterministic controls and paired-run reporting, using offline SDK responses."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI

from policy_compiler.baselines import ceiling_copy_policy, keyword_policy
from policy_compiler.compare import compare, main
from policy_compiler.evaluate import FIXTURES, assess, oracle_policy, read_suite
from policy_compiler.references import recipient_mentions


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.context, self.suite = read_suite(FIXTURES / "context.json", FIXTURES / "cases.json")

    def test_ceiling_copy_passes_limits_but_grants_unneeded_task_scope(self):
        case = self.suite.cases[2]
        policy = ceiling_copy_policy(case.request, self.context)
        result = assess(case, policy)
        self.assertFalse(result["scope_correct"])
        self.assertIn("read:project_y_update", result["overgrants"])
        self.assertIn("operation:email.send", result["overgrants"])

    def test_keywords_handle_clear_requests_and_ambiguous_identity(self):
        for index in (0, 2, 9, 10, 12, 13, 14, 15, 16, 17, 18, 19):
            case = self.suite.cases[index]
            with self.subTest(case=case.id):
                result = assess(case, keyword_policy(case.request, self.context))
                self.assertTrue(result["scope_correct"], result)

    def test_trusted_reference_binding_prefers_full_name_and_preserves_case(self):
        people, ambiguous = recipient_mentions("Contact CASEY PATEL.", self.context)
        self.assertEqual(people, [dict(recipient_id="casey_patel", request_reference="CASEY PATEL")])
        self.assertEqual(ambiguous, [])
        people, ambiguous = recipient_mentions("Contact Casey.", self.context)
        self.assertFalse(people)
        self.assertEqual(set(ambiguous[0]["candidate_ids"]), {"casey_patel", "casey_morgan"})

    def test_comparison_has_two_bounded_live_methods_and_two_offline_controls(self):
        import openrouter_client as backend
        requests = []
        with tempfile.TemporaryDirectory() as temp:
            cases_path = Path(temp) / "cases.json"
            raw = self.suite.model_dump()
            raw["cases"] = raw["cases"][:2]
            for case in raw["cases"]:
                case["request"] += " Please."
            cases_path.write_text(json.dumps(raw))
            _, suite = read_suite(FIXTURES / "context.json", cases_path)
            cases = {c.request: c for c in suite.cases}
            def handler(request):
                payload = json.loads(request.content)
                requests.append(payload)
                supplied = json.loads(payload["messages"][-1]["content"])
                case = cases[supplied["original_request"]]
                if "effective_permissions" in supplied:
                    candidate = dict(status="ready", reads=[dict(resource_id=r, basis="explicit")
                        for r in case.expected.required_reads], actions=[], ambiguities=[], denial_reason=None)
                else:
                    candidate = oracle_policy(case, self.context)
                return httpx.Response(200, json={
                    "id": "offline", "object": "chat.completion", "created": 0, "model": "test/comparison",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(candidate)},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})
            client = OpenAI(base_url=backend.OPENROUTER_BASE_URL, api_key="offline-test-key",
                            http_client=httpx.Client(transport=httpx.MockTransport(handler)))
            with client, patch.object(backend, "get_client", return_value=client), redirect_stdout(io.StringIO()):
                run = compare(FIXTURES / "context.json", cases_path, Path(temp) / "runs", model="test/comparison")
                manifest = json.loads((run / "comparison.json").read_text())
                self.assertEqual(len(requests), 4)
                self.assertTrue(manifest["sources_unchanged"])
                for method in ("original", "revised", "keywords"):
                    self.assertEqual(manifest["metrics"][method]["scope_correct"], 2)
                self.assertEqual(manifest["metrics"]["ceiling_copy"]["scope_correct"], 0)
                text = (run / "COMPARISON.md").read_text()
                self.assertIn("2 synthetic requests", text)
                self.assertNotIn("frozen before these requests were written", text)
                with patch.object(backend, "get_client", side_effect=AssertionError("Offline only")):
                    main(["--report-only", str(run)])
                self.assertEqual(text, (run / "COMPARISON.md").read_text())
                manifest["metrics"]["revised"]["scope_correct"] = 0
                path = run / "comparison.json"
                original = path.read_text()
                path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, "comparison metrics disagree"):
                    main(["--report-only", str(run)])
                self.assertEqual(text, (run / "COMPARISON.md").read_text())
                path.write_text(original)
                child = run / manifest["methods"]["revised"]
                path = child / "metrics.json"
                original = path.read_text()
                cached = json.loads(original)
                cached["scope_correct"] = 0
                path.write_text(json.dumps(cached))
                with self.assertRaisesRegex(ValueError, "method metrics disagree"):
                    main(["--report-only", str(run)])
                path.write_text(original)
                path = child / "source_snapshot/policy_compiler/task_compiler.py"
                original = path.read_text()
                path.write_text(original + "\n")
                with self.assertRaisesRegex(ValueError, "snapshot differs"):
                    main(["--report-only", str(run)])


if __name__ == "__main__":
    unittest.main()
