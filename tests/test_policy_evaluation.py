"""Offline checks for annotation scoring, provenance, and bounded SDK evaluation."""

from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI

from policy_compiler import PolicyValidationError, validate_policy
from policy_compiler.evaluate import (
    FIXTURES, MAX_OUTPUT_TOKENS, assess, main, oracle_policy, read_suite, run_evaluation, summarize,
    validated_metrics,
)


class PolicyEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.context, self.suite = read_suite(FIXTURES / "context.json", FIXTURES / "cases.json")

    def test_all_twenty_annotations_are_expressible_under_one_context(self):
        self.assertEqual(len(self.suite.cases), 20)
        self.assertEqual(sum(c.expected.status == "ready" for c in self.suite.cases), 13)
        self.assertEqual(sum(c.expected.status == "denied" for c in self.suite.cases), 4)
        self.assertEqual(sum(c.expected.status == "clarification_required" for c in self.suite.cases), 3)
        for case in self.suite.cases:
            with self.subTest(case=case.id):
                policy = validate_policy(oracle_policy(case, self.context), case.request, self.context)
                self.assertTrue(assess(case, policy)["full_annotation_match"])

    def test_ceiling_valid_extra_reads_are_task_overgrants(self):
        case = self.suite.cases[0]
        candidate = oracle_policy(case, self.context)
        read = deepcopy(candidate["reads"][0])
        read["resource_id"] = "project_y_update"
        candidate["reads"].append(read)
        policy = validate_policy(candidate, case.request, self.context)
        result = assess(case, policy)
        self.assertTrue(result["status_correct"])
        self.assertFalse(result["scope_correct"])
        self.assertEqual(result["overgrants"], ["read:project_y_update"])

    def test_missing_reads_operations_scopes_and_counts_are_separate(self):
        case = self.suite.cases[10]  # twice, max_calls=2
        candidate = oracle_policy(case, self.context)
        candidate["reads"] = []
        candidate["actions"][0]["max_calls"] = 1
        result = assess(case, validate_policy(candidate, case.request, self.context))
        self.assertIn("read:project_x_update", result["undergrants"])
        self.assertIn("slack.send_message:max_calls:1<2", result["undergrants"])
        candidate["actions"][0]["max_calls"] = 3
        result = assess(case, validate_policy(candidate, case.request, self.context))
        self.assertIn("slack.send_message:max_calls:3>2", result["overgrants"])

    def test_optional_reads_and_evidence_labels_are_scored_separately(self):
        case = self.suite.cases[2]
        candidate = oracle_policy(case, self.context)
        candidate["reads"][1]["evidence"]["basis"] = "explicit"
        result = assess(case, validate_policy(candidate, case.request, self.context))
        self.assertTrue(result["scope_correct"])
        self.assertFalse(result["full_annotation_match"])
        self.assertEqual(result["basis_errors"], ["read:coworker_directory"])
        candidate["reads"] = candidate["reads"][:1]
        self.assertTrue(assess(case, validate_policy(candidate, case.request, self.context))["full_annotation_match"])

    def test_wrong_status_and_validation_failure_never_count_as_denial_success(self):
        case = self.suite.cases[16]
        self.assertFalse(assess(case, None)["scope_correct"])
        candidate = oracle_policy(case, self.context)
        candidate.update(status="ready", denial_reason=None)
        result = assess(case, validate_policy(candidate, case.request, self.context))
        self.assertFalse(result["status_correct"])
        self.assertFalse(result["scope_correct"])

    def test_clarification_topic_screen_is_separate_from_scope(self):
        case = self.suite.cases[12]
        candidate = oracle_policy(case, self.context)
        candidate["ambiguities"] = [dict(field="unspecified", question="Please clarify.", candidate_ids=[])]
        result = assess(case, validate_policy(candidate, case.request, self.context))
        self.assertTrue(result["scope_correct"])
        self.assertFalse(result["full_annotation_match"])
        self.assertEqual(result["clarification_topics_missing"], ["recipient"])

    def test_summary_requires_complete_unique_records(self):
        with self.assertRaises(ValueError):
            summarize([], self.suite)

    def test_saved_api_events_must_be_unique_complete_and_request_bound(self):
        records = [dict(id=c.id, category=c.category, request=c.request, outcome="accepted",
                        policy=oracle_policy(c, self.context)) for c in self.suite.cases]
        events = [dict(case_id=c.id, status="ok", requested_model="offline", wall_seconds=0,
                       messages=[dict(role="user", content=json.dumps(dict(original_request=c.request)))])
                  for c in self.suite.cases]
        self.assertEqual(validated_metrics(self.context, self.suite, records, events)["scope_correct"], 20)
        for corrupt in (
            lambda e: e.append(deepcopy(e[0])),
            lambda e: e.pop(),
            lambda e: e[0].update(case_id="unknown_case"),
            lambda e: e[0].update(status="pending"),
            lambda e: e[0].update(status="api_error"),
            lambda e: e[0]["messages"][-1].update(content=json.dumps(dict(original_request="Different"))),
        ):
            bad = deepcopy(events)
            corrupt(bad)
            with self.assertRaises(ValueError):
                validated_metrics(self.context, self.suite, records, bad)

    def test_full_evaluation_and_offline_report_use_sdk_without_network_or_gold_leakage(self):
        import openrouter_client as backend
        cases = {case.request: case for case in self.suite.cases}
        requests = []
        def handler(request):
            payload = json.loads(request.content)
            requests.append(payload)
            supplied = json.loads(payload["messages"][-1]["content"])
            self.assertEqual({k: v for k, v in supplied.items() if k != "original_request"}, self.context.model_dump())
            self.assertNotIn("required_reads", json.dumps(payload))
            case = cases[supplied["original_request"]]
            candidate = oracle_policy(case, self.context)
            return httpx.Response(200, json={
                "id": "offline", "object": "chat.completion", "created": 0, "model": "test/evaluator",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(candidate)},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            })
        client = OpenAI(base_url=backend.OPENROUTER_BASE_URL, api_key="offline-secret-key",
                        http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        with tempfile.TemporaryDirectory() as temp, client, patch.object(
                backend, "get_client", return_value=client), redirect_stdout(io.StringIO()):
            run = run_evaluation(FIXTURES / "context.json", FIXTURES / "cases.json", Path(temp), model="test/evaluator")
            metrics = json.loads((run / "metrics.json").read_text())
            self.assertEqual(metrics["scope_correct"], 20)
            self.assertEqual(metrics["full_annotation_match"], 20)
            self.assertEqual(metrics["api_calls"], 20)
            self.assertEqual(metrics["usage"]["total_tokens"], 3000)
            self.assertTrue(json.loads((run / "metadata.json").read_text())["sources_unchanged"])
            self.assertEqual(len(requests), 20)
            self.assertTrue(all(r["max_tokens"] == MAX_OUTPUT_TOKENS for r in requests))
            self.assertNotIn("offline-secret-key", (run / "api.jsonl").read_text())
            before = (run / "metrics.json").read_text()
            with patch.object(backend, "get_client", side_effect=AssertionError("No API on replay")):
                main(["--report-only", str(run)])
            self.assertEqual(before, (run / "metrics.json").read_text())
            records_path = run / "records.jsonl"
            original_records = records_path.read_text()
            records = [json.loads(line) for line in original_records.splitlines()]
            records[0]["policy"]["expiration"]["max_duration_seconds"] = 7200
            records_path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            with self.assertRaisesRegex(PolicyValidationError, "expiration"):
                main(["--report-only", str(run)])
            records_path.write_text(original_records)
            # An annotation change must be detected before offline rescoring.
            path = run / "cases.json"
            path.write_text(path.read_text() + " ")
            with self.assertRaisesRegex(ValueError, "changed"):
                main(["--report-only", str(run)])

    def test_transport_failures_have_no_sdk_retries_or_sensitive_exception_logs(self):
        import openrouter_client as backend
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(503, json={"error": {"message": "PRIVATE-ERROR-VALUE"}})
        client = OpenAI(base_url=backend.OPENROUTER_BASE_URL, api_key="offline-secret-key",
                        http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        with tempfile.TemporaryDirectory() as temp, client, patch.object(
                backend, "get_client", return_value=client), redirect_stdout(io.StringIO()):
            cases_path = Path(temp) / "two_cases.json"
            subset = self.suite.model_dump()
            subset["cases"] = subset["cases"][:2]
            cases_path.write_text(json.dumps(subset))
            run = run_evaluation(FIXTURES / "context.json", cases_path, Path(temp) / "runs", model="test/evaluator")
            metrics = json.loads((run / "metrics.json").read_text())
            self.assertEqual(len(requests), 2)
            self.assertEqual(metrics["outcomes"], {"api_error": 2})
            self.assertEqual(metrics["scope_correct"], 0)
            self.assertIn("2 fixed, synthetic requests", (run / "REPORT.md").read_text())
            for name in ("api.jsonl", "records.jsonl", "REPORT.md"):
                text = (run / name).read_text()
                self.assertNotIn("PRIVATE-ERROR-VALUE", text)
                self.assertNotIn("offline-secret-key", text)


if __name__ == "__main__":
    unittest.main()
